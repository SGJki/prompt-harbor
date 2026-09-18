"""Helpers for the pi-messages wire protocol."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


def split_model_id(model: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return the provider and provider-local id from a namespaced model id."""
    if not isinstance(model, str) or "/" not in model:
        return None, model if isinstance(model, str) else None
    provider, model_id = model.split("/", 1)
    if not provider or not model_id:
        return None, model
    return provider, model_id


def normalize_usage(usage: Any) -> Optional[Dict[str, Any]]:
    """Map pi-ai camelCase usage names to the existing SQLite usage columns."""
    if not isinstance(usage, dict):
        return None
    result = dict(usage)
    aliases = {
        "input_tokens": ("input_tokens", "input", "prompt_tokens"),
        "output_tokens": ("output_tokens", "output", "completion_tokens"),
        "total_tokens": ("total_tokens", "totalTokens"),
    }
    for target, candidates in aliases.items():
        if target not in result:
            for candidate in candidates:
                if candidate in usage:
                    result[target] = usage[candidate]
                    break
    return result


@dataclass
class PiMessagesState:
    """Incrementally tracks protocol termination while bytes are forwarded."""

    buffer: bytearray = field(default_factory=bytearray)
    terminal_type: Optional[str] = None
    terminal_reason: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    raw_usage: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    protocol_error: Optional[str] = None

    def feed(self, chunk: bytes) -> None:
        if self.terminal_type is not None or not chunk:
            return
        self.buffer.extend(chunk.replace(b"\r\n", b"\n"))
        while b"\n\n" in self.buffer:
            raw, remainder = self.buffer.split(b"\n\n", 1)
            self.buffer = bytearray(remainder)
            self._consume_frame(bytes(raw))

    def finish(self) -> None:
        if self.buffer.strip() and self.terminal_type is None:
            self._consume_frame(bytes(self.buffer))
            self.buffer.clear()

    def _consume_frame(self, raw: bytes) -> None:
        data_lines = []
        for line in raw.split(b"\n"):
            if line.startswith(b"data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            return
        encoded = b"\n".join(data_lines).strip()
        if not encoded or encoded == b"[DONE]":
            return
        try:
            event = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.protocol_error = f"invalid pi-messages event: {exc}"
            return
        if not isinstance(event, dict):
            self.protocol_error = "pi-messages event must be a JSON object"
            return
        event_type = event.get("type")
        if event_type not in {"done", "error"}:
            return
        self.terminal_type = event_type
        self.terminal_reason = event.get("reason") if isinstance(event.get("reason"), str) else None
        raw_usage = event.get("usage")
        self.raw_usage = dict(raw_usage) if isinstance(raw_usage, dict) else None
        self.usage = normalize_usage(raw_usage)
        if isinstance(event.get("errorMessage"), str):
            self.error_message = event["errorMessage"]

    @property
    def complete(self) -> bool:
        return self.terminal_type in {"done", "error"}

    @property
    def succeeded(self) -> bool:
        return self.terminal_type == "done" and self.protocol_error is None
