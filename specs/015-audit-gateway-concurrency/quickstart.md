# Quickstart Validation

This guide validates the planned behavior after implementation. It is intentionally a runbook rather than an implementation recipe.

## Prerequisites

- Python environment with the repository dependencies installed (`uv sync` if the environment is not already prepared).
- A loopback-only upstream fixture capable of returning JSON and interleaved SSE responses.
- A temporary SQLite path and, for sidecar scenarios, the existing local pi-ai sidecar fixture.

## Baseline regression

From the repository root:

```bash
uv run pytest -q
```

Expected: all existing tests pass, including localhost binding, authorization exclusion, immediate SSE flushing, UI static checks, and sidecar configuration tests.

## Schema and identity smoke test

```bash
uv run prompt-harbor init --database /tmp/prompt-harbor-plan.db
```

Inspect the database and verify that it contains `runtime_sessions`, `client_sessions`, `calls.runtime_session_id`, `calls.client_session_row_id`, and the independent thread/request/capture fields described in [data-model.md](data-model.md). Start two gateway runs against the same database and send calls with two distinct `session-id` headers.

Expected:

- Each process run has a distinct runtime session.
- Calls with the same `session-id` share one client session across runs.
- Calls with different `session-id` values remain separate.
- A call without reliable identity creates an unresolved/ephemeral client session in default mode and is rejected in strict mode.
- `options.sessionId` does not change client-session grouping.

## Concurrent forwarding and capture

1. Start a loopback upstream fixture that emits at least 20 interleaved streaming/non-streaming responses across four client session IDs.
2. Run the gateway with a small per-call capture limit and a process-wide capture budget lower than the combined stream sizes.
3. Capture client-visible bytes and timestamps while polling `/api/events`.

Expected:

- Every SSE chunk is flushed without waiting for SQLite terminal persistence.
- Calls complete in whichever order the upstream finishes.
- No call is associated with another client session.
- Some records may contain `response_truncated` or `capture_degraded`, but forwarding still succeeds.
- Terminal invalidation is observed only after the corresponding call is queryable.

## Explicit audit API

```bash
curl -s http://127.0.0.1:8787/api/runtime-sessions
curl -s http://127.0.0.1:8787/api/client-sessions
curl -s 'http://127.0.0.1:8787/api/calls?client_session_id=<opaque-id>'
curl -s http://127.0.0.1:8787/api/calls/<call-id>
curl -i http://127.0.0.1:8787/api/sessions
```

Expected: the first four responses use explicit fields and filters; the last request returns 404. The call detail contains a single final response snapshot, safe headers, usage where available, and independent completeness/truncation/degradation fields.

## Semantic payload safety

Send a valid `/messages` request, an invalid JSON `/messages` body, and `/health`/`/models` probes to the sidecar path.

Expected:

- Valid `/messages` creates the normal call/attempt/payload/usage lifecycle and stores the sanitized outbound body.
- Invalid semantic JSON creates failure metadata without storing unsanitized raw bytes.
- Health and model probes do not create audit calls.
- Authorization and callback capabilities are absent from persisted request bytes and headers.

## Sidecar ownership and crash

Exercise one externally configured loopback sidecar URL and one gateway-managed child process. Stop the managed child during an active stream, then issue a later request.

Expected:

- The gateway reaps only the child it started.
- The active call fails once with sidecar crash/incomplete metadata and no concatenated retry stream.
- A later request may re-probe/restart according to ownership policy.
- Transparent `/v1/*` remains available when the sidecar is unavailable.

## CLI/UI verification

Run `list` and `show <call-id>`, open the local audit UI, switch between Runtime Sessions and Client Sessions, and filter calls by each scope. Verify that labels never present a runtime ID as an unqualified session ID and that updates arrive through explicit invalidation resources.
