# pi-ai 适配技术方案

状态：Phase 0-2 已实现（截至 2026-09-18）；Phase 3/4 仍是后续工作。

现状证据：`/messages`、`/models`、`/health`、可选托管 sidecar、生命周期持久化和 fake sidecar 回归测试均已存在；真实 provider smoke、完整 Node 单元测试、迁移和更高层语义能力仍待补齐。

日期：2026-09-18

## 1. 背景与目标

PromptHarbor 当前是 Python 标准库实现的 OpenAI 透明代理。它接收 Codex 的 `/v1/*` 请求，原样转发到上游，并把 session、call、attempt、payload 和 usage 写入 SQLite。

pi-ai 提供统一的模型、上下文、工具、流式事件和 provider 抽象，适合后续的模型切换、按规则路由、Anthropic/Google 适配和 pi 客户端接入。由于 pi-ai 是 Node.js/TypeScript 项目，而 PromptHarbor 必须保持 Python 标准库、单进程网关设计，采用可选 Node sidecar，不把 pi-ai 引入 Python 运行时。

本方案首期增加一个 `pi-messages` 语义端点：

- 保持现有 `/v1/*` 透明转发行为完全不变；
- 在网关中记录 pi-messages 请求、原始 SSE、终止状态和统一 usage；
- 由 Node sidecar 使用 pi-ai 将请求路由到已配置的 provider；
- sidecar 不可用时，现有 OpenAI 代理仍可独立工作；
- 首期只做 `pi-messages`，不把 OpenAI Responses 转成 pi-ai Context。

## 2. 明确不做的事情

- 不在 Python 中实现 Anthropic、Google、Bedrock 等协议转换；
- 不修改 `/v1/*` 的请求或响应 body；
- 不在网关层自动重试、切换模型或负载均衡；
- 不把客户端的 Authorization 当作上游 provider 凭证；
- 不首期启用逐 token 的 SQLite `stream_chunks` 持久化；
- 不在网关启动时自动执行 npm build；
- 不要求没有 Node.js 的环境安装或运行 pi-ai。

## 3. 组件与进程

```text
Codex / pi client
       │
       ├── POST /v1/*       ──> PromptHarbor ──> OpenAI（现有透明路径）
       │
       └── POST /messages   ──> PromptHarbor ──> pi-ai sidecar
                                                    ├─ Anthropic
                                                    ├─ OpenAI
                                                    ├─ Google
                                                    ├─ OpenRouter
                                                    └─ 其他 pi-ai provider
```

PromptHarbor 只负责本地 HTTP 接入、审计记录、SSE 字节转发和协议终止状态判断。sidecar 负责模型目录、provider 认证、Context 到具体 API 的转换以及具体 provider 的流式事件生成。

sidecar 监听 `127.0.0.1` 的随机端口。PromptHarbor 启动它时读取明确的 READY 握手，随后通过 HTTP 请求访问；每次请求前不重新创建 Node 进程。也允许通过配置指定一个外部 sidecar URL，方便单独调试和部署。

## 4. pi-messages HTTP 契约

### 4.1 `POST /messages`

请求体遵循 pi-ai 的现有格式：

```json
{
  "model": "anthropic/claude-sonnet-4-5",
  "context": {"messages": []},
  "options": {
    "temperature": 0.2,
    "maxTokens": 4096,
    "reasoning": "medium",
    "toolChoice": "auto",
    "sessionId": "optional-session-id"
  }
}
```

`context` 和 `options` 透传给 sidecar，但 Python 不信任其中的 provider 认证字段。请求体大小受 `PROMPT_HARBOR_MAX_BODY` 限制，JSON 无法解析时返回 400。

### 4.2 模型命名

pi-messages 线上请求只有一个 `model` 字符串，而多 provider 路由需要 provider 和 model 两个维度。因此 sidecar 使用全局唯一、带 provider 前缀的模型 ID，例如 `openai/gpt-5-mini`。sidecar 内部维护：

```text
client model id -> { provider: "openai", id: "gpt-5-mini" }
```

未注册、歧义或未授权的模型直接返回结构化错误，不进行猜测路由。

### 4.3 SSE 响应

响应使用 `Content-Type: text/event-stream`，每个事件是一个 `data: <JSON>\n\n` 块。事件类型遵循 pi-ai 的 `PiMessagesEvent`：

```text
start
text_start / text_delta / text_end
thinking_start / thinking_delta / thinking_end
toolcall_start / toolcall_delta / toolcall_end
done
error
```

`done` 和 `error` 必须是终止事件，分别包含完整 usage 或错误信息。PromptHarbor 原样转发 SSE 字节并立即 flush；它只对事件边界做最小解析，以便判断终止状态和提取 usage。

`GET /health` 返回 sidecar 是否已就绪。`GET /models` 返回不含秘密的模型目录，至少包括 `id`、`provider`、`name`、`api`、`contextWindow`、`input`、`reasoning` 和 `cost`。

## 5. 认证与安全边界

上游认证由 sidecar 的 pi-ai provider 负责，优先级遵循 pi-ai 的环境变量、credential store 和 OAuth 机制。客户端发给 PromptHarbor 的 Authorization 只用于可选的本地 sidecar 访问控制，不直接传给 Anthropic/OpenAI。

已实现可选配置 `PROMPT_HARBOR_MESSAGES_TOKEN`：配置后 `/messages` 和 `/models` 使用该本地 token；未配置时依赖 loopback 绑定。比较使用常量时间比较。请求和响应 headers 继续通过现有 `sanitize()` 处理，Authorization、Cookie、Host 等敏感字段不落库、不打印。对应回归覆盖见 `tests/test_pi_messages.py`。

sidecar 的上游 credential store 与 PromptHarbor 的 SQLite 分离。PromptHarbor 不读取或复制 provider 的 API key，也不负责 OAuth 刷新。

## 6. 持久化模型与状态

继续沿用现有生命周期：

```text
session → call → attempt → payload / usage
```

`/messages` 请求创建记录时：

- `calls.endpoint = /messages`；
- `calls.api_family = pi-messages`；
- `calls.provider` 使用模型注册表解析出的实际 provider；
- `calls.model` 使用规范化模型 ID；
- `attempts.upstream_url` 写入 sidecar 的 `/messages` URL；
- request body 和脱敏 headers 先落库，再开始转发。

响应结束时：

- 收到 `done`：HTTP 成功且应用状态为 `succeeded`；
- 收到 `error`：即使 HTTP 是 200，应用状态也为 `failed`；
- HTTP 非 2xx 且没有事件流：记录 `sidecar_http` 错误并保留错误 body；
- EOF 前没有 `done`/`error`：记录 `upstream_incomplete`；
- 客户端断开：停止向客户端写入，关闭 sidecar 响应，并记录 `client_cancel`。

现有 `usage` 表的映射为：

```text
usage.input       -> input_tokens
usage.output      -> output_tokens
usage.totalTokens -> total_tokens
```

完整 usage（包括 `cacheRead`、`cacheWrite` 和 `cost`）保存在 `raw_usage_json`。如果 UI 后续需要按缓存 token 或成本查询，再增加专用列。

首期仍把受限的原始 SSE 累积到 `payloads.response_body`，并在结束时写入；为降低进程崩溃时的损失，可按 64 KiB 或 250 ms 做节流式 checkpoint。禁止每个 token 单独提交 SQLite 事务。真正的紧凑 frame 持久化留作后续工作，因为线上 `PiMessagesEvent` 不带 `AssistantMessageEvent` 所需的 `partial`，不能直接使用 pi-ai 的 `AssistantMessageFrameEncoder`。

## 7. Python 网关改造

现有 `Handler.do_POST()` 将 OpenAI 代理和记录逻辑放在一起，并固定写入 `provider=openai`。实施时先抽出一个参数化的内部代理流程：

```text
proxy_request(target_url, api_family, provider, route_kind)
```

其中透明 `/v1/*` 和语义 `/messages` 只提供不同的目标 URL、元数据和终止判断。这样可以避免复制一套生命周期代码，也保证两个路径都遵守：

- 只监听 localhost；
- Authorization 不存储；
- SSE chunk 立即转发；
- response body 有统一大小上限；
- attempt 完成后再写 usage 和状态。

`/messages` 应是精确路由。`/v1/*` 的原有路径拼接、状态码和 header 行为不变。sidecar 请求只发送必要的 `Content-Type`、`Accept`、本地认证和追踪 header，不盲目转发客户端所有 header。

## 8. sidecar 实现与生命周期

sidecar 放在 `sidecar/`，拥有独立 `package.json` 和 lockfile。开发环境可使用到 `../pi/packages/ai` 的 `file:` 依赖；部署环境使用固定版本或构建产物，不在 PromptHarbor 启动时编译。

启动前检查：Node 版本至少为 22.19、pi-ai `dist` 存在、依赖可导入。Python 启动子进程后等待：

```text
READY 127.0.0.1:<port>
```

超时则 sidecar 标记为不可用，PromptHarbor 仍继续提供 `/v1/*`。请求期间 sidecar 崩溃时，`/messages` 快速返回 503 并完成失败记录。PromptHarbor 退出时回收由自己启动的子进程。

`builtinModels()` 适合验证阶段，但会加载所有 provider。正式实现应优先注册实际启用的 provider，减少启动时间和依赖体积；模型目录刷新与 credential 检查不应阻塞每个请求。

## 9. 数据库迁移

当前 schema 只有建表逻辑，没有版本迁移。若实现需要新增列或表，必须先引入 `PRAGMA user_version` 迁移步骤，至少包含：

```text
v1：当前 schema
v2：pi-messages 元数据与 usage 扩展（如实际需要）
```

迁移必须对已有数据库幂等执行，不能依赖重新创建数据库。`purge` 需要同时清理新增的子表或字段关联。

## 10. 测试与验收

Python 侧继续使用 `uv run pytest -q`。新增本地 fake sidecar，不访问真实 provider，覆盖：

1. `/messages` JSON 请求被完整转发，`/v1/*` 行为无回归；
2. 第一个 SSE chunk 在终止事件前到达客户端；
3. text、thinking、tool call 事件交错时原始字节保持不变；
4. `done` usage 正确映射，camelCase 字段完整保存在 raw JSON；
5. HTTP 200 + `error` 事件被记录为失败；
6. 非 2xx、无终止事件、sidecar 不可用和客户端取消；
7. 模型 ID 映射、未知模型和本地 token 校验；
8. Authorization 不出现在 headers、body 记录或错误信息中；
9. 请求/响应大小限制和截断标记；
10. 旧数据库迁移、purge 和 UI detail API。

sidecar 侧增加 Node 单元测试，验证模型注册表、事件顺序、provider auth 错误和 SSE 编码。至少用一个真实配置 provider 做一次手工 smoke test，但不把真实凭证或网络调用放进回归测试。

## 11. 分阶段交付

### Phase 0：契约和 fixture

冻结 `/messages`、`/models`、`/health`、模型 ID、错误 JSON 和终止状态；完成 fake sidecar 与 SSE fixture。

### Phase 1：可选 sidecar

实现 Node HTTP server、模型注册表、health/models、一个 provider 和环境变量认证。完成独立运行与 Python 之外的测试。

### Phase 2：Python 代理与持久化

抽取通用生命周期流程，增加 `/messages` 路由、SSE 立即转发、usage 映射、状态判断、sidecar 健康失败处理和 schema migration。

### Phase 3：真实 provider 验证

先接一个 OpenAI 或 Anthropic provider，再扩展其他 provider。验证工具调用、thinking、取消、OAuth 和 UI 展示。

### Phase 4：后续语义能力

在已有 `pi-messages` 记录稳定后，再考虑规则路由、模型切换、紧凑 frame、成本统计以及 OpenAI Responses 到统一 Context 的转换。

## 12. 完成标准

- 没有 Node.js 或 sidecar 时，现有 `/v1/*` 回归测试和行为不受影响；
- `/messages` 能用 fake sidecar 完成首字节即时转发和完整审计；
- 正常、错误、不完整和取消流都能在 call/attempt/payload/usage 中保持一致；
- 认证信息不会出现在数据库、CLI、UI 或错误响应中；
- 旧数据库可自动迁移，`uv run pytest -q` 通过；
- sidecar 进程可以独立启动、健康检查、停止和替换，不要求 Python 引入第三方依赖。
