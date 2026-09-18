import http.client
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import prompt_harbor as canonical
from prompt_harbor import database, retention, server, usage
from prompt_harbor.config import ConfigError, validate_listen
from prompt_harbor.core import Handler

ROOT = Path(__file__).parents[1]


class TinyUpstream(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until(predicate, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for condition")


def start_local(tmp_path, *, interval=86400, keepalive=0.05):
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), TinyUpstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    path = tmp_path / "iteration3.db"
    gateway = server.serve(
        f"127.0.0.1:0",
        f"http://127.0.0.1:{upstream.server_port}",
        str(path),
        retention_days=1,
        purge_interval=interval,
    )
    Handler.sse_keepalive = keepalive
    thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    thread.start()
    return gateway, thread, upstream, path


def stop_local(gateway, upstream):
    gateway.shutdown()
    gateway.server_close()
    upstream.shutdown()


def test_periodic_purge_cascades_and_server_stays_live(tmp_path):
    gateway, thread, upstream, path = start_local(tmp_path, interval=0.03)
    try:
        old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        recent = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(path) as connection:
            sid = connection.execute(
                "INSERT INTO sessions(agent,started_at,last_seen_at) VALUES(?,?,?)", ("old", old, old)
            ).lastrowid
            cid = connection.execute(
                "INSERT INTO calls(session_id,created_at,status) VALUES(?,?,?)", (sid, old, "succeeded")
            ).lastrowid
            aid = connection.execute(
                "INSERT INTO attempts(call_id,attempt_no,status) VALUES(?,?,?)", (cid, 1, "succeeded")
            ).lastrowid
            connection.execute("INSERT INTO payloads(attempt_id,request_body) VALUES(?,?)", (aid, b"old"))
            connection.execute("INSERT INTO usage(attempt_id,total_tokens) VALUES(?,?)", (aid, 3))
            recent_sid = connection.execute(
                "INSERT INTO sessions(agent,started_at,last_seen_at) VALUES(?,?,?)", ("recent", recent, recent)
            ).lastrowid
            connection.execute(
                "INSERT INTO calls(session_id,created_at,status) VALUES(?,?,?)", (recent_sid, recent, "succeeded")
            )

        wait_until(lambda: sqlite3.connect(path).execute("SELECT COUNT(*) FROM calls WHERE id=?", (cid,)).fetchone()[0] == 0)
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM attempts WHERE call_id=?", (cid,)).fetchone() == (0,)
            assert connection.execute("SELECT COUNT(*) FROM payloads WHERE attempt_id=?", (aid,)).fetchone() == (0,)
            assert connection.execute("SELECT COUNT(*) FROM usage WHERE attempt_id=?", (aid,)).fetchone() == (0,)
            assert connection.execute("SELECT COUNT(*) FROM calls WHERE session_id=?", (recent_sid,)).fetchone() == (1,)

        client = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=2)
        client.request("POST", "/v1/responses", b'{"model":"live"}')
        response = client.getresponse()
        assert response.status == 200 and json.loads(response.read()) == {"ok": True}
        client.close()
        assert gateway._purge_thread.is_alive()
    finally:
        stop_local(gateway, upstream)
    assert gateway._purge_thread is None
    assert not thread.is_alive()


@pytest.mark.parametrize("listen", ["0.0.0.0:8787", "localhost:8787", "[::1]:8787", "192.168.1.2:8787"])
def test_listener_rejects_non_loopback_before_bind(listen):
    with pytest.raises(ConfigError, match="127.0.0.1"):
        validate_listen(listen)


def test_listener_accepts_default_and_explicit_loopback():
    assert validate_listen("127.0.0.1:0") == ("127.0.0.1", 0)
    assert validate_listen("127.0.0.1:8787") == ("127.0.0.1", 8787)


@pytest.mark.parametrize("configured", [0, -1, "not-an-integer"])
def test_request_level_body_limit_error_is_json_and_terminal(tmp_path, configured):
    gateway, _thread, upstream, path = start_local(tmp_path)
    previous = Handler.capture_max_body
    Handler.capture_max_body = configured
    try:
        client = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=2)
        client.request("POST", "/v1/responses", b'{"model":"bad-limit"}')
        response = client.getresponse()
        payload = json.loads(response.read())
        client.close()
        assert response.status == 400
        assert payload["error"]["message"]
        wait_until(lambda: sqlite3.connect(path).execute("SELECT status FROM calls").fetchone() == ("failed",))
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT status,error_type FROM attempts").fetchone() == ("failed", "configuration_error")
            assert connection.execute("SELECT COUNT(*) FROM payloads").fetchone() == (1,)
        assert gateway._BaseServer__shutdown_request is False
    finally:
        Handler.capture_max_body = previous
        stop_local(gateway, upstream)


def test_sidecar_configuration_error_persists_only_scrubbed_body(tmp_path):
    gateway, _thread, upstream, path = start_local(tmp_path)
    previous_limit = Handler.capture_max_body
    previous_sidecar = Handler.sidecar_url
    Handler.capture_max_body = 0
    Handler.sidecar_url = "http://127.0.0.1:1"
    raw = json.dumps({
        "model": "anthropic/test",
        "context": {},
        "apiKey": "SIDEcar-secret",
        "authorization": "Bearer sidecar-secret",
    }).encode()
    try:
        client = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=2)
        client.request("POST", "/messages", raw, {"Content-Type": "application/json"})
        response = client.getresponse()
        assert response.status == 400
        response.read()
        client.close()
        wait_until(lambda: sqlite3.connect(path).execute("SELECT status FROM calls").fetchone() == ("failed",))
        with sqlite3.connect(path) as connection:
            stored, input_bytes = connection.execute("SELECT request_body, input_bytes FROM payloads JOIN attempts ON attempts.id=payloads.attempt_id").fetchone()
            assert stored is not None and b"SIDEcar-secret" not in stored and b"sidecar-secret" not in stored
            assert input_bytes == len(raw)
    finally:
        Handler.capture_max_body = previous_limit
        Handler.sidecar_url = previous_sidecar
        stop_local(gateway, upstream)


def test_all_public_entrypoints_share_canonical_behavior():
    import prompt_harbor.cli as cli
    import prompt_harbor.proxy as proxy
    import prompt_harbor.__main__ as package_main

    assert cli.main is canonical.main
    assert package_main.main is canonical.main
    assert proxy.Handler is canonical.Handler
    assert server.Handler is canonical.Handler
    assert usage.extract(b'{"usage":{"total_tokens":1}}') == canonical.extract_usage(b'{"usage":{"total_tokens":1}}')
    assert retention.purge.__module__ == "prompt_harbor.retention"


def _read_sse_resource(response, resource, timeout=3):
    response.fp.raw._sock.settimeout(0.2)
    deadline = time.time() + timeout
    lines = []
    while time.time() < deadline:
        try:
            line = response.readline()
        except TimeoutError:
            continue
        if not line:
            continue
        lines.append(line)
        if line == b"\n":
            if any(resource.encode() in item for item in lines):
                return lines
            lines = []
    raise AssertionError(f"did not receive SSE resource {resource}: {lines!r}")


def test_sse_notifies_direct_sql_calls_and_sessions(tmp_path):
    gateway, _thread, upstream, path = start_local(tmp_path, keepalive=0.03)
    events = None
    try:
        events = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=3)
        events.request("GET", "/api/events")
        response = events.getresponse()
        assert response.readline() == b"event: ready\n"
        assert response.readline() == b"data: {}\n"
        assert response.readline() == b"\n"
        with sqlite3.connect(path) as connection:
            sid = connection.execute(
                "INSERT INTO sessions(agent,started_at,last_seen_at) VALUES(?,?,?)", ("sql", canonical.iso(), canonical.iso())
            ).lastrowid
            connection.execute(
                "INSERT INTO calls(session_id,created_at,status) VALUES(?,?,?)", (sid, canonical.iso(), "running")
            )
        assert any(b'"resource":"calls"' in line for line in _read_sse_resource(response, "calls"))
        assert any(b'"resource":"sessions"' in line for line in _read_sse_resource(response, "sessions"))
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE calls SET status='succeeded' WHERE session_id=?", (sid,))
        assert any(b'"resource":"calls"' in line for line in _read_sse_resource(response, "calls"))
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE calls SET model='changed-model', endpoint='/changed' WHERE session_id=?", (sid,))
            connection.execute(
                "UPDATE sessions SET project_name='changed-project', metadata_json=? WHERE id=?",
                ('{"changed":true}', sid),
            )
        assert any(b'"resource":"calls"' in line for line in _read_sse_resource(response, "calls"))
        assert any(b'"resource":"sessions"' in line for line in _read_sse_resource(response, "sessions"))
    finally:
        if events:
            events.close()
        stop_local(gateway, upstream)
