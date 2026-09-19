---

description: "Actionable implementation tasks for audit gateway session and concurrency evolution"
---

# Tasks: Audit Gateway Session and Concurrency Evolution

**Input**: Design documents from `specs/015-audit-gateway-concurrency/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, and `quickstart.md`

**Organization**: Tasks are grouped by user story. All tasks include the concrete repository path they modify or validate.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish seams and fixtures without changing client-visible behavior.

- [X] T001 Create the planned module boundaries and compatibility exports in `prompt_harbor/identity.py`, `prompt_harbor/lifecycle.py`, `prompt_harbor/storage.py`, `prompt_harbor/transport.py`, and `prompt_harbor/events.py`.
- [X] T002 [P] Add reusable temporary-database, loopback-upstream, and concurrent-request fixtures in `tests/conftest.py`.
- [X] T003 [P] Add a focused test-fixture helper for interleaved SSE chunks and out-of-order completions in `tests/test_concurrency.py`.
- [X] T004 [P] Record the current HTTP, CLI, UI, and sidecar regression commands in `specs/015-audit-gateway-concurrency/quickstart.md` and verify the paths match the repository.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Implement shared persistence, lifecycle, and bounded capture foundations before any story-specific surface changes.

**Checkpoint**: Fresh databases can represent the new domain and a started call can converge to a terminal record without holding a client stream lock.

- [X] T005 Replace `sessions` with `runtime_sessions` and create `client_sessions` in `prompt_harbor/core.py`, including internal row ID, opaque `client_session_id`, `identity_status`, `identity_source`, timestamps, and safe metadata fields from `data-model.md`.
- [X] T006 Add `runtime_session_id`, `client_session_row_id`, `thread_id`, `request_correlation_id`, and provider session context columns plus indexes to `calls` in `prompt_harbor/core.py`.
- [X] T007 Add `capture_degraded` to `payloads` and preserve independent `response_complete`, `response_truncated`, and request truncation fields in `prompt_harbor/core.py`.
- [X] T008 [P] Update runtime/client session creation and lookup helpers in `prompt_harbor/models.py` and `prompt_harbor/server.py` without changing request forwarding yet.
- [X] T009 [P] Implement SQLite connection pragmas, WAL setup, busy timeout, lock classification, and bounded retry/backoff helpers in `prompt_harbor/storage.py` and `prompt_harbor/database.py`.
- [X] T010 Implement call/attempt start, terminal state, and aggregate outcome helpers in `prompt_harbor/lifecycle.py`, preserving the `runtime_session -> call -> attempt -> payload/usage` relationship.
- [X] T011 Implement post-commit resource invalidation dispatch in `prompt_harbor/events.py`; do not emit an SSE/change-log event until the terminal transaction commits.
- [X] T012 Implement a process-wide bounded response capture coordinator in `prompt_harbor/transport.py` that continues forwarding when budget acquisition fails and releases retained bytes after persistence.
- [X] T013 [P] Add strict/default identity-policy and capture-budget settings to `prompt_harbor/config.py`, including validation and restart/runtime semantics.
- [X] T014 [P] Update retention and cascade cleanup for `runtime_sessions`, `client_sessions`, and the expanded call relationships in `prompt_harbor/retention.py` and `prompt_harbor/database.py`.
- [X] T015 Add schema, indexes, fresh-database, purge, and lifecycle fixture coverage in `tests/test_schema.py` and `tests/test_retention.py`.

---

## Phase 3: User Story 1 - Preserve concurrent forwarding and terminal audit convergence (Priority: P1) 🎯 MVP

**Goal**: Multiple client connections receive immediate, independent upstream results while audit records converge without per-delta writes or capture-induced cancellation.

**Independent Test**: Use the loopback fixture to send at least 20 interleaved streaming and non-streaming requests across four client identities; verify byte-visible responses, out-of-order completion, terminal records, and independent capture flags.

### Tests for User Story 1

- [X] T016 [P] [US1] Add an interleaved multi-connection forwarding test with four `session-id` values and out-of-order upstream completion in `tests/test_concurrency.py`.
- [X] T017 [P] [US1] Add SSE immediate-flush and final-single-snapshot assertions, including no per-delta payload rows, in `tests/test_concurrency.py`.
- [X] T018 [P] [US1] Add SQLite lock contention and bounded terminal retry tests that assert forwarding completes before audit retry exhaustion in `tests/test_concurrency.py`.
- [X] T019 [P] [US1] Add capture-budget tests for `response_complete`, `response_truncated`, and `capture_degraded` combinations and memory release in `tests/test_concurrency.py`.
- [X] T020 [P] [US1] Add post-commit invalidation ordering tests for calls and payloads in `tests/test_concurrency.py`.

### Implementation for User Story 1

- [X] T021 [US1] Route transparent and semantic response loops through `prompt_harbor/transport.py` while preserving immediate `wfile.flush()` behavior in `prompt_harbor/core.py`.
- [X] T022 [US1] Replace synchronous terminal writes with the bounded retry path from `prompt_harbor/storage.py` and `prompt_harbor/lifecycle.py`, ensuring the SSE write lock is never held during SQLite work in `prompt_harbor/core.py`.
- [X] T023 [US1] Persist one final bounded response snapshot and independent capture/transport facts in `prompt_harbor/core.py` and `prompt_harbor/lifecycle.py`.
- [X] T024 [US1] Make concurrent calls update their own runtime/client lifecycle rows and complete independently in `prompt_harbor/core.py` and `prompt_harbor/server.py`.
- [X] T025 [US1] Preserve localhost binding, hop-by-hop header filtering, Authorization exclusion, redirect handling, and transparent response bytes while integrating the new lifecycle in `prompt_harbor/core.py`.

**Checkpoint**: User Story 1 is independently demonstrable with concurrent transparent forwarding and a live process that reaches terminal audit state under bounded lock/capture pressure.

---

## Phase 4: User Story 2 - Distinguish runtime and client sessions (Priority: P1)

**Goal**: Every call has a stable client-session association and a process-local runtime-session association, with thread/request/provider context kept separate.

**Independent Test**: Send calls over separate connections and two gateway runs using repeated and distinct `session-id` values; verify grouping, restart reuse, default unresolved behavior, strict rejection, and independent context fields.

### Tests for User Story 2

- [X] T026 [P] [US2] Add identity precedence tests for `session-id`, supported metadata session, and default per-call `unresolved/ephemeral` creation in `tests/test_identity.py`.
- [X] T027 [P] [US2] Add strict-mode rejection tests proving unresolved requests are not forwarded upstream in `tests/test_identity.py`.
- [X] T028 [P] [US2] Add tests proving `thread-id`, `x-client-request-id`, `x-request-id`, and `traceparent` remain independent fields without cross-value validation in `tests/test_identity.py`.
- [X] T029 [P] [US2] Add concurrent explicit client-session upsert and cross-runtime restart grouping tests in `tests/test_identity.py`.
- [X] T030 [P] [US2] Add a regression test proving pi-ai `options.sessionId` never becomes the client-session fallback in `tests/test_pi_messages.py`.

### Implementation for User Story 2

- [X] T031 [US2] Implement header/body identity extraction and source precedence in `prompt_harbor/identity.py`; keep `session-id` unchanged on transparent upstream requests and strip only a PromptHarbor-private fallback header.
- [X] T032 [US2] Implement explicit client-session upsert and per-unresolved-call ephemeral rows with concurrency-safe uniqueness handling in `prompt_harbor/models.py` and `prompt_harbor/storage.py`.
- [X] T033 [US2] Integrate identity resolution before call start for `/v1/*` and `/messages` in `prompt_harbor/core.py`, associating every call with `runtime_session_id` and `client_session_row_id`.
- [X] T034 [US2] Persist `thread_id`, request correlation IDs, and provider/model-scoped session context in `prompt_harbor/lifecycle.py` and `prompt_harbor/core.py` without using them as identity keys.
- [X] T035 [US2] Apply strict/default identity settings through `prompt_harbor/config.py`, `prompt_harbor/server.py`, and CLI argument parsing in `prompt_harbor/core.py`.
- [X] T036 [US2] Update domain documentation and field comments for runtime/client identity semantics in `prompt_harbor/models.py` and `docs/SPEC.md`.

**Checkpoint**: User Stories 1 and 2 both pass independently; concurrent forwarding remains unchanged while calls are correctly grouped across connections and runtime restarts.

---

## Phase 5: User Story 3 - Inspect safe explicit resources and sidecar outcomes (Priority: P2)

**Goal**: Operators can inspect unambiguous runtime/client resources and safe payloads, while semantic payload and sidecar behavior follows the same audit lifecycle.

**Independent Test**: Exercise valid/invalid `/messages`, health/models probes, explicit API resources, CLI/UI views, and managed/external sidecar crash/ownership cases.

### Tests for User Story 3

- [X] T037 [P] [US3] Add HTTP contract tests for `/api/runtime-sessions`, `/api/client-sessions`, scoped `/api/calls` filters, explicit overview fields, and 404 `/api/sessions` in `tests/test_api_sessions.py`.
- [X] T038 [P] [US3] Add safe payload tests for valid and invalid `/messages`, ensuring sanitized outbound bytes and no unsanitized invalid body persistence in `tests/test_pi_messages.py`.
- [X] T039 [P] [US3] Add sidecar health/models exclusion, managed-child ownership, crash-during-stream, no-concatenated-retry, and transparent-path-availability tests in `tests/test_sidecar.py`.
- [X] T040 [P] [US3] Add CLI assertions for explicit runtime/client IDs, context fields, capture facts, and Authorization exclusion in `tests/test_cli.py`.
- [X] T041 [P] [US3] Update browser/static tests for separate Runtime Sessions and Client Sessions views and removed ambiguous resource usage in `tests/test_ui_static.py` and `browser-tests/audit.spec.js`.

### Implementation for User Story 3

- [X] T042 [US3] Implement explicit runtime/client session queries, call filters, safe details, and 404 removal of `/api/sessions` in `prompt_harbor/core.py` according to `contracts/http-api.md`.
- [X] T043 [US3] Update CLI `list` and `show` output and filters to show explicit runtime/client IDs, thread/request/provider context, safe payloads, usage, and capture facts in `prompt_harbor/cli.py` and `prompt_harbor/core.py`.
- [X] T044 [US3] Split UI API/store/realtime keys into Runtime Sessions and Client Sessions and update filtering/refetch behavior in `ui/js/api.js`, `ui/js/store.js`, and `ui/js/realtime.js`.
- [X] T045 [US3] Update UI tables, call detail, labels, and capture/transport status display for explicit scopes in `ui/js/views.js` and `ui/index.html`.
- [X] T046 [US3] Enforce safe outbound payload persistence and invalid semantic-body handling in `prompt_harbor/pi_messages.py` and `prompt_harbor/core.py`.
- [X] T047 [US3] Keep `/health` and `/models` outside the audit lifecycle while routing `/messages` through call/attempt/payload/usage persistence in `prompt_harbor/core.py` and `prompt_harbor/pi_messages.py`.
- [X] T048 [US3] Encode external versus gateway-managed sidecar ownership, active-stream crash failure, later re-probe behavior, and child-only reaping in `prompt_harbor/sidecar.py`, `prompt_harbor/server.py`, and `prompt_harbor/core.py`.
- [X] T049 [US3] Emit explicit resource invalidations only after committed runtime/client/call changes in `prompt_harbor/events.py` and `prompt_harbor/core.py`.

**Checkpoint**: All user stories are independently queryable/testable; explicit API/UI/CLI semantics and sidecar ownership match the contracts without weakening transparent forwarding.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Harden the integrated feature, complete documentation, and validate the quickstart end to end.

- [X] T050 [P] Update `docs/SPEC.md`, `docs/UI_SPEC.md`, and `docs/TEST_MATRIX.md` with runtime/client session terminology, API removal, capture flags, and concurrency acceptance cases.
- [X] T051 [P] Add module-level compatibility imports and concise comments for the lifecycle seams in `prompt_harbor/core.py`, `prompt_harbor/models.py`, and `prompt_harbor/server.py`.
- [X] T052 Refactor remaining identity, lifecycle, storage, transport, event, and sidecar logic out of `prompt_harbor/core.py` only where the new tests cover the seam.
- [X] T053 Run `uv run pytest -q` and the browser suite from `playwright.config.js`; resolve regressions while preserving loopback/security constraints.
- [X] T054 Run every scenario in `specs/015-audit-gateway-concurrency/quickstart.md` against a temporary database and record evidence in `iteration/iteration-15/PROGRESS.md`.
- [X] T055 Update `iteration/iteration-15/PROGRESS.md`, `iteration/iteration-15/BLOCKED.md`, and `iteration/INDEX.md` with implementation evidence and final status.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No implementation dependency; establishes seams and fixtures.
- **Foundational (Phase 2)**: Depends on Setup and blocks all user stories because every story uses the explicit schema, lifecycle, retry, and capture contracts.
- **User Story 1 (Phase 3)**: Depends on Foundational; is the MVP because it protects the primary forwarding behavior.
- **User Story 2 (Phase 4)**: Depends on Foundational and integrates with US1 lifecycle start; identity tests can begin after T005-T010.
- **User Story 3 (Phase 5)**: Depends on the lifecycle fields from Foundational and the session associations from US2; API/UI/sidecar surfaces can then be delivered independently by file area.
- **Polish (Phase 6)**: Depends on all desired stories and their focused tests.

### User Story Dependencies

- **US1 (P1)**: Foundational only; independently validates transparent forwarding and audit convergence.
- **US2 (P1)**: Foundational plus the call-start seam from US1; independently validates identity and cross-runtime grouping.
- **US3 (P2)**: Foundational plus US2's explicit fields; independently validates API/UI/CLI/payload/sidecar behavior once those fields exist.

### Parallel Opportunities

- T002-T004 can run in parallel after the module skeleton is created.
- T008-T009, T013-T014 can run in parallel within the foundational phase because they touch separate modules.
- T016-T020 are independent US1 tests and can be authored in parallel.
- T026-T030 are independent US2 identity/provider tests and can be authored in parallel.
- T037-T041 are independent US3 contract/UI/sidecar tests and can be authored in parallel.
- T050-T051 can run in parallel with final decomposition review.

## Parallel Example: User Story 1

```text
Task: "Add interleaved multi-connection forwarding test in tests/test_concurrency.py"
Task: "Add SSE final-snapshot and no-per-delta assertions in tests/test_concurrency.py"
Task: "Add SQLite lock/retry ordering tests in tests/test_concurrency.py"
Task: "Add capture-budget flag and memory-release tests in tests/test_concurrency.py"
```

## Implementation Strategy

### MVP First

1. Complete Setup and Foundational phases.
2. Complete US1, including the 20-call interleaving fixture and terminal retry/capture tests.
3. Stop and validate transparent forwarding, SSE flushing, and audit convergence independently.

### Incremental Delivery

1. Add US2 identity resolution and cross-runtime grouping without changing client-visible forwarding.
2. Add US3 explicit API/CLI/UI resources and safe semantic payload behavior.
3. Align sidecar lifecycle and complete cross-cutting documentation/quickstart validation.

### Notes

- Every task uses the required `- [ ] Txxx` checklist format.
- `[P]` is used only where the task can touch a separate file area without an incomplete dependency.
- `[US1]`, `[US2]`, and `[US3]` map directly to the stories in `spec.md`.
- Tests are included because the feature specification defines independent acceptance scenarios and measurable regression outcomes.
