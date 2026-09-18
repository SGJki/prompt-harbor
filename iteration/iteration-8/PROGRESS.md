# iteration-8

状态：closed（2026-09-18）

目标：修复安全审查发现的权限失败静默、重定向响应暴露、认证字典生命周期、非法端口和默认 origin 判断问题。

范围：只处理本次审查指出的安全边界，保留 iteration-7 的 Configuration Tab 和本地 fixture 兼容性。

完成：权限收紧失败终止初始化；3xx 不再暴露给客户端；请求头字典立即清空；端口在启动期校验；默认 upstream 按规范化 origin（含端口）判断。

验收：新增 4 项安全回归；沙箱外 `uv run pytest -q` 为 103 passed；沙箱外 `npm run test:browser` 为 1 passed；`git diff --check` 通过。
