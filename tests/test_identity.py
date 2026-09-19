import json

import pytest
import sqlite3
import threading

from prompt_harbor.identity import IdentityRequiredError, resolve_identity


def test_session_header_wins_and_context_stays_separate():
    result = resolve_identity(
        {"session-id": "client-a", "thread-id": "thread-a", "x-client-request-id": "request-a"},
        json.dumps({"options": {"sessionId": "provider-a"}}).encode(),
    )
    assert result.client_session_id == "client-a"
    assert result.identity_source == "session-id"
    assert result.thread_id == "thread-a"
    assert result.request_correlation_id == "request-a"
    assert result.provider_session_context == "provider-a"


def test_metadata_identity_is_used_before_unresolved():
    result = resolve_identity({}, b'{"metadata":{"session_id":"metadata-a"}}')
    assert result.client_session_id == "metadata-a"
    assert result.identity_source == "metadata"
    assert result.identity_status == "explicit"


def test_provider_session_context_never_becomes_identity():
    result = resolve_identity({}, b'{"options":{"sessionId":"provider-only"}}')
    assert result.identity_source == "unresolved"
    assert result.client_session_id != "provider-only"
    assert result.provider_session_context == "provider-only"


def test_strict_mode_rejects_missing_identity():
    with pytest.raises(IdentityRequiredError, match="client session identity"):
        resolve_identity({}, b'{"options":{"sessionId":"provider-only"}}', strict=True)


def test_concurrent_explicit_upsert_is_unique(tmp_path):
    from prompt_harbor.core import init
    from prompt_harbor.models import resolve_client_session
    path = tmp_path / "identity.db"
    init(str(path))
    barrier = threading.Barrier(4)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        with sqlite3.connect(path, timeout=2) as connection:
            row_id = resolve_client_session(connection, "stable", "explicit", "session-id")
            connection.commit()
        with lock:
            results.append(row_id)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(results)) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("select count(*) from client_sessions where client_session_id='stable'").fetchone() == (1,)
