"""Client identity and request-context extraction for audit calls."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class IdentityResolution:
    client_session_id: str
    identity_status: str
    identity_source: str
    thread_id: Optional[str] = None
    request_correlation_id: Optional[str] = None
    provider_session_context: Optional[str] = None


class IdentityRequiredError(ValueError):
    """Raised when strict mode cannot resolve a client session identity."""


def _header(headers: Any, name: str) -> Optional[str]:
    value = headers.get(name) if hasattr(headers, "get") else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _metadata_session(parsed: Any) -> Optional[str]:
    if not isinstance(parsed, Mapping):
        return None
    metadata = parsed.get("metadata")
    candidates = [metadata, parsed.get("context", {}).get("metadata") if isinstance(parsed.get("context"), Mapping) else None]
    for value in candidates:
        if isinstance(value, Mapping):
            for key in ("session_id", "session-id", "sessionId"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
    for key in ("session_id", "session-id"):
        candidate = parsed.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def resolve_identity(headers: Any, body: bytes = b"", *, strict: bool = False) -> IdentityResolution:
    """Resolve stable client identity without using provider session options."""
    explicit = _header(headers, "session-id")
    parsed: Any = None
    if body:
        try:
            parsed = json.loads(body)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
    if explicit:
        source = "session-id"
        status = "explicit"
        client_id = explicit
    else:
        metadata = _metadata_session(parsed)
        if metadata:
            source = "metadata"
            status = "explicit"
            client_id = metadata
        elif strict:
            raise IdentityRequiredError("client session identity is required")
        else:
            source = "unresolved"
            status = "unresolved/ephemeral"
            client_id = f"unresolved-{uuid.uuid4().hex}"
    thread_id = _header(headers, "thread-id")
    request_correlation_id = _header(headers, "x-client-request-id") or _header(headers, "x-request-id") or _header(headers, "traceparent")
    provider_context = None
    if isinstance(parsed, Mapping):
        options = parsed.get("options")
        if isinstance(options, Mapping) and isinstance(options.get("sessionId"), str):
            provider_context = options["sessionId"]
    return IdentityResolution(client_id, status, source, thread_id, request_correlation_id, provider_context)
