# Test review follow-up

2026-09-18 复核结论：以下 6 项均未关闭。`PROMPT_HARBOR_MAX_BODY` 已有配置失败的回归测试，但实现仍未提供请求级友好处理；其余项目也没有新的实现或测试证据可以关闭。

以下事项由测试审查记录，继续保留：

| 项目 | 状态 | 写位置/证据 | 影响 |
|---|---|---|---|
| 周期清理缺失 | 未关闭 | `prompt_harbor/core.py` 启动时调用 `purge`，未发现后台周期任务；启动清理测试只覆盖启动时机 | 运行中超过 2 天的数据不会自动清理 |
| 非本机监听未限制 | 未关闭 | `prompt_harbor.py start --listen` 接受任意 host；规格要求只绑定 127.0.0.1 | 可能暴露到局域网 |
| 非法 `PROMPT_HARBOR_MAX_BODY` | 未关闭 | 现状测试覆盖 `int()` 解析失败且进程存活 | 单请求线程异常并断开，配置错误未被友好处理 |
| 重复模块实现 | 未关闭 | `prompt_harbor/core.py` 与 `prompt_harbor/{server,proxy,database,retention,usage}.py` 存在并行入口 | 后续维护可能修改错误实现 |
| SSE 规格差异 | 未关闭 | 当前通知测试验证真实 POST 完成后的 `invalidate`；直接 SQL 插入/新 session 通知未纳入本轮 UI_SPEC 验收 | 更窄的事件触发覆盖 |
| 前端逻辑零覆盖 | 未关闭 | 本轮仅验证静态资源和后端 API；`ui/js` 交互没有浏览器测试 | 前端筛选、刷新和详情逻辑可能回归 |
