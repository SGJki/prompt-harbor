# BLOCKED

Loopback is unavailable in this execution sandbox. Minimal reproduction:

```text
uv run python -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0))"
PermissionError: [Errno 1] Operation not permitted
```

The real upstream/gateway integration test (`tests/test_gateway.py`) therefore fails explicitly rather than skipping. Run `uv sync` and `uv run pytest -q` in a loopback-enabled environment to complete proxy, SSE timing, cancellation, and upstream failure groups.
