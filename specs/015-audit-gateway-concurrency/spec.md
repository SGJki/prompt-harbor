# Feature Specification: Audit Gateway Session and Concurrency Evolution

**Feature Branch**: `015-audit-gateway-concurrency`

**Created**: 2026-09-19

**Status**: Draft

**Input**: Iteration 14 domain and architecture decisions for the PromptHarbor local audit gateway.

## User Scenarios & Testing

### User Story 1 - Preserve client-visible forwarding while auditing concurrent calls (Priority: P1)

As a Codex client, I can open multiple concurrent connections and receive the same upstream behavior, while PromptHarbor records each call against the correct runtime and client session.

**Why this priority**: Transparent forwarding is the product's primary function and must remain available when audit work is contended or degraded.

**Independent Test**: Send interleaved streaming and non-streaming requests from multiple client sessions and verify byte-visible forwarding, independent completion, and terminal audit records.

**Acceptance Scenarios**:

1. **Given** several active client connections, **When** their calls complete in any order, **Then** each client receives its upstream response without waiting for unrelated calls.
2. **Given** an audit lock conflict or exhausted capture budget, **When** a call is forwarded, **Then** forwarding continues and the audit record eventually reaches a terminal state or records the documented capture degradation.
3. **Given** an SSE response, **When** upstream chunks arrive, **Then** chunks are flushed immediately and only one bounded final response snapshot is persisted after the stream completes.

### User Story 2 - Distinguish runtime and client sessions (Priority: P1)

As an operator, I can distinguish one gateway process run from a logical client session that spans connections and gateway restarts, and I can inspect calls through either dimension.

**Why this priority**: Concurrent users and sessions are otherwise mixed together, making audit results and future features unreliable.

**Independent Test**: Send calls with different `session-id` values across two runtime sessions, query both explicit API resources, and verify stable client-session grouping.

**Acceptance Scenarios**:

1. **Given** a call with a Codex `session-id`, **When** it is accepted, **Then** it is associated with the matching opaque `client_session_id` and the current `runtime_session`.
2. **Given** no reliable client identity, **When** default mode is enabled, **Then** an `unresolved/ephemeral` client session is created and forwarding continues.
3. **Given** no reliable client identity, **When** strict mode is enabled, **Then** the request is rejected before upstream forwarding.
4. **Given** a `thread-id` or request correlation header, **When** a call is stored, **Then** those values remain independent context fields and are not validated against the client session ID.

### User Story 3 - Inspect safe, explicit audit resources and sidecar outcomes (Priority: P2)

As an operator or CLI user, I can list and inspect runtime sessions, client sessions, calls, attempts, payload snapshots, usage, and sidecar outcomes using explicit semantics and safe payloads.

**Why this priority**: Audit data is useful only when its identifiers and persisted bytes have unambiguous meaning and do not expose credentials.

**Independent Test**: Exercise transparent `/v1/*` and semantic `/messages` calls, inspect API/UI/CLI results, and run sidecar health, crash, and ownership cases.

**Acceptance Scenarios**:

1. **Given** a valid `/messages` request, **When** it is forwarded, **Then** the persisted request payload is the sanitized outbound body.
2. **Given** invalid semantic JSON, **When** the request fails validation, **Then** unsanitized raw input is not persisted.
3. **Given** a sidecar health or models probe, **When** it is handled, **Then** it does not create an audit call; a `/messages` request does.
4. **Given** a gateway-managed sidecar crash during a stream, **When** the stream fails, **Then** the active call is recorded as incomplete/sidecar crash and the gateway does not concatenate a retry stream.

## Edge Cases

- Multiple calls from the same client session arrive over separate connections and complete out of order.
- The same client session reappears after a gateway restart.
- `session-id` is absent while metadata contains a supported session field; `options.sessionId` must not be used as fallback.
- Repeated identity fields disagree within the same identity layer; the source and mismatch are recorded without conflating thread or request IDs.
- SQLite is busy during terminal persistence; bounded retries run in-process without delaying the client stream.
- The concurrent response capture budget is exhausted; forwarding continues with `capture_degraded`.
- A response exceeds the per-call capture limit; `response_truncated` is independent of `response_complete`.
- The process crashes after forwarding but before terminal persistence; the bounded loss is accepted.
- A sidecar URL is external versus a child process started by the gateway; only owned children are reaped.

## Requirements

### Functional Requirements

- **FR-001**: The gateway MUST preserve transparent `/v1/*` client-visible request, response, header, and SSE flushing behavior.
- **FR-002**: Every audit call MUST associate one `runtime_session` and one `client_session`.
- **FR-003**: The gateway MUST resolve client identity from inbound `session-id`, then supported metadata session, then `unresolved/ephemeral` in default mode; strict mode MUST reject unresolved identity.
- **FR-004**: The gateway MUST keep `thread-id`, request correlation IDs, and provider session context separate from client identity.
- **FR-005**: The schema MUST expose `runtime_sessions`, `client_sessions`, and calls with both internal client-session row ID and opaque `client_session_id`.
- **FR-006**: The old ambiguous `/api/sessions` resource MUST be removed; explicit runtime-session and client-session resources MUST replace it.
- **FR-007**: Calls MUST be allowed to complete independently; no cross-call business ordering may be assumed.
- **FR-008**: Audit persistence MUST yield to forwarding, use short SQLite WAL transactions, and retry terminal writes with bounded in-process backoff.
- **FR-009**: Terminal change notifications MUST occur only after the terminal transaction commits.
- **FR-010**: SSE deltas MUST NOT be stored individually; one bounded final response snapshot MAY be persisted after flush.
- **FR-011**: The system MUST expose independent `response_complete`, `response_truncated`, and `capture_degraded` facts.
- **FR-012**: When capture budget is exhausted, the gateway MUST continue forwarding and release bounded response memory after terminal persistence succeeds.
- **FR-013**: Persisted request payloads MUST be the safe outbound bytes; invalid semantic bodies MUST not persist unsanitized raw input.
- **FR-014**: `/health` and `/models` probes MUST not create audit calls; `/messages` MUST use the normal call/attempt/payload/usage lifecycle.
- **FR-015**: External sidecar URLs and gateway-managed child processes MUST use explicit ownership semantics; the gateway MUST reap only children it started.
- **FR-016**: A sidecar crash during an active stream MUST fail that call without automatic stream concatenation; later calls MAY re-probe.
- **FR-017**: Authorization credentials MUST remain excluded from persisted or logged headers, and loopback binding MUST remain enforced.
- **FR-018**: CLI and UI views MUST name runtime-session and client-session dimensions explicitly and show thread/request correlation fields where applicable.

### Key Entities

- **Runtime Session**: One gateway process run; owns process-scoped lifecycle and can contain calls from many client sessions.
- **Client Session**: A logical Codex client session identified by opaque `client_session_id`; can span runtime sessions and connections.
- **Call**: One inbound request, associated with both session types and independent thread/request correlation context.
- **Attempt**: One provider execution for a call, with its own outcome and transport facts.
- **Payload Snapshot**: A bounded safe request or final response byte snapshot.
- **Usage**: Parsed semantic usage associated with a completed attempt/call.
- **Provider Session Context**: Provider/model-scoped value such as pi-ai `options.sessionId`; never a canonical client session.
- **Sidecar Instance**: External or gateway-owned provider bridge with explicit readiness, crash, and shutdown ownership.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Existing transparent proxy and UI regression tests remain green, with `uv run pytest -q` passing after implementation.
- **SC-002**: At least 20 concurrent interleaved calls across 4 client sessions complete with no cross-session association and no client-visible SSE buffering caused by audit writes.
- **SC-003**: Every started call that does not coincide with an accepted process crash reaches a terminal audit state, including bounded retry recovery and capture degradation facts.
- **SC-004**: Calls can be queried correctly by runtime session, client session, thread ID, and request correlation ID without using the removed ambiguous sessions resource.
- **SC-005**: No persisted or logged header/payload in the covered flows contains Authorization credentials or unsanitized invalid semantic request bytes.
- **SC-006**: Sidecar crash, restart ownership, health/models exclusion, and `/messages` lifecycle behavior are covered by automated regression scenarios.

## Assumptions

- The project remains a standard-library Python, single-process gateway with SQLite and an optional Node sidecar.
- The project is not deployed yet, so the schema and API may adopt explicit names without migration compatibility.
- A process crash may lose audit events that have not reached terminal persistence; durable write-ahead auditing is out of scope.
- Provider-specific behavior remains outside PromptHarbor's canonical client-session identity model.
- Existing localhost binding, authentication exclusion, and immediate SSE flushing are compatibility constraints.
