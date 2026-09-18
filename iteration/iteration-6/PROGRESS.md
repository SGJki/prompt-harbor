# iteration-6

状态：closed（2026-09-18）

目标：收紧透明代理的 API key 安全边界，覆盖上游 URL 校验与告警、重定向凭证保护，并记录进程/本地存储的残余风险。

范围：保持标准库、单进程和 localhost 网关设计；允许 loopback HTTP fixture 继续用于本地测试，禁止非 loopback HTTP 上游。

完成：公网 HTTP 上游拒绝、loopback fixture 告警、自定义上游 Authorization 告警、重定向禁止、认证字段短时内存清理、SQLite 0600 权限、UI 告警和文档同步。

验收：新增 7 项安全策略测试；沙箱外 `uv run pytest -q` 为 99 passed；沙箱外 `npm run test:browser` 为 1 passed；`git diff --check` 通过。
