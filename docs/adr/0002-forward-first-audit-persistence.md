# Forward First, Persist the Final Audit Snapshot

Transparent forwarding has priority over waiting for audit writes. PromptHarbor records the call start, forwards response bytes immediately without per-SSE-delta transactions, and persists the final attempt state and response snapshot after forwarding; transient database failures are retried while the process remains alive, and a crash may lose events that were not yet persisted.

**Consequences**

- A normal live process must eventually move every started call to a terminal audit state.
- SSE timing is independent of SQLite commit latency.
- Response capture is bounded by the configured limit; oversized responses retain the allowed snapshot prefix and truncation metadata.
