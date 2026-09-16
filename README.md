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

```bash
uv run python -m prompt_harbor --database gateway.db init
uv run python -m prompt_harbor --database gateway.db list
uv run python -m prompt_harbor --database gateway.db show 1
uv run python -m prompt_harbor --database gateway.db purge
```

数据保留两天；网关启动和 `purge` 都会清理过期调用。`show` 展示完整 body、脱除认证字段的 headers、状态、耗时、错误与 usage。SSE 按 chunk 立即转发并 flush。

body 默认最多保存 10 MiB，可用 `PROMPT_HARBOR_MAX_BODY` 调整；超过限制时保留截断内容并设置截断标记。`prompt_harbor.py` 提供命令行入口。

## 测试

```bash
uv run pytest -q
```

前端页面位于 `ui/index.html`，通过 `/api/overview` 获取调用与会话数据。
测试只使用本地 upstream fixture，不访问真实 OpenAI。

代码按配置、数据库、代理、headers、usage、保留策略和 CLI 分模块组织在 `prompt_harbor/` 中。
