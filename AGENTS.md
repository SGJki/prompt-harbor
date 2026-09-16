## Project Extensions

### Think Before Coding

- When changing proxying, persistence, or cleanup, trace the session → call → attempt → payload/usage lifecycle and preserve consistency across those records.

### Simplicity First

- For gateway or CLI changes, keep the current standard-library, single-process design unless repository requirements require otherwise; avoid introducing a framework or service layer.

### Surgical Changes

- When touching HTTP forwarding or headers, preserve localhost binding, authorization exclusion from stored/logged headers, and immediate SSE chunk flushing.

### Goal-Driven Execution

- Use `uv run pytest -q` as the regression check; when loopback is unavailable, perform the documented manual local-fixture verification and report the environment limitation.
- If a command fails because of network, DNS, dependency download, or another clear sandbox limitation, retry it outside the sandbox before reporting the problem.
