# Make Sidecar Ownership Explicit

PromptHarbor treats an external sidecar URL and a gateway-managed sidecar process as different ownership modes. Health and model-directory probes describe provider runtime readiness outside the call audit lifecycle; `/messages` calls use the normal audit model. A sidecar crash during an active stream fails that call and never triggers an in-stream retry or response concatenation.

**Consequences**

- URL changes affect subsequent requests only; command, token, and process-lifecycle changes require restart.
- The gateway reaps only sidecars it started.
- Runtime health can be degraded while the transparent `/v1/*` path remains available.
