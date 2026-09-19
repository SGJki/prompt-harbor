import sqlite3
import json

from test_ui_static import get, post_call, start_gateway

from prompt_harbor import core


def test_explicit_session_schema_and_indexes(tmp_path):
    path = tmp_path / "sessions.db"
    core.init(str(path))
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        calls = {row[1] for row in connection.execute("PRAGMA table_info(calls)")}
        payloads = {row[1] for row in connection.execute("PRAGMA table_info(payloads)")}
        assert {"runtime_sessions", "client_sessions"} <= tables
        assert {"runtime_session_id", "client_session_row_id", "thread_id", "request_correlation_id", "provider_session_context"} <= calls
        assert "capture_degraded" in payloads


def test_explicit_client_session_upsert_is_stable(tmp_path):
    path = tmp_path / "sessions.db"
    core.init(str(path))
    from prompt_harbor.models import resolve_client_session
    with sqlite3.connect(path) as connection:
        first = resolve_client_session(connection, "client-a", "explicit", "session-id")
        second = resolve_client_session(connection, "client-a", "explicit", "session-id")
        assert first == second
        assert connection.execute("SELECT COUNT(*) FROM client_sessions").fetchone()[0] == 1


def test_explicit_http_resources_filters_and_remove_ambiguous_path(tmp_path):
    process, port, database, upstream = start_gateway(tmp_path)
    try:
        for session in ("api-a", "api-b", "api-a"):
            import http.client
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request("POST", "/v1/responses", b'{"model":"api"}', {"session-id": session})
            response = connection.getresponse(); response.read(); connection.close()
        status, _, body = get(port, "/api/client-sessions?identity_status=explicit&limit=1")
        payload = json.loads(body)
        assert status == 200 and len(payload["client_sessions"]) == 1
        client_id = payload["client_sessions"][0]["client_session_id"]
        status, _, body = get(port, f"/api/calls?client_session_id={client_id}&status=succeeded&limit=1")
        assert status == 200 and len(json.loads(body)["calls"]) == 1
        status, _, _ = get(port, "/api/sessions")
        assert status == 404
    finally:
        process.terminate(); process.wait(timeout=3); upstream.shutdown()
