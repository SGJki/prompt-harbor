# iteration-2

状态：closed（2026-09-18）

目标：补齐转发、通知、清理、取消和失败记录的真实回归测试，并保持知识面与现役实现一致。
基线：HEAD=2dbc6f4；2026-09-18 收集 77 tests，loopback-enabled 环境回归 77 passed、0 skipped、0 xfailed。
完成：通知/headers、完整 purge 链、同库重启清理、同步客户端取消终态、模型/stream、CLI/API/body/usage 边界。
review 修正：边界等待三行且逐行终态；逐项记录请求体；旧取消测试改状态等待；StatusUpstream finally 隔离；清理测试执行停启两次。
当前定向结果：上述 review 相关 9 项测试为 9 passed；同期业务改动完成后全套回归为 77 passed、0 skipped、0 xfailed。
反向验证：临时副本切断 invalidate、丢弃 Authorization、漏删 usage、取消误记 upstream_error，四项目标测试均非零。
文档：TEST_MATRIX.md、TEST_REVIEW_FOLLOWUP.md 与现役规格已更新；git diff --check 通过。
外部改动：本次未修改业务实现；仅同步规格、路线图、验收基线和历史记录；当前回归已验证通过。
