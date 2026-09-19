"""Post-commit resource invalidation dispatch."""

from __future__ import annotations

from typing import Iterable, Callable


def notify_after_commit(notify: Callable[[set[str]], None], resources: Iterable[str]) -> None:
    """Call the notification sink only after the caller's transaction commits."""
    notify(set(resources))


def commit_then_notify(connection, notify: Callable[[set[str]], None], resources: Iterable[str]) -> None:
    """Commit a completed transaction before publishing advisory invalidations."""
    connection.commit()
    notify_after_commit(notify, resources)
