# Verification note

The loopback integration suite is currently blocked in this sandbox: binding
`127.0.0.1` raises `PermissionError: [Errno 1] Operation not permitted`.
Run `uv run pytest -q` in a loopback-enabled environment to verify proxy,
SSE timing, cancellation, and upstream failure behavior.
