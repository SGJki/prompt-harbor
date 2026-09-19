# CLI and UI Contract

## CLI

The CLI keeps `init`, `serve`, and `purge` semantics. `list` and `show` must use explicit names:

- `list` displays `runtime_session_id`, `client_session_id` (opaque value), `client_session_row_id` when useful for diagnostics, `thread_id`, `request_correlation_id`, call status, endpoint, and timing.
- `show <call-id>` displays both session scopes, independent attempt status, safe request/response headers and payloads, usage, and `response_complete`, `response_truncated`, and `capture_degraded`.
- No CLI output calls the process-level ID merely `session_id` without a scope label.

The CLI must continue to avoid printing Authorization credentials and must return a non-zero status for unknown calls or invalid filters.

## UI

The audit UI exposes separate Runtime Sessions and Client Sessions views. Calls can be filtered by either view and show the other scope as a linked field. Call detail shows thread/request correlation fields, provider session context (when present), attempt outcome, safe payloads, usage, and all independent capture/transport flags.

The UI subscribes to `/api/events`, then refetches the affected explicit resource. It must tolerate advisory or duplicated invalidation events. It must not construct a new ambiguous `sessions` store key.

The configuration surface may expose identity strict/default mode and capture budget settings, but settings that affect sidecar process ownership remain restart-required as documented by the existing configuration contract.
