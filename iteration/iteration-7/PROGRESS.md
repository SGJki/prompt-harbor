# iteration-7

状态：closed（2026-09-18）

目标：在内置审计台增加 Configuration Tab，使 `prompt-harbor.ini` 的全部可配置字段可视化编辑、原子写回，并让安全的运行时字段即时热更新。

范围：保持标准库、单进程、localhost 网关；不在运行中重绑监听 socket、切换数据库或接管 sidecar 子进程。

进度：

- [x] 盘点配置字段、启动优先级和网关运行时资源所有权。
- [x] 增加配置元数据、校验、原子写回和 `GET/PUT /api/config`。
- [x] 增加 Configuration Tab、敏感 token 脱敏、热更新/重启提示。
- [x] 补齐回归测试、文档和最终验收。

验收：`uv run pytest -q`（沙箱外）为 105 passed；`npm run test:browser` 为 1 passed；配置 API 临时 loopback fixture 验证通过；`git diff --check` 通过。

交付：新增 `GET/PUT /api/config`，配置元数据与启动校验共用，INI 通过临时文件原子替换并设为 0600；Configuration Tab 覆盖 `[gateway]` / `[sidecar]` 全字段，敏感 token 脱敏；可安全更新的 upstream、body limit、超时、SSE、purge、UI path、sidecar URL/token 即时作用于运行时，监听、数据库和 sidecar 子进程生命周期相关字段提示重启。
