"""Lifecycle state constants and terminal persistence helpers."""

from __future__ import annotations

from typing import Any, Mapping, Optional


TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "incomplete"})


def call_outcome(*, status: Optional[int], response_complete: bool, protocol_error: bool = False, cancelled: bool = False) -> str:
    if cancelled:
        return "cancelled"
    if not response_complete:
        return "incomplete"
    if protocol_error or status is None or status >= 400:
        return "failed"
    return "succeeded"


def terminal_values(record: Mapping[str, Any], done: str) -> tuple[Any, ...]:
    return (
        done,
        record.get("state"),
        record.get("status"),
        record.get("response_headers_json"),
        record.get("first_iso"),
        record.get("duration_ms"),
        record.get("total_out", 0),
        record.get("error_type"),
        record.get("error_message"),
    )
