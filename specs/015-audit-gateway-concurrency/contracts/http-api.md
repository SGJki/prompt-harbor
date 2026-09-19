# HTTP API Contract

All API responses are JSON with `Cache-Control: no-store`. The gateway remains loopback-only and keeps Authorization out of stored or logged headers. Existing transparent `/v1/*` and `/messages` forwarding contracts remain client-compatible; the resources below describe the audit surface.

## Explicit resources

### `GET /api/runtime-sessions`

Returns `{ "runtime_sessions": [...] }`. Each item includes `id`, `agent`, `started_at`, `last_seen_at`, `cwd`, `project_name`, and `call_count`. The resource represents gateway process runs.

Supported query parameters: `limit` (bounded positive integer), `before` (optional cursor), and `id` (exact lookup where implemented).

### `GET /api/client-sessions`

Returns `{ "client_sessions": [...] }`. Each item includes internal `id`, opaque `client_session_id`, `identity_status`, `identity_source`, `first_seen_at`, `last_seen_at`, and `call_count`. The opaque ID is displayed as data, never interpreted as an authorization token.

Supported query parameters: `limit`, `before`, `client_session_id`, and `identity_status`.

### `GET /api/calls`

Returns `{ "calls": [...] }`. Each item includes `id`, `runtime_session_id`, `client_session_row_id`, `client_session_id`, `thread_id`, `request_correlation_id`, `provider_session_context` when safe, endpoint/model, lifecycle status, status code, timing, byte counts, and error metadata.

Supported filters: `runtime_session_id`, `client_session_id`, `thread_id`, `request_correlation_id`, `status`, and bounded pagination/limit.

### `GET /api/calls/{id}`

Returns one call with its attempt, sanitized headers, bounded payload snapshot, usage, and independent capture/transport flags. Missing calls return 404. Authorization values must be absent or redacted.

### `GET /api/overview`

Returns aggregate counts plus explicit `runtime_sessions`, `client_sessions`, and recent calls. It must not expose an ambiguous `sessions` field.

### `GET /api/events`

SSE invalidation stream. Events are emitted only after the transaction that makes the changed resource queryable commits. Event data is a JSON object such as `{ "resource": "calls" }`, `{ "resource": "runtime-sessions" }`, or `{ "resource": "client-sessions" }`. A client may reconnect and refetch; event delivery is advisory, not a durability mechanism.

## Removed resource

`GET /api/sessions` is removed and returns 404. Clients must use `/api/runtime-sessions` or `/api/client-sessions` according to the desired scope.

## Identity request contract

- `session-id` is the primary client-session identity source and is forwarded unchanged on transparent upstream requests.
- A PromptHarbor-private fallback header, if needed for metadata resolution, is removed before forwarding.
- `thread-id` and `x-client-request-id`/`x-request-id`/`traceparent` are recorded as separate fields.
- pi-ai `/messages` `options.sessionId` is stored as provider session context scoped by provider/model and is never a client-session fallback.

## Error behavior

Strict identity mode returns a normal structured 4xx error before upstream forwarding when no reliable client identity exists. Default mode creates an unresolved/ephemeral client session and proceeds. Persistence contention does not change the client response; terminal audit retry happens in-process.
