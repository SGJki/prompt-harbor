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

### Iteration Tracking

- Record project work under `iteration/iteration-{i}/`; root-level progress files are not iteration records.
- Use `iteration/INDEX.md` to locate the latest work, but first compare its highest-numbered row with the `iteration/iteration-{i}/` directories. Verify the candidate iteration's `PROGRESS.md` and `BLOCKED.md` before starting; if the index and records differ, the per-iteration records are authoritative and the index must be repaired.
- Treat an iteration as active until both `PROGRESS.md` and `BLOCKED.md` explicitly contain `状态：closed`; a missing record or any other status means it is not closed. Continue the highest-numbered unclosed iteration.
- If every existing iteration is closed, create the next number with both records before recording new work. Keep closed iterations as history; do not delete or reuse them.
- Update the matching `iteration/INDEX.md` row whenever an iteration is created, becomes active, or closes, including a concise scope summary and links to both records.
