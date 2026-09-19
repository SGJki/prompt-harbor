# Keep Provider Session Context Separate from Client Sessions

`/messages` `options.sessionId` remains provider session context rather than PromptHarbor client identity. pi-ai providers use the value inconsistently for prompt-cache keys, session-affinity headers, routing, connection reuse, or nothing at all, so PromptHarbor stores it with provider/model provenance and never uses it as a fallback for `client_session_id`.

**Consequences**

- Client session grouping requires the Codex `session-id` header or an explicit supported metadata source.
- Provider context can be inspected for audit and debugging without claiming a cross-provider user-session meaning.
- Missing client identity still follows the unresolved/strict-mode policy instead of silently adopting a provider cache key.
