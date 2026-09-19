# PromptHarbor Domain

PromptHarbor is a local audit gateway whose primary responsibility is preserving client behavior while recording the request lifecycle. Protocol adapters, provider runtimes, and the audit UI are extensions around that core.

## Core Lifecycle

**Runtime Session**:
A gateway run created when one gateway process starts. Calls received during that run share the runtime session; it is not a client's conversational session.

**Client Session**:
One logical Codex client context whose calls must be grouped and distinguished from calls belonging to other client contexts. Every call belongs to a client session, and that client session may span multiple runtime sessions.

**Client Session ID**:
The client-provided opaque identifier used to group calls across connections and runtime sessions. Codex's `session-id` is the primary candidate; this value is a correlation identity, not an authorization credential, and must be distinguished from any database row ID.

**Unresolved Client Session**:
A local client-session record created when a call has no reliable client identity. It keeps the call associated with a session without claiming that later calls belong to the same client context.

**Thread Context**:
The conversation or work-thread identifier carried by a client request. It is related context for analysis and is distinct from Client Session Identity.

**Provider Session Context**:
A session-like value carried inside a semantic provider request. It belongs to the provider runtime's protocol context, is scoped by provider/model behavior, and does not become a Client Session ID.

**Request Correlation ID**:
An identifier for one request or trace, such as `x-client-request-id`, `x-request-id`, or `traceparent`. It must not be used as a long-lived Client Session Identity.

**Session**:
An ambiguous shorthand that should be avoided in new design discussions. Use `Runtime Session` or `Client Session` explicitly.

**Call**:
One request received by the gateway from a client. A call exists whether its execution succeeds, fails, is cancelled, or ends incomplete.

**Attempt**:
One execution of a call against one upstream target. A call may have multiple attempts when retry or routing behavior is introduced.

**Payload**:
The bounded request and response byte snapshot associated with an attempt. Payload capture describes what can be audited and is separate from transport success.

For a request, the canonical payload is the safe byte sequence actually sent to the upstream target. A transparent path therefore records the client's bytes; a semantic path records its sanitized outbound bytes.

**Response Snapshot**:
The final response bytes assembled for an attempt and persisted when the forwarding lifecycle ends. SSE deltas are forwarding events, not separate audit records; a snapshot is complete only within the configured capture limit.

**Transport Completeness**:
Whether the upstream response reached its protocol-defined end. It is independent from whether the persisted Response Snapshot was truncated by the capture limit.

**Usage**:
The model usage result associated with an attempt. Usage is a semantic result and is distinct from HTTP status, stream completeness, and transport errors.

**Call Outcome**:
The result observed by the client after the call's forwarding lifecycle completes. A call succeeds only when the client receives a complete successful result; individual attempts retain their own transport and provider outcomes.

## Boundaries

**Audit Gateway**:
The primary product boundary responsible for local client ingress, transparent forwarding, lifecycle recording, and preserving observable client behavior.

**Transparent Path**:
The OpenAI-compatible path that forwards a client's request and response without changing their protocol meaning.

**Provider Runtime**:
The optional runtime responsible for model catalogs, provider credentials, provider protocol translation, and provider-generated stream events. It does not own the gateway's audit lifecycle.

**Sidecar Ownership**:
The runtime relationship in which PromptHarbor either connects to an externally managed sidecar or owns a sidecar child process. Ownership changes are explicit lifecycle boundaries, not ordinary request-time routing.

**Semantic Path**:
A protocol path whose request and response semantics are produced by a provider runtime rather than transparently forwarded from the client's upstream protocol.

**Audit UI**:
The local read and configuration surface for audit data. It presents gateway-owned state and does not decide call or attempt outcomes.

**Runtime Session View**:
A view of gateway process runs, their timing, and their aggregate calls. It must not be presented as a user's client session.

**Client Session View**:
A view of logical client sessions and the calls grouped under them across runtime sessions.

**Identity Status**:
The confidence category for a Client Session Identity, such as explicit or unresolved. It describes how the gateway obtained the identity and is never an authorization decision.

**Capture Degradation**:
A condition in which forwarding continues but response capture stops early because the process-wide concurrent capture budget was reached. It is distinct from an oversized response and from incomplete upstream transport.
