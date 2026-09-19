# Implementation Plan: Audit Gateway Session and Concurrency Evolution

**Branch**: `015-audit-gateway-concurrency` | **Date**: 2026-09-19 | **Spec**: [spec.md](spec.md)

**Input**: Iteration 14 domain and architecture decisions for PromptHarbor.

## Summary

PromptHarbor must remain a transparent local gateway while several Codex sessions use it concurrently. The implementation will split process runtime sessions from persistent client sessions, associate every call with both, preserve independent thread/request/provider context, and replace the ambiguous sessions API. SQLite remains the single-process audit store, but lifecycle persistence will use WAL, short transactions, bounded terminal retries, and post-commit invalidation. Streaming responses continue to flush immediately; only a bounded final response snapshot is persisted. The work finishes by aligning semantic `/messages` payload safety, sidecar ownership/crash handling, CLI, UI, and regression coverage with the new model.

## Technical Context

**Language/Version**: Python 3.x (current project runtime), browser JavaScript, Node.js sidecar fixture

**Primary Dependencies**: Python standard library (`http.server`, `sqlite3`, `urllib`), pytest, Playwright browser tests, optional pi-ai sidecar

**Storage**: SQLite database with local file permissions, new `runtime_sessions` and `client_sessions` tables, existing calls/attempts/payloads/usage lifecycle

**Testing**: `uv run pytest -q`; targeted HTTP concurrency tests; existing Playwright/browser test suite where UI contracts change

**Target Platform**: Local loopback gateway on macOS/Linux development environments; no external service dependency

**Project Type**: Single-process local web service with CLI and embedded audit UI

**Performance Goals**: Preserve immediate SSE chunk flushing; allow at least 20 interleaved calls across 4 client sessions in the acceptance fixture; terminal audit writes must not hold client stream locks

**Constraints**: Forwarding takes precedence over audit latency; SQLite writes are bounded and retried in-process; process crash may lose not-yet-persisted events; Authorization never enters persisted/logged headers; localhost binding and sidecar ownership rules remain enforced

**Scale/Scope**: One gateway process, multiple concurrent HTTP connections, client sessions spanning process restarts, bounded local audit history; no distributed broker or migration compatibility layer

## Constitution Check

The checked-in `.specify/memory/constitution.md` is still the generated placeholder: it contains no ratified principles, gates, version, or governance rules. Therefore there is no constitutional gate to fail. The effective repository constraints in `AGENTS.md` are applied as design gates:

- standard-library, single-process architecture is preserved;
- session -> call -> attempt -> payload/usage lifecycle remains intact;
- localhost binding, Authorization exclusion, and immediate SSE flushing remain intact;
- changes are planned as surgical module/API updates with `uv run pytest -q` regression validation;
- sidecar startup, forwarding, failure, and shutdown ownership are traced with persisted call lifecycle.

**Initial gate**: PASS. No unjustified complexity or unresolved requirement remains after Phase 0 research.

## Project Structure

### Documentation (this feature)

```text
specs/015-audit-gateway-concurrency/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── contracts/
    ├── http-api.md
    ├── cli-ui.md
    └── lifecycle-events.md
```

### Source Code

```text
prompt_harbor/
├── core.py              # existing handler/server; progressively decomposed
├── database.py          # schema, connection pragmas, retry/transaction helpers
├── models.py            # runtime/client session and lifecycle data helpers
├── identity.py          # planned identity extraction and policy resolution
├── lifecycle.py         # planned call/attempt start and terminal aggregation
├── storage.py           # planned short transactions and terminal retry worker
├── transport.py         # planned capture budget and forwarding helpers
├── events.py            # planned post-commit invalidation/change-log handling
├── pi_messages.py       # semantic payload/provider session context
├── sidecar.py           # explicit external/managed ownership and crash state
├── cli.py               # explicit runtime/client session output and filters
└── server.py            # runtime session startup and graceful shutdown

ui/
├── js/api.js            # explicit audit resource clients
├── js/store.js          # runtime/client session state
├── js/views.js          # explicit views, filters, and call detail
└── js/realtime.js       # post-commit invalidation refetch

tests/
├── test_schema.py
├── test_gateway.py
├── test_concurrency.py  # planned interleaved calls, retries, capture budget
├── test_identity.py     # planned source precedence and strict/default modes
├── test_api_sessions.py # planned explicit API/CLI/UI resource contracts
└── test_sidecar.py
```

**Structure Decision**: Keep the current single Python project and embedded UI. Add small focused modules only where they create testable seams; retain compatibility wrappers while `core.py` is decomposed incrementally. No framework, service, broker, or second deployable is introduced.

## Implementation Sequence

The phases are dependency ordered. Each phase ends with focused tests before the next public surface is changed.

### Phase 1: Schema and domain seams

1. Replace the ambiguous `sessions` schema and helpers with `runtime_sessions`; add `client_sessions` with internal row ID, opaque `client_session_id`, identity status/source, timestamps, and safe metadata.
2. Add call columns and indexes for `runtime_session_id`, `client_session_row_id`, `thread_id`, `request_correlation_id`, and provider session context. Add `capture_degraded` to the payload snapshot.
3. Update retention/purge, triggers/change-log resource names, fixtures, and model helpers to use explicit entities. The project is pre-deployment, so create the new schema directly rather than carrying a migration alias.
4. Add domain dataclasses/constants for identity status, call/attempt terminal states, and independent capture facts without changing HTTP behavior yet.

**Exit criteria**: Fresh databases have the explicit schema; lifecycle foreign keys and indexes are correct; existing non-HTTP schema tests are adapted and pass.

### Phase 2: Identity extraction and call start

1. Implement an `identity.py` resolver with the exact precedence `session-id` -> supported semantic metadata session -> unresolved/strict policy.
2. Preserve inbound `session-id` on transparent upstream requests; strip only a PromptHarbor-private fallback header if one is used.
3. Extract `thread-id`, request/trace correlation IDs, and pi-ai provider session context into separate call fields. Do not compare or validate their values against the client identity.
4. Make every `/v1/*` and `/messages` call resolve a client session before starting its call/attempt record. Ensure multiple HTTP handler threads cannot create duplicate explicit client-session rows; create a distinct ephemeral row per unresolved call in default mode.
5. Add configuration/CLI/UI representation for default unresolved mode and strict mode, with safe defaults matching the spec.

**Exit criteria**: Calls from multiple headers and connections are grouped correctly; missing identity follows the selected policy; `options.sessionId` cannot become a fallback; identity tests cover concurrent creation and restart reuse.

### Phase 3: Forward-first storage and capture budget

1. Introduce storage helpers that open per-operation SQLite connections, set WAL and `busy_timeout`, keep start/terminal transactions short, and classify retryable lock errors.
2. Keep the start record minimal and fast. Move terminal persistence behind a bounded in-process retry worker or equivalent queue that never owns the SSE write lock.
3. Emit change-log/invalidation only after the terminal transaction commits, including explicit runtime/client session resource names.
4. Add process-wide capture budgeting around response snapshot buffers. Set `capture_degraded` when the budget is unavailable, while continuing to forward all chunks.
5. Preserve `response_complete` and `response_truncated` independently and release retained buffers after terminal persistence succeeds. Bound any retry-held data.

**Exit criteria**: Lock contention does not delay forwarding; a live process converges started calls to terminal records; notifications never precede queryable data; concurrent streams enforce memory bounds and independent flags.

### Phase 4: Transport and semantic payload behavior

1. Adapt transparent forwarding to the new lifecycle/storage interfaces while preserving headers, redirects, localhost security, and immediate flush behavior.
2. Adapt `/messages` sanitization so the persisted request is exactly the safe outbound body; invalid JSON records failure metadata without raw unsanitized bytes.
3. Assemble SSE bytes in the bounded capture buffer and persist one final snapshot. Keep usage normalization independent from transport status.
4. Ensure `/health` and `/models` remain outside the audit lifecycle while `/messages` uses the same call/attempt/payload/usage records.

**Exit criteria**: Transparent and semantic paths satisfy payload safety and response snapshot contracts; existing proxy behavior remains byte-compatible; usage and protocol-error cases reach correct terminal states.

### Phase 5: Explicit API, CLI, and UI resources

1. Replace `/api/sessions` with `/api/runtime-sessions` and `/api/client-sessions`; update overview and calls payloads to use explicit fields.
2. Add call filters for both session scopes plus thread and request correlation IDs, with bounded pagination/limits.
3. Update CLI `list`/`show` to display explicit runtime/client IDs, thread/request context, provider context, capture facts, and safe payloads.
4. Split UI state and views into Runtime Sessions and Client Sessions; update details, filters, labels, and realtime refetch keys. Remove ambiguous `sessions` resource use.
5. Return 404 for `/api/sessions` and update browser/static tests to assert removal rather than alias compatibility.

**Exit criteria**: API, CLI, and UI contracts match [contracts/http-api.md](contracts/http-api.md) and [contracts/cli-ui.md](contracts/cli-ui.md); invalidation events only target explicit resources.

### Phase 6: Sidecar lifecycle alignment

1. Make sidecar ownership explicit in the runtime state and configuration path; preserve restart-required semantics for command/token/process-lifecycle changes.
2. Record active `/messages` sidecar failures through the normal attempt/call terminal path. Do not create audit calls for readiness/model probes.
3. Detect a managed sidecar crash during a stream, fail the active call once, release capture state, and avoid a second concatenated stream. Allow later requests to re-probe.
4. Reap only child processes started by the gateway; never stop externally managed URLs. Keep transparent `/v1/*` available when sidecar readiness is degraded.

**Exit criteria**: External and managed sidecar tests cover startup, readiness, crash, shutdown, and ownership alongside persisted lifecycle records.

### Phase 7: Regression hardening and decomposition cleanup

1. Add an interleaving harness for at least 20 calls across four client sessions and separate TCP connections, including out-of-order completion.
2. Add deterministic SQLite lock/retry tests, notification-after-commit tests, capture-budget tests, restart grouping tests, and accepted crash-loss boundary tests.
3. Run the full pytest suite and browser tests; fix any compatibility regressions before moving further decomposition.
4. Move remaining identity, lifecycle, storage, transport, event, and sidecar logic out of `core.py` only when tests cover the seam; preserve public imports used by existing callers.
5. Update `docs/SPEC.md`, `docs/UI_SPEC.md`, `docs/TEST_MATRIX.md`, and iteration records with the final contracts and evidence.

**Exit criteria**: The quickstart scenarios pass; all automated checks pass in an environment where loopback binding is available; documentation and iteration tracking agree with code.

## Testing Strategy

- **Unit**: identity precedence and strict/default policy; header classification; payload sanitization; capture flag transitions; retry classification; sidecar ownership state.
- **Database/integration**: fresh schema, concurrent explicit-session upsert, WAL/busy timeout, terminal retry, post-commit event ordering, purge cascades, cross-runtime grouping.
- **HTTP contract**: transparent `/v1/*`, semantic `/messages`, explicit API resources, removed `/api/sessions`, filtering and 404/error behavior.
- **Concurrency**: interleaved SSE and non-streaming calls over multiple connections; completion order independence; capture budget and memory release; sidecar crash during stream.
- **Browser/CLI**: explicit Runtime Sessions and Client Sessions views, realtime invalidation, safe call details, scoped CLI output.
- **Regression**: `uv run pytest -q` remains the required baseline. If sandbox loopback restrictions reproduce the known failure, rerun outside the sandbox before reporting it.

## Post-Design Constitution Check

**Gate**: PASS. Phase 1 artifacts keep the standard-library single-process architecture, retain the existing lifecycle, make no unsupported durability promise, preserve forwarding/security constraints, and assign sidecar ownership explicitly. The placeholder constitution still has no ratified gates; all effective `AGENTS.md` constraints are represented in the phases and quickstart.

## Complexity Tracking

No constitution violations or extra deployables are introduced. The only intentional complexity is a bounded in-process terminal retry/capture coordinator, required to reconcile concurrent SQLite writers and forward-first streaming while keeping the existing single-process boundary.
