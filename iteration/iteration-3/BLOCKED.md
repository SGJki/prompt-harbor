状态：closed（2026-09-18）

阻塞项：无。沙箱禁止 loopback socket bind，已按 `AGENTS.md` 在沙箱外完成回归；Playwright Chromium 下载超时，但本机 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` 可用并完成真实浏览器测试。

证据：2026-09-18，`uv run pytest --collect-only -q` 收集 89；沙箱外 `uv run pytest -q` 为 89 passed、0 skipped、0 xfailed；`npm ci` 后浏览器测试使用 Playwright 默认 Chromium 或 `PROMPT_HARBOR_BROWSER` 指定浏览器。
