# 历史 iteration-1 Verification note

状态：closed（2026-09-18）

本文件记录当时的沙箱限制，不代表当前项目阻塞状态；现役回归状态见 `iteration/iteration-2/PROGRESS.md`。

The loopback integration suite is currently blocked in this sandbox: binding
`127.0.0.1` raises `PermissionError: [Errno 1] Operation not permitted`.
Run `uv run pytest -q` in a loopback-enabled environment to verify proxy,
SSE timing, cancellation, and upstream failure behavior.
