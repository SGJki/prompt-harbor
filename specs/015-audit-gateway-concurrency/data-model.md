# Data Model: Audit Gateway Session and Concurrency Evolution

The model keeps the existing lifecycle language while making the two session scopes explicit:

```text
runtime_session 1 ─── * call * ─── 1 client_session
                         |
                         1 ─── * attempt 1 ─── 0..1 payload_snapshot
                                             └── 0..1 usage
```

## RuntimeSession

Represents one gateway process run. It is local to one process lifetime and is never presented as a user's client session.

| Field | Type | Rules |
| --- | --- | --- |
| `id` | integer PK | Internal row ID. |
| `agent` | text | Existing agent label, normally `codex`. |
| `started_at` | timestamp | Required; set once at process start. |
| `last_seen_at` | timestamp | Updated by started calls and lifecycle maintenance. |
| `cwd` | text | Gateway working directory. |
| `project_name` | text nullable | Optional project context. |
| `metadata_json` | text | JSON object; must not contain Authorization credentials. |

## ClientSession

Represents a logical client context that can span TCP connections and runtime sessions.

| Field | Type | Rules |
| --- | --- | --- |
| `id` | integer PK | Internal row ID used by foreign keys. |
| `client_session_id` | text | Opaque client-provided identity; unique for explicit identities. |
| `identity_status` | text | `explicit` or `unresolved/ephemeral`. |
| `identity_source` | text | `session-id`, supported metadata field, or `unresolved`. |
| `first_seen_at` | timestamp | Required. |
| `last_seen_at` | timestamp | Updated when a call is associated. |
| `metadata_json` | text | Optional safe identity metadata. |

An unresolved record is intentionally not treated as a stable claim that later unresolved calls belong to the same client. The default implementation creates a distinct ephemeral `ClientSession` for each call that lacks reliable identity, so separate unknown callers are never silently merged; strict mode rejects that call instead.

## Call

Represents one inbound request and its aggregate client-visible outcome.

| Field | Type | Rules |
| --- | --- | --- |
| `id` | integer PK | Stable audit identifier. |
| `runtime_session_id` | FK | Required; points to the process run. |
| `client_session_row_id` | FK | Required; points to `ClientSession.id`. |
| `thread_id` | text nullable | Independent thread context. Never compared to `client_session_id`. |
| `request_correlation_id` | text nullable | `x-client-request-id`, `x-request-id`, or `traceparent` value. |
| `provider_session_context` | text nullable | Provider/model-scoped context, such as pi-ai `options.sessionId`; not client identity. |
| `created_at`, `completed_at` | timestamps | Start and terminal times. |
| `provider`, `api_family`, `endpoint`, `model` | text | Routing and protocol facts. |
| `stream` | integer boolean | Whether the request asks for streaming. |
| `status` | text | `running`, `succeeded`, `failed`, `cancelled`, or `incomplete`. |
| `status_code` | integer nullable | HTTP fact; does not alone determine business status. |
| `first_byte_at`, `duration_ms` | timestamp/integer nullable | Transport timing. |
| `input_bytes`, `output_bytes` | integer | Full transport byte counts, independent from snapshot limits. |
| `error_type`, `error_message` | text nullable | Safe failure metadata. |

Recommended indexes: `calls(runtime_session_id, created_at)`, `calls(client_session_row_id, created_at)`, `calls(thread_id)`, and `calls(request_correlation_id)`.

## Attempt

Represents one provider execution. A call may have multiple attempts in future work; the first implementation creates one attempt while retaining the separation.

| Field | Type | Rules |
| --- | --- | --- |
| `id` | integer PK | Stable attempt identifier. |
| `call_id` | FK | Required. |
| `attempt_no` | integer | Monotonic per call. |
| `started_at`, `completed_at` | timestamps | Attempt lifecycle. |
| `upstream_url` | text | Target origin/path without credentials. |
| `status`, `status_code` | text/integer nullable | Attempt transport/provider outcome. |
| `request_headers_json`, `response_headers_json` | text | Sanitized headers; Authorization excluded. |
| `first_byte_at`, `duration_ms` | timestamp/integer nullable | Attempt timing. |
| `input_bytes`, `output_bytes` | integer | Transport counts. |
| `error_type`, `error_message` | text nullable | Attempt-specific failure. |

## PayloadSnapshot

One bounded request/response snapshot per attempt.

| Field | Type | Rules |
| --- | --- | --- |
| `id` | integer PK | Stable payload row. |
| `attempt_id` | unique FK | Exactly one snapshot row per attempt. |
| `request_body` | blob nullable | Safe outbound bytes, bounded by request capture limit. Invalid semantic input is null/absent rather than raw unsanitized bytes. |
| `response_body` | blob nullable | Final bounded response snapshot; never one row per SSE delta. |
| `request_content_type`, `response_content_type` | text nullable | Wire metadata. |
| `response_complete` | integer boolean | Upstream transport reached its protocol-defined end. |
| `response_truncated` | integer boolean | Snapshot exceeded the per-call capture limit. |
| `capture_degraded` | integer boolean | Shared concurrent capture budget stopped capture early. |
| `request_truncated` | integer boolean | Request snapshot exceeded its limit. |
| `created_at`, `updated_at` | timestamps | Snapshot lifecycle. |

## Usage

One normalized semantic usage result per attempt, with provider-specific raw usage retained in a safe JSON field. Usage is independent from HTTP status and transport completeness.

## SidecarInstance

Runtime state rather than a user call entity.

| Field/attribute | Rules |
| --- | --- |
| `ownership` | `external` or `gateway-managed`. |
| `url` | Validated loopback URL. |
| `process` | Present only for a child started by the gateway. |
| `readiness` | Startup/health outcome; health/models probes do not create calls. |
| `crash` | Captured as active-call failure metadata; no in-stream retry. |

## State transitions

### Call and attempt

```text
created -> running -> succeeded
                  -> failed
                  -> cancelled
                  -> incomplete
```

The terminal state is written after response bytes have been flushed to the client. A call may be `failed` even with a non-error HTTP status when the upstream response is incomplete or the provider protocol reports an error. A future multi-attempt aggregator will use the final complete client-visible result for the call outcome while retaining each attempt's state.

### Capture facts

`response_complete`, `response_truncated`, and `capture_degraded` are independent booleans. For example, a fully delivered upstream response may have a truncated snapshot; a complete response may have degraded capture; an incomplete upstream response may have neither capture flag.

### Identity resolution

```text
session-id header -> explicit client session
supported metadata session -> explicit client session
no reliable identity + default -> unresolved/ephemeral client session
no reliable identity + strict -> request rejected before forwarding
```

`thread-id`, request/trace IDs, and provider session context are recorded alongside the call and never used as fallback identity.
