## Project Extensions

### Think Before Coding

- When changing proxying, persistence, or cleanup, trace the session → call → attempt → payload/usage lifecycle and preserve consistency across those records.
- When changing the optional sidecar or its gateway bridge, trace sidecar startup, request forwarding, error handling, and shutdown ownership alongside the persisted call/attempt lifecycle.

### Simplicity First

- For gateway or CLI changes, keep the current standard-library, single-process design unless repository requirements require otherwise; avoid introducing a framework or service layer.

### Surgical Changes

- When touching HTTP forwarding or headers, preserve localhost binding, authorization exclusion from stored/logged headers, and immediate SSE chunk flushing.

### Goal-Driven Execution

- Use `uv run pytest -q` as the regression check. If it fails because loopback is unavailable or due to another clear sandbox limitation, retry the command outside the sandbox before reporting the problem. Only when loopback remains unavailable after that retry, perform the documented manual local-fixture verification and report the environment limitation.
