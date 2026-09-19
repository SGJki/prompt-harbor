"""Bounded response capture that never controls client forwarding."""

from __future__ import annotations

import threading


class CaptureBudget:
    def __init__(self, limit: int) -> None:
        self.limit = max(1, int(limit))
        self._used = 0
        self._lock = threading.Lock()

    def reserve(self, amount: int) -> bool:
        amount = max(0, int(amount))
        with self._lock:
            if self._used + amount > self.limit:
                return False
            self._used += amount
            return True

    def release(self, amount: int) -> None:
        with self._lock:
            self._used = max(0, self._used - max(0, int(amount)))

    @property
    def used(self) -> int:
        with self._lock:
            return self._used


def capture_chunk(record: dict, chunk: bytes, budget: CaptureBudget | None = None) -> None:
    if not chunk:
        return
    if record.get("first") is None:
        import time
        record["first"] = time.time()
    record["total_out"] = record.get("total_out", 0) + len(chunk)
    remaining = max(0, record["limit"] - len(record["out"]))
    if remaining:
        accepted = min(remaining, len(chunk))
        if budget is None or budget.reserve(accepted):
            record["out"].extend(chunk[:accepted])
        else:
            record["capture_degraded"] = True
    if record["total_out"] > record["limit"]:
        record["response_truncated"] = True
