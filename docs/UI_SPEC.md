# Prompt Harbor UI 完成规格

## 目标
提供参考 session-share 的本地审计台，真实连接 SQLite 数据并实时刷新。

## 必须交付
- `ui/` 目录（`index.html`、`css/app.css`、`js/*.js` 原生 ES 模块，无构建步骤），包含 Overview、Calls、Sessions 视图、筛选栏与移动端布局。
- 后端托管 `ui/` 内 `.html/.css/.js` 静态资源（白名单扩展名 + normpath 防路径穿越）。
- 后端提供 `GET /api/overview`、`GET /api/calls`、`GET /api/sessions`、`GET /api/calls/{id}`（含 usage 与 request/response 截断标记）。
- 后端提供 SSE `GET /api/events`；每次新 call 完成或新 session 写入后发送 `invalidate` 事件。
- 前端收到 `invalidate` 后按当前视图重新请求数据；轮询仅作为断线兜底。
- Calls 支持状态和 session 筛选，点击行查看请求、响应、headers、usage 和错误。
- 授权头不得出现在 API 返回或页面内容中。

## 验收
1. `uv run pytest -q` 通过（环境禁止 loopback 时记录限制）。
2. 使用临时 SQLite 插入 call 后，已连接 SSE 客户端能收到 `invalidate`。
3. 浏览器请求上述 API 返回合法 JSON，详情可展开。
4. 静态资源返回正确 Content-Type；`..`、URL 编码穿越与未知扩展名一律 404（见 `tests/test_ui_static.py`）。
