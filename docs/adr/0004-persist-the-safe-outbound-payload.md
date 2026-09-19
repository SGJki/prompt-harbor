# Persist the Safe Outbound Payload

`payload.request_body` represents the bounded bytes actually sent to the upstream target. Transparent paths therefore preserve the client bytes, while `/messages` persists the sanitized body after provider credentials and callback capabilities are removed; malformed semantic input is recorded as a failure without persisting an unsanitized body.

**Consequences**

- The audit payload matches the request the upstream could observe.
- A future raw-ingress audit field must be explicitly sanitized and separately named.
- Response SSE deltas are assembled in memory and persisted as one bounded final snapshot, never as per-delta database records.
