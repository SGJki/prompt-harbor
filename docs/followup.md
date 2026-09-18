# 后续能力

2026-09-18 复核结论：周期清理、loopback 监听校验和非法 `PROMPT_HARBOR_MAX_BODY` 的请求级错误处理已在 iteration-3/4 关闭；以下仍是未实现路线图。

以下能力仍未实现，保留在路线图中：

- 修改 request 后再转发
- 可配置自动重试与退避
- 模型切换
- 按模型、请求类型或规则路由到不同上游
- Claude Code 的 Anthropic Messages/SSE 适配
- 成本统计、token 趋势和调用分析
- 请求/响应搜索与过滤
- 导出、回放和事件重放
- OpenTelemetry 等外部观测系统导出
- 更完善的 secret 脱敏和加密存储
