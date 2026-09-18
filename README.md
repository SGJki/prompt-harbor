# PromptHarbor

本地透明转发 Codex CLI 的 OpenAI API 请求，并将请求、响应、SSE、耗时、状态和 token 记录到 SQLite。Authorization 仅透传到上游，永不写入数据库或 CLI 输出。

## 安装与启动

```bash
uv sync
uv tool install -e .
prompt-harbor --database gateway.db start
OPENAI_BASE_URL=http://127.0.0.1:8787/v1 codex
```

默认监听 `127.0.0.1:8787`，上游为 `https://api.openai.com`。完整的配置文件和覆盖规则见下节。

## 配置文件

网关默认查找当前目录的 `prompt-harbor.ini`；也可以通过 `--config` 或 `PROMPT_HARBOR_CONFIG` 指定路径。示例配置见 `prompt-harbor.ini.example`。配置优先级为：CLI 参数 > 环境变量 > INI 文件 > 内置默认值。未创建配置文件时仍使用以下默认值：

```ini
[gateway]
database = gateway.db
listen = 127.0.0.1:8787
upstream = https://api.openai.com
max_body = 10485760
retention_days = 2
db_timeout = 30
upstream_timeout = 600
sidecar_timeout = 10
sidecar_start_timeout = 5
sidecar_stop_timeout = 2
api_call_limit = 200
cli_call_limit = 50
sse_keepalive = 15
purge_interval = 86400

[sidecar]
# url = http://127.0.0.1:8790
# command = node sidecar/server.mjs
# token = local-token
```

例如：`prompt-harbor --config ./prompt-harbor.ini start`。`max_body` 控制保存到 SQLite 的 request/response 前缀大小；认证 token 等敏感值不要提交到版本库。

## pi-ai sidecar

网关可以通过可选的 Node sidecar 提供 `pi-messages` 语义端点。现有 `/v1/*` 透明路径不依赖 Node。使用已经运行的 sidecar：

```bash
prompt-harbor --pi-sidecar-url http://127.0.0.1:8790 start
```

也可以让网关管理 sidecar 子进程：

```bash
PI_AI_SIDECAR_FIXTURE=1 prompt-harbor \
  --pi-sidecar-command 'node sidecar/server.mjs' start
```

真实 provider 运行前需要在 `sidecar/` 安装依赖，并确保 pi-ai 已构建。上游 provider 的 API key 由 pi-ai 环境变量或 credential store 管理。`PROMPT_HARBOR_MESSAGES_TOKEN` 可为 `/messages`、`/models` 和 sidecar 之间增加本地共享 token；客户端 Authorization 不会作为 provider 凭证转发，也不会写入 SQLite。

sidecar 提供 `GET /health`、`GET /models` 和 `POST /messages`。模型 ID 使用带 provider 前缀的形式，例如 `anthropic/claude-sonnet-4-5`。网关没有配置 sidecar 时，`/messages` 返回 503，OpenAI 透明代理仍可使用。

## 查询

安装为本地命令后直接调用 `prompt-harbor`，无需再加 `uv run`：

```bash
prompt-harbor --database gateway.db init
prompt-harbor --database gateway.db list
prompt-harbor --database gateway.db show 1
prompt-harbor --database gateway.db purge
```

数据保留两天；网关启动、显式 `purge` 和后台周期任务都会清理过期调用。`purge_interval`（默认 86400 秒）或 `PROMPT_HARBOR_PURGE_INTERVAL` 可调整周期。`show` 展示完整 body、脱除认证字段的 headers、状态、耗时、错误与 usage。SSE 按 chunk 立即转发并 flush。

body 默认最多保存 10 MiB，可用 `PROMPT_HARBOR_MAX_BODY` 调整；超过限制时保留截断内容并设置截断标记。源码目录内也可以用 `python -m prompt_harbor`（或根目录的 `prompt_harbor.py`）运行同样的命令。

## 网页审计台

网关启动后，浏览器打开 `http://127.0.0.1:8787/` 即可使用内置审计台。前端位于 `ui/` 目录（`index.html` + `css/app.css` + `js/` 原生 ES 模块，无构建步骤），由网关托管目录内的 `.html/.css/.js` 静态资源；`PROMPT_HARBOR_UI` 仍可替换入口 HTML。页面提供与 CLI 相同的查询能力：

- **Overview / Calls / Sessions** 切换视图，对应 `GET /api/overview`、`/api/calls`、`/api/sessions`；Sessions 显示每个会话的调用数，并可一键跳到该会话的调用列表；
- **Calls** 支持按状态（成功/失败/进行中）、按会话和 model/endpoint 关键字筛选；
- 点击调用查看详情（`GET /api/calls/{id}`）：请求/响应 body（JSON 自动美化、SSE 逐条展开）、脱除认证字段的 headers、usage、错误与截断标记；
- 数据通过 SSE（`/api/events`）自动刷新，断线自动重连并以低频轮询兜底；网关不可达时页面显示错误横幅并可重试，**↻ Refresh** 可随时强制刷新。

## 测试

```bash
uv run pytest -q
```

测试只使用本地 upstream fixture，不访问真实 OpenAI。

代码按配置、数据库、代理、headers、usage、保留策略和 CLI 分模块组织在 `prompt_harbor/` 中。
