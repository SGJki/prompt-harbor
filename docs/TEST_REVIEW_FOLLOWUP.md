# Test review follow-up

2026-09-18 iteration-4 复验：6 项均已关闭。新增 15 个 Python 回归用例（收集总数 92）和 1 个真实 Chromium Playwright 用例；所有网关生命周期测试在 loopback-enabled 环境通过。

| 项目 | 状态 | 写位置/证据 | 影响 |
|---|---|---|---|
| 周期清理缺失 | 已关闭 | `tests/test_iteration3.py::test_periodic_purge_cascades_and_server_stays_live`；`GatewayHTTPServer.server_close()` 停止后台线程；`PROMPT_HARBOR_PURGE_INTERVAL` 可注入 | 运行中过期链会被级联删除，近期链和服务保留 |
| 非本机监听未限制 | 已关闭 | `tests/test_iteration3.py::test_listener_rejects_non_loopback_before_bind`、`test_listener_accepts_default_and_explicit_loopback`；`validate_listen` 在绑定前拒绝 host | 仅允许 `127.0.0.1:PORT` |
| 非法 `PROMPT_HARBOR_MAX_BODY` | 已关闭 | `tests/test_iteration3.py::test_request_level_body_limit_error_is_json_and_terminal`（0、负值、非法字符串）；`test_sidecar_configuration_error_persists_only_scrubbed_body` 验证 `/messages` 错误路径不持久化凭据 | 单请求错误不再断开线程，后续请求继续服务 |
| 重复模块实现 | 已关闭 | `tests/test_iteration3.py::test_all_public_entrypoints_share_canonical_behavior`；server/proxy/database/retention/usage 均转向 canonical core/database 行为 | 并行入口行为一致 |
| SSE 规格差异 | 已关闭 | `tests/test_iteration3.py::test_sse_notifies_direct_sql_calls_and_sessions`、`test_notify_does_not_advance_cursor_past_concurrent_change`、`test_purge_preserves_change_log_after_slowest_active_cursor`；SQLite 触发器覆盖 sessions 全部可变字段并兼容旧库迁移，在线客户端按最慢游标保留日志；真实 POST 既有测试继续通过 | ready、calls、sessions 事件逐帧发送且连接保持 |
| 前端逻辑零覆盖 | 已关闭 | `browser-tests/audit.spec.js` 真实 Chromium：Overview/Calls/Sessions、筛选、详情、session 跳转、刷新、SSE 刷新、失败重试、Authorization 不泄露 | UI 交互有真实浏览器回归 |

实际验收命令：

- `uv run pytest --collect-only -q` → **92 tests collected**。
- `uv run pytest -q`（loopback-enabled）→ **92 passed, 0 skipped, 0 xfailed**。
- `npm ci && npm run test:browser` → **1 passed**；未设置环境变量时使用已安装的 Playwright 默认 Chromium。
