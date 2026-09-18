# Iteration Index

用于快速判断各 iteration 的当前状态和工作内容。状态以对应目录中的 `PROGRESS.md` 与 `BLOCKED.md` 为准：两者均为 `状态：closed` 时才算 `closed`，否则为 `active`。

| Iteration | 状态 | 内容摘要 | 记录 |
|---|---|---|---|
| iteration-1 | `closed` | Codex/OpenAI 透明网关 MVP：代理完整性、截断、持久化、package 拆分与基础验收。 | [PROGRESS](./iteration-1/PROGRESS.md) · [BLOCKED](./iteration-1/BLOCKED.md) |
| iteration-2 | `closed` | 补齐转发、通知、清理、取消和失败记录的真实回归测试，统一规格与验收基线。 | [PROGRESS](./iteration-2/PROGRESS.md) · [BLOCKED](./iteration-2/BLOCKED.md) |
| iteration-3 | `closed` | 完成周期清理、loopback 监听校验、请求级 body 错误、入口一致性、SSE 通知和浏览器回归。 | [PROGRESS](./iteration-3/PROGRESS.md) · [BLOCKED](./iteration-3/BLOCKED.md) |
| iteration-4 | `closed` | 修复 sessions SSE 通知、change-log 并发游标窗口和无限增长，补齐 92 项测试验收。 | [PROGRESS](./iteration-4/PROGRESS.md) · [BLOCKED](./iteration-4/BLOCKED.md) |
| iteration-5 | `closed` | 知识与治理收尾：同步规格、测试矩阵、sidecar 方案、路线图和工作区规则。 | [PROGRESS](./iteration-5/PROGRESS.md) · [BLOCKED](./iteration-5/BLOCKED.md) |
| iteration-6 | `active` | 收紧透明代理 API key 安全边界：上游 URL 校验与告警、重定向凭证保护、UI/健康状态说明及回归测试。 | [PROGRESS](./iteration-6/PROGRESS.md) · [BLOCKED](./iteration-6/BLOCKED.md) |

## Maintenance

创建、关闭或切换活动 iteration 时，同步更新本索引的状态和内容摘要；详细进度、阻塞和证据仍只写在对应 iteration 目录内。
