# iteration-10

状态：closed（2026-09-18）

目标：前端增量重构——审计台安全加固（Host 白名单、安全响应头、写接口 CSRF 深度防御）、配置体验完善（INI 写回保留注释、值来源追踪与锁定）、视觉/可访问性打磨（CSS token、tablist、键盘可达）。

范围：保持标准库、单进程、localhost 网关与无构建前端；不改代理转发路径的既有行为。

进度：

- [x] Host 头白名单与安全响应头落地。
- [x] PUT /api/config 自定义头要求与锁定字段拒绝。
- [x] INI 逐行写回保留注释与未知键。
- [x] 前端 config 模块拆分、CSS token 化与可访问性。
- [x] 回归测试、浏览器测试与文档同步。

验收证据（2026-09-18）：`uv run pytest -q` 沙箱外 **109 passed**；沙箱内 loopback bind 被环境拒绝。`npm run test:browser` **1 passed**；覆盖 Configuration 保存闭环与响应头断言。`git diff --check` 通过。
