# PromptHarbor 技术方案

## 1. 目标

构建一个运行在本机的透明网关，观察 Codex CLI 发往 OpenAI API 的完整 request，以及 OpenAI 返回给 Codex 的完整 response。网关应尽量不改变 Codex 的行为，尤其是流式输出的时序和内容。

## 2. 当前范围（2026-09-19）

### 支持

- 目标客户端：Codex CLI
- 上游协议：OpenAI API
- OpenAI Responses API，以及 Codex 实际使用到的 OpenAI API endpoint
- JSON 请求/响应
- SSE 流式响应
- 本机单用户运行
- SQLite 本地存储
- 在保存上限内保存 prompt、response、请求/响应头（认证信息除外），超限时保留前缀并设置截断标记
- 请求头中的 `Authorization` 透传到上游，但不写入数据库或日志
- `upstream` 必须是 HTTPS；仅 loopback HTTP 可用于本地 fixture，并在 CLI/UI 告警
- 网关不跟随上游 HTTP 重定向，避免 Authorization 被转发到重定向目标
- SQLite 数据库初始化为 0600；API key 在转发期间仍存在于网关进程内存中，宿主机进程安全不由网关解决
- 数据按时间保留 2 天；启动、显式 `purge` 和后台周期任务会清理过期数据
- CLI：启动、查看调用列表、查看详情、清理数据
- 可选 pi-ai sidecar：`/messages`、`/models`、`/health`
- 内置无构建步骤的 Web 审计台：Overview、Calls、Runtime Sessions、Client Sessions 和实时失效通知

### 不支持

- Claude Code 的 Anthropic Messages/SSE 适配
- 多用户、远程部署、Docker 部署
- 请求修改
- 自动重试
- 模型切换
- 路由和负载均衡
- 不对 prompt 内容做脱敏；完整内容只保存在本机 SQLite

## 3. 使用方式

网关默认监听本机地址：

```text
http://127.0.0.1:8787
```

启动 Codex 时将 OpenAI base URL 指向网关：

```bash
OPENAI_BASE_URL=http://127.0.0.1:8787/v1 codex
```

网关收到 `POST /v1/...` 后，使用配置的上游地址保留原路径转发，例如：

```text
http://127.0.0.1:8787/v1/responses
        ↓
https://api.openai.com/v1/responses
```

客户端的 `Authorization` 只在内存中透传给上游，不由网关读取、持久化或打印。

## 4. 核心行为

1. 接收 Codex 请求并创建 `call`、`attempt` 和 `payload` 记录。
2. 记录请求到达时间、HTTP 方法、路径、请求头（排除认证信息）、请求 body 和流式标记。
3. 将请求转发到 OpenAI。
4. 对普通响应直接转发；对 SSE 响应边读取边写回客户端，同时复制响应内容用于记录。
5. 记录状态码、响应头（排除认证信息）、受保存上限约束的 response body、首字节时间、完成时间和错误信息；超限时设置截断标记。
6. 上游连接中断、客户端取消、网关异常都要留下可查询的失败事件。
7. 每次启动、显式执行 `purge` 和后台周期任务都会删除超过 2 天的调用及其关联记录；周期由 `purge_interval` / `PROMPT_HARBOR_PURGE_INTERVAL` 配置，默认 86400 秒。

## 5. 数据模型

SQLite 使用运行时/客户端会话和调用链表，关联由应用层维护，不创建外键约束：

- `runtime_sessions`：一次网关进程运行及工作目录元数据；每个 call 关联当前 runtime session。
- `client_sessions`：由 inbound `session-id` 或支持的 metadata session 得到的逻辑客户端会话，可跨连接和网关重启；无可靠身份时记录独立 `unresolved/ephemeral` 行。
- `calls`：客户端请求的状态、provider/API family、endpoint、model、流式标记、状态码、耗时和错误。
- `attempts`：每个调用的上游尝试、脱敏 headers、上游 URL、状态和字节统计。
- `payloads`：请求/最终响应 snapshot、content type，以及 `response_complete`、`response_truncated`、`capture_degraded` 等独立事实。
- `usage`：输入/输出/总 token 及原始 usage JSON。

body 直接存 SQLite。请求和响应默认各保存最多 10 MiB，超出部分只保留前缀并设置截断标记；进程级 `capture_budget` 限制并发 response snapshot 的内存占用，预算耗尽时转发继续但设置 `capture_degraded`。

## 6. CLI

建议命令：

```text
uv run python -m prompt_harbor start [--listen 127.0.0.1:8787] [--upstream https://api.openai.com]
uv run python -m prompt_harbor list
uv run python -m prompt_harbor show <call-id>
uv run python -m prompt_harbor purge
```

`list` 显示时间、路径、模型、状态、耗时、输入/输出大小和 call ID。`show` 显示完整请求与响应内容，并明确标识流式响应和错误。

运行时配置支持当前目录的 `prompt-harbor.ini`，也可通过 `--config` 或 `PROMPT_HARBOR_CONFIG` 指定文件。覆盖优先级为 CLI 参数、环境变量、INI 文件、内置默认值。配置文件使用 `[gateway]` 和 `[sidecar]` 两个 section；默认值和可配置项见 `prompt-harbor.ini.example`。

## 7. 性能与正确性要求

- SSE chunk 转发不等待完整响应。
- 网关自身处理不主动缓冲完整 SSE 响应后再发送。
- 记录操作不得阻塞转发；必要时使用内存缓冲和独立写入队列。
- 保留上游状态码、关键响应头和响应字节内容。
- 客户端取消时及时关闭上游连接并完成失败事件记录。
- 用 OpenAI JSON 和 SSE fixtures 覆盖透明转发、错误、取消和过期清理。

## 8. 安全与限制

- 只绑定 `127.0.0.1`；`--listen` 在绑定前拒绝其他 host 和非法端口。
- 不持久化 API key，不在 CLI 输出 API key。
- 不做 prompt 内容脱敏；完整内容只保存在本机 SQLite，按 2 天策略清理。
- 默认监听 loopback，配置校验不会允许局域网或公网地址。

## 9. UI

已提供独立无依赖页面 `ui/index.html`，由网关托管。页面通过 `GET /api/overview`、`/api/calls`、`/api/runtime-sessions`、`/api/client-sessions` 和 `GET /api/calls/{id}` 查询数据，并通过 `GET /api/events` 接收 `invalidate` 事件；旧的 `/api/sessions` 路径返回 404，完整契约和验收项见 `docs/UI_SPEC.md`。


## 10. 主键与关联约定

所有表使用 SQLite 自增整数主键：`id INTEGER PRIMARY KEY AUTOINCREMENT`。

表之间不创建 SQLite 外键约束。`runtime_session_id`、`client_session_row_id`、`call_id`、`attempt_id` 只由业务代码维护；写入、查询和级联删除均由应用层负责。旧的 `/api/sessions` 资源不再提供。

## 11. Runtime Session 与 Client Session 期间的完整链路

```mermaid
sequenceDiagram
    participant U as 用户
    participant C as Codex CLI
    participant G as Gateway
    participant D as SQLite
    participant O as OpenAI API
    U->>C: 输入任务
    C->>G: POST /v1/responses
    G->>D: 创建 call 和 attempt
    G->>O: 透传 request
    O-->>G: JSON 或 SSE response
    G-->>C: 立即转发 response/chunk
    G->>D: 保存完整 request/response 与状态
    C-->>U: 展示模型输出
    U->>C: 继续操作
    C->>G: 下一次 request
    G->>D: 在 runtime session 中创建新的 call，并按 client session 分组
```

## 12. 数据表 ER 图（逻辑关联）

图中的关联是业务逻辑关系，不是 SQLite 外键约束。

```mermaid
erDiagram
    RUNTIME_SESSIONS ||--o{ CALLS : "runtime_session_id"
    CLIENT_SESSIONS ||--o{ CALLS : "client_session_row_id"
    CALLS ||--|{ ATTEMPTS : "call_id"
    ATTEMPTS ||--|| PAYLOADS : "attempt_id"
    ATTEMPTS ||--o| USAGE : "attempt_id"
    ATTEMPTS ||--o{ STREAM_CHUNKS : "attempt_id"
    RUNTIME_SESSIONS { integer id PK }
    CLIENT_SESSIONS { integer id PK text client_session_id }
    CALLS { integer id PK integer runtime_session_id integer client_session_row_id text thread_id text request_correlation_id }
    ATTEMPTS { integer id PK integer call_id }
    PAYLOADS { integer id PK integer attempt_id }
    USAGE { integer id PK integer attempt_id }
    STREAM_CHUNKS { integer id PK integer attempt_id }
```

当前启用 `runtime_sessions`、`client_sessions`、`calls`、`attempts`、`payloads` 和 `usage`；`stream_chunks` 作为后续扩展，默认不逐 chunk 持久化。`session-id` 是 client session 的主要身份来源；`thread-id`、request correlation ID 和 provider session context 保持独立。

## 13. 当前实现约定

实现代码位于 `prompt_harbor/`，按配置、数据库、代理、headers、usage、保留策略和 CLI 分模块组织；`prompt_harbor.py` 提供命令行入口。推荐运行方式为 `uv run python -m prompt_harbor ...`。

请求和响应默认最多各保存 10 MiB，可通过 `PROMPT_HARBOR_MAX_BODY` 调整；进程级 response snapshot 预算可通过 `capture_budget` / `PROMPT_HARBOR_CAPTURE_BUDGET` 调整。超过单调用限制时只保存前缀并设置 `request_truncated` 或 `response_truncated`；预算不足时转发仍继续并设置 `capture_degraded`。`response_complete` 表示上游传输是否完整，和 HTTP 状态码、snapshot 是否降级无关。
