# Phase 0 Research: Audit Gateway Session and Concurrency Evolution

This research resolves the technical choices needed by the implementation plan. The product decisions were confirmed in iteration 14; the repository inspection below maps them to the current implementation.

## 1. Session identity and lifecycle

**Decision**: Keep two first-class session records: `runtime_sessions` for one gateway process run and `client_sessions` for an opaque client identity that can span connections and runtime restarts. Every call references both. Resolve client identity from the inbound Codex `session-id` header, then the supported semantic metadata session field, then an `unresolved/ephemeral` client session in default mode. Strict mode rejects a call with no reliable identity. Never use `options.sessionId`, Authorization, IP, User-Agent, TCP connection, or a request ID as the canonical client identity.

**Rationale**: The current schema has `sessions` and `calls.session_id`, but the server creates that row once at startup, so concurrent Codex sessions are mixed together. Codex's Responses client sends `session-id`, `thread-id`, and `x-client-request-id`; they represent different scopes. A client session must remain stable across multiple connections and gateway runs, while a runtime session must remain process-local.

**Alternatives considered**:

- Derive identity from TCP connection, process, IP, User-Agent, or Authorization: rejected because none remains stable across connections/restarts and some are security-sensitive or shared.
- Use `thread-id` or `x-client-request-id`: rejected because they are thread and request/trace context, not a long-lived client identity.
- Use pi-ai `/messages` `options.sessionId`: rejected because providers use it inconsistently for prompt-cache affinity, routing, session headers, or not at all.

**Evidence**: `prompt_harbor/core.py` currently creates one startup row in `sessions`; iteration 14 references Codex source files `codex-rs/codex-api/src/requests/headers.rs`, `endpoint/responses.rs`, `tests/clients.rs`, and `core/src/client.rs`; pi-ai sources show provider-scoped `options.sessionId` behavior.

## 2. SQLite concurrency and terminal persistence

**Decision**: Retain a single-process standard-library architecture. Enable SQLite WAL and `busy_timeout`, keep lifecycle transactions short, and add bounded retry/backoff for terminal persistence through an in-process worker or equivalent queue. Commit the terminal transaction before emitting `change_log`/SSE invalidation. Forward response bytes without waiting on audit writes. A process crash may lose events not yet persisted; a live process must converge every started call to a terminal audit state.

**Rationale**: The gateway is local and single-process, so an external broker or audit service would add operational complexity without solving the primary compatibility requirement. SQLite WAL allows readers and writers to overlap, while bounded retries handle concurrent writers. Committing before notification prevents the UI from observing an event for data that is not queryable.

**Alternatives considered**:

- Write one SQLite transaction per SSE delta: rejected because it delays streaming and creates unnecessary lock contention.
- Block forwarding until the terminal write succeeds: rejected because it violates the forward-first product boundary.
- Add a message broker or separate persistence service: rejected by the standard-library, single-process constraint and unnecessary durability scope.
- Guarantee crash-proof audit durability: deferred; iteration 14 explicitly accepts loss between forwarding and terminal persistence.

**Evidence**: `core.py` currently performs `_begin_attempt` and `_finish_attempt` synchronously and notifies after commit; the plan preserves that lifecycle while isolating retry behavior and replacing trigger-driven notification assumptions where necessary.

## 3. Bounded response capture under concurrency

**Decision**: Capture at most the configured per-call response limit and enforce a process-wide concurrent capture budget. Continue forwarding when the budget is exhausted, set `capture_degraded`, and release retained response memory after terminal persistence succeeds. Keep `response_complete` (upstream transport reached its end), `response_truncated` (per-call snapshot limit exceeded), and `capture_degraded` (shared budget denied) as independent facts. Store one final snapshot, never per-delta rows.

**Rationale**: Concurrent SSE calls can otherwise multiply memory usage even though forwarding must remain unaffected. The three flags describe different failure dimensions and allow the UI and CLI to distinguish provider transport failure from audit capture limits.

**Alternatives considered**:

- Cancel or slow a client stream when capture is full: rejected because forwarding is first priority.
- Persist every delta to avoid in-memory assembly: rejected because it increases lock contention and does not preserve meaningful audit semantics.
- Collapse all incomplete cases into one `truncated` flag: rejected because transport completeness and capture capacity are independent.

## 4. Safe outbound payloads and semantic paths

**Decision**: `payload.request_body` is the bounded byte sequence actually sent upstream. Transparent `/v1/*` records the client bytes. `/messages` records the sanitized outbound body after removing credentials and callback capabilities. Invalid semantic JSON records failure metadata without persisting unsanitized raw input. `/health` and `/models` do not create calls; `/messages` uses the normal call -> attempt -> payload/usage lifecycle.

**Rationale**: Auditors need to know what the provider could observe, while the gateway must not create a second sensitive raw-ingress archive. The current implementation already has separate transparent and pi-messages handlers, making this boundary incremental.

**Alternatives considered**:

- Persist both raw and outbound bodies by default: rejected because raw invalid semantic input can contain credentials or callback capabilities.
- Store SSE deltas as payload records: rejected by the response snapshot decision.
- Treat sidecar health/models as audited calls: rejected because they are runtime readiness/control probes, not user model calls.

## 5. Explicit API, CLI, and UI resources

**Decision**: Rename the schema table to `runtime_sessions`; add `client_sessions` and explicit call fields (`runtime_session_id`, `client_session_row_id`, `thread_id`, `request_correlation_id`). Remove `/api/sessions`; expose `/api/runtime-sessions` and `/api/client-sessions`, with call filtering by either dimension. Update CLI and UI labels and details accordingly. No compatibility migration layer is required because the project is not deployed.

**Rationale**: The existing `/api/sessions` endpoint and UI label hide the fact that the data is process-scoped. Explicit resource names prevent future features from depending on ambiguous semantics.

**Alternatives considered**:

- Keep `/api/sessions` as an alias: rejected because it preserves the ambiguity the domain model is correcting.
- Reuse a single table with a type discriminator: rejected because separate tables make ownership, uniqueness, and query semantics explicit.
- Use `external_id` for the client identity column: rejected as semantically unclear; use `client_session_id` for the opaque client-provided value and a separate internal row ID.

## 6. Sidecar ownership and failures

**Decision**: Treat an external sidecar URL and a gateway-started child process as explicit ownership modes. Only a child started by the gateway is reaped. URL changes affect subsequent requests; command, token, and process-lifecycle changes require restart. A sidecar crash during an active stream fails that call and records the crash; do not restart and concatenate another SSE stream. Later requests may re-probe. Transparent `/v1/*` remains available when the sidecar is unavailable.

**Rationale**: Process ownership is the boundary that determines who may stop/restart a sidecar. In-stream retry would create an invalid client-visible response and ambiguous audit history.

**Alternatives considered**:

- Treat every configured URL as gateway-owned: rejected because an external process may be shared or managed elsewhere.
- Retry a crashed stream transparently: rejected because the client could receive concatenated or duplicated events.
- Make sidecar availability block transparent traffic: rejected because the sidecar is an extension, not the primary gateway path.

## 7. Implementation shape

**Decision**: Decompose `core.py` incrementally along identity, lifecycle, storage, transport, events, and sidecar boundaries. Start with identity and storage/lifecycle seams, then adapt handlers and API consumers. Keep the standard-library server and existing tests as the regression baseline.

**Rationale**: A full rewrite would increase compatibility risk. Small seams allow concurrency tests to exercise persistence and identity independently before the HTTP surface changes.

No `NEEDS CLARIFICATION` items remain after this research.
