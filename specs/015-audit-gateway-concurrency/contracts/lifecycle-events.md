# Lifecycle and Persistence Contract

## Start

1. Resolve or create the `ClientSession`.
2. In a short transaction, update `RuntimeSession.last_seen_at`, insert `Call` and first `Attempt`, and persist the bounded safe request payload.
3. Commit before or while the upstream request is established; a failure to create the start record is handled according to the configured audit failure policy without delaying transparent forwarding beyond the minimum required boundary.

## Forward

- Forward transparent bytes and headers without changing client-visible semantics.
- Flush every SSE chunk immediately.
- Accumulate only the bounded final response snapshot while capture budget permits.
- Do not write a SQLite row per delta or wait on terminal audit writes.

## Finish

1. Determine attempt and call outcome from transport/protocol facts, not HTTP status alone.
2. In a bounded retry loop/worker, commit attempt, call, payload snapshot, and usage in one short terminal transaction.
3. Emit `change_log`/SSE invalidation only after commit succeeds.
4. Release retained response bytes after terminal persistence succeeds. If persistence remains unavailable, retain only the bounded retry record permitted by the capture budget and preserve the forward result.

## Failure boundaries

- Process crash may lose records not yet committed.
- SQLite busy/locked errors are retried with bounded backoff; they must not hold an SSE client write lock.
- Capture budget exhaustion sets `capture_degraded` and never cancels forwarding.
- Sidecar crash marks the active attempt/call incomplete or failed with sidecar error metadata and never starts a second stream for the same client request.
