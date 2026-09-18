# iteration-11

状态：closed（2026-09-18）

目标：修复 Configuration Tab CR 指出的托管 sidecar 生命周期、sidecar token 重启语义和敏感配置回写问题，同时保留 `0ffedb8` 的来源锁定、CSRF 和 INI 注释保留改动。

进度：

- [x] 区分托管 sidecar 动态 URL 与 INI 外部 URL。
- [x] 将 sidecar token 标记为 restart-required，并防止 startup secret 被无意写回。
- [x] 增加回归测试。
- [x] 完成全套回归并关闭 iteration。

验收：`uv run pytest -q`（沙箱外）为 111 passed；`npm run test:browser` 为 1 passed；`git diff --check` 通过。
