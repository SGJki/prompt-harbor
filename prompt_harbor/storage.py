"""Small SQLite concurrency primitives used by the gateway."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

T = TypeVar("T")


def configure(connection: sqlite3.Connection, timeout: float) -> sqlite3.Connection:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(f"PRAGMA busy_timeout={max(1, int(timeout * 1000))}")
    connection.row_factory = sqlite3.Row
    return connection


def retryable(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and any(token in str(exc).lower() for token in ("locked", "busy"))


def with_retry(operation: Callable[[], T], *, attempts: int = 5, initial_delay: float = 0.01) -> T:
    delay = initial_delay
    for index in range(max(1, attempts)):
        try:
            return operation()
        except Exception as exc:
            if index + 1 >= attempts or not retryable(exc):
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.25)
    raise RuntimeError("unreachable")


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
