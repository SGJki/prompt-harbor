# iteration-3

状态：closed（2026-09-18）

目标：为 `docs/TEST_REVIEW_FOLLOWUP.md` 中的 6 个未覆盖项补齐真实回归测试，并以最小实现修复通过全部验收。
顺序：先核对基线和运行器，再完成周期清理、监听边界、请求级 body 错误、入口一致性、SSE 直接 SQL 通知、真实浏览器交互。
最大风险：网关生命周期、SQLite 级联记录一致性、SSE 断开处理与 loopback/浏览器运行环境限制。

## 基线证据

- Initial `uv run pytest --collect-only -q`: **77 tests collected**; final collection after iteration-3 additions: **89 tests collected** (2026-09-18).
- Sandboxed `uv run pytest -q`: **34 failed, 43 passed** because loopback bind is denied (`PermissionError: [Errno 1] Operation not permitted`); this is an environment limitation, not a product result.
- `node --version`: `v26.8.2`; `npm --version`: `11.19.1`; the `npx playwright --version` probe did not complete in the sandbox.

## 进度

- [x] 基线与运行环境核对：89 collected；沙箱外 89 passed。
- [x] 周期清理、非本机监听、请求级 body 错误、入口一致性、SSE 直接 SQL 通知、真实浏览器交互。
- [x] 文档同步与最终验收：依赖安装后浏览器测试 1 passed；`git diff --check` 通过。
