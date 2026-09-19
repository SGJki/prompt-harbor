# iteration-13

状态：closed（2026-09-19）

目标：同步 `AGENTS.md` 的 Iteration Tracking 规则与当前 `iteration/INDEX.md` 及逐轮记录的实际工作方式。

进度：

- [x] 核对现有 iteration 目录、索引和关闭状态。
- [x] 更新 `AGENTS.md` 的 iteration 定位、状态判定和索引同步规则。
- [x] 完成本轮文档变更验收并关闭 iteration。

验收：沙箱内 `uv run pytest -q` 受 loopback bind 权限限制（`PermissionError`）；沙箱外 `uv run pytest -q` 为 **152 passed**；`git diff --check` 通过。本轮仅修改项目规则与迭代记录，未涉及业务代码。
