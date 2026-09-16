目标：实现可实际使用的 Codex/OpenAI 透明网关 MVP。
顺序：schema/config/session、代理与流式记录、usage、CLI、端到端测试、文档。
当前：已建立 schema/config/CLI/retention 单元测试与真实 HTTP SSE 集成测试。
最大风险：客户端取消、截断上限、loopback 沙箱限制及不同 SSE usage 格式的兼容性。
