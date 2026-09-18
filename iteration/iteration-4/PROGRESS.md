# iteration-4

状态：closed（2026-09-18）

目标：修复 sessions `last_seen_at` SSE 通知、change-log 并发游标丢事件窗口，以及 change-log 无限增长；补齐回归测试并完成全套验收。

范围：保持现有 SQLite、标准库 HTTP/SSE 和单进程网关设计，确保在线客户端消费边界优先于日志清理。

完成：sessions 更新触发器覆盖全部可变字段并兼容旧库迁移；通知线程不再无条件推进 SSE 游标；purge 按在线客户端最小游标或 retention 清理 change_log；新增 3 项回归测试。

验证：`uv run pytest --collect-only -q` 收集 92；沙箱外 `uv run pytest -q` 为 92 passed、0 skipped、0 xfailed；`git diff --check` 通过。沙箱内 loopback 测试仍受 `PermissionError: [Errno 1] Operation not permitted` 限制。
