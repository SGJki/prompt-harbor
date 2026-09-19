# Separate Runtime and Client Sessions

PromptHarbor separates the gateway process run from the logical Codex client session. Existing runtime-session data remains the process-level audit context, while a new client-session identity groups calls across connections and gateway restarts; calls reference both. This avoids conflating concurrent Codex sessions and makes the existing `/api/sessions` ambiguity explicit rather than preserving it as a compatibility contract.

**Consequences**

- `client_sessions` is a first-class domain record and calls carry both runtime and client session references.
- `session-id` is the primary external identity; missing identities use an `unresolved/ephemeral` record by default, with a strict rejection mode available.
- The ambiguous `/api/sessions` resource is removed in favor of explicit runtime-session and client-session resources.
