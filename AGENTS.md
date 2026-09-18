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

- Track project work under `iteration/iteration-{i}/`; do not treat root-level progress files as the active iteration record.
- Before starting work, scan every `iteration/iteration-{i}/` directory and follow the highest-numbered iteration whose records do not contain `状态：closed`.
- An iteration is active until both its `PROGRESS.md` and `BLOCKED.md` contain `状态：closed`; closed iterations remain as history and must not be deleted as cleanup.
- When no unclosed iteration exists, create the next numbered iteration and put its progress and blocked records there.
- Maintain `iteration/INDEX.md` as the quick status index; update its row when an iteration is created, becomes active, or is closed. Treat the per-iteration records as the detailed source of truth.
