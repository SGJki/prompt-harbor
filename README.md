# Agent API Observer

观察 coding agent 与上游大模型 API 之间的完整 request/response。

## MVP 决策

第一阶段选择 **Codex CLI**。原因是它的上游请求可以通过 OpenAI-compatible base URL 指向本地代理；代理不需要修改 Codex 源码，也能先验证“捕获、检索、脱敏、回放”这条核心链路。

## 目标体验

启动 observer 后，Codex 仍按原来的方式运行：

```bash
agent-observer start --upstream https://api.openai.com/v1
OPENAI_BASE_URL=http://127.0.0.1:8787/v1 codex
```

每次请求都会产生一条事件，包含：时间、会话、模型、请求 token（如果上游提供）、延迟、状态码、错误，以及可选的完整 request/response body。敏感字段（API key、authorization、cookie）默认脱敏。

## 架构

```text
Codex CLI
   │ HTTP(S), base URL 指向 localhost
   ▼
Observer Proxy ──▶ OpenAI API
   │
   ├── SQLite：事件索引、状态、耗时、token、错误
   ├── Blob store：原始 JSON body（按事件 ID 存储）
   └── Web UI / CLI：实时流、详情、搜索、导出、回放
```

代理层与 agent 适配层分开：核心代理负责转发和记录，适配层只负责 base URL、认证头、流式协议和 provider-specific 字段。这样后续可以增加 Claude Code（Anthropic Messages/SSE）和 pi，而不改变存储模型。

## 第一版范围

1. OpenAI Chat Completions/Responses 的非流式和 SSE 流式转发。
2. 事件落盘：request headers（脱敏）、request body、response headers（脱敏）、response body、时间线和错误。
3. 端口、上游地址、数据目录、body 是否持久化均可配置。
4. `list`、`show <event-id>`、`export` 三个 CLI 命令。
5. 默认只监听 `127.0.0.1`，并提供一键清理数据命令。

## 必须先解决的工程问题

- **流式响应**：不能等待完整响应后再返回，否则会改变 agent 的交互行为；需要边读边写，同时复制字节到记录器。
- **重试**：记录每一次实际上游请求，并用 parent/request-attempt 关联，避免把重试误认为一次调用。
- **敏感信息**：日志中永不保存 API key；请求 body 中的常见 secret 字段提供递归脱敏，原始内容持久化需要显式开启。
- **大小限制**：单事件和总磁盘配额都要有限制，超限时保留摘要和截断标记。
- **协议兼容**：先覆盖 Codex 实际使用的 endpoint，再用录制的 fixture 做回归测试。

## 后续适配

- **Claude Code**：增加 Anthropic `/v1/messages` 和 SSE event parser，使用 `ANTHROPIC_BASE_URL` 指向代理。
- **pi**：根据其 provider 配置增加对应 upstream adapter。
- 多 agent 会话关联、OpenTelemetry 导出、可选本地搜索和 web UI。

## 建议的实现顺序

1. 先做一个可运行的 HTTP reverse proxy 和 SQLite schema。
2. 用 curl fixture 验证 JSON 与 SSE 透明转发。
3. 接入 Codex，验证真实请求、重试、取消和错误场景。
4. 再补 CLI 查询和最小 web UI。

## 使用 uv 运行

```bash
uv run agent_gateway.py start
OPENAI_BASE_URL=http://127.0.0.1:8787/v1 uv run agent_gateway.py start
```

常用命令：

```bash
uv run python agent_gateway.py list
uv run python agent_gateway.py show 1
uv run python agent_gateway.py purge
```
