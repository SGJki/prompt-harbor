# PromptHarbor

本地透明转发 Codex CLI 的 OpenAI API 请求，并将请求、响应、SSE、耗时、状态和 token 记录到 SQLite。Authorization 仅透传到上游，永不写入数据库或 CLI 输出。

## 安装与启动

```bash
uv sync
uv tool install -e .
prompt-harbor --database gateway.db start
OPENAI_BASE_URL=http://127.0.0.1:8787/v1 codex
```

配置优先级为 CLI 参数、环境变量（`PROMPT_HARBOR_DB`、`PROMPT_HARBOR_LISTEN`、`PROMPT_HARBOR_UPSTREAM`）、默认值。默认监听 `127.0.0.1:8787`，上游为 `https://api.openai.com`。

## 查询

安装为本地命令后直接调用 `prompt-harbor`，无需再加 `uv run`：

```bash
prompt-harbor --database gateway.db init
prompt-harbor --database gateway.db list
prompt-harbor --database gateway.db show 1
prompt-harbor --database gateway.db purge
```

数据保留两天；网关启动和 `purge` 都会清理过期调用。`show` 展示完整 body、脱除认证字段的 headers、状态、耗时、错误与 usage。SSE 按 chunk 立即转发并 flush。

body 默认最多保存 10 MiB，可用 `PROMPT_HARBOR_MAX_BODY` 调整；超过限制时保留截断内容并设置截断标记。源码目录内也可以用 `python -m prompt_harbor`（或根目录的 `prompt_harbor.py`）运行同样的命令。

## 网页审计台

网关启动后，浏览器打开 `http://127.0.0.1:8787/` 即可使用内置审计页面（源文件为 `ui/index.html`，可用 `PROMPT_HARBOR_UI` 指定其他路径）。页面提供与 CLI 相同的查询能力：

- **Overview / Calls / Sessions** 按钮切换视图，分别对应 `GET /api/overview`、`/api/calls`、`/api/sessions`，展示调用列表、状态、耗时、成功率与会话信息；
- **↻ Refresh** 按钮手动重新查询，页面还会通过 SSE（`/api/events`）在新调用完成时自动刷新，并以 5 秒轮询作为断线兜底；
- 点击 Calls 中的记录可查看单次调用详情（`GET /api/calls/{id}`），返回的 headers 已脱除认证字段。

## 测试

```bash
uv run pytest -q
```

测试只使用本地 upstream fixture，不访问真实 OpenAI。

代码按配置、数据库、代理、headers、usage、保留策略和 CLI 分模块组织在 `prompt_harbor/` 中。
