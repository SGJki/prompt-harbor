import http.client
import io
import os
import shutil
import subprocess
import sys
import threading
import time
from email.message import Message
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import prompt_harbor.core as core
import prompt_harbor.retention as compatibility_retention
import prompt_harbor.server as compatibility_server
from prompt_harbor.config import ConfigError, Settings, settings_values, validate_sidecar_url, validate_values, write_config
from prompt_harbor.pi_messages import PiMessagesState
from prompt_harbor.sidecar import SidecarProcess, SidecarStartupError


ROOT = Path(__file__).parents[1]


def test_sidecar_get_redirect_is_not_forwarded(monkeypatch):
    class RedirectResponse:
        status = 302
        headers = {"Location": "https://evil.example.test/steal"}

        def read(self):
            raise AssertionError("redirect body must not be forwarded")

        def close(self):
            self.closed = True

    response = RedirectResponse()
    monkeypatch.setattr(core, "open_url", lambda request, timeout: response)
    handler = object.__new__(core.Handler)
    handler.sidecar_url = "http://127.0.0.1:9876"
    handler.sidecar_token = "secret"
    handler.sidecar_timeout = 1
    handler.close_connection = False
    sent = []
    handler.send_error = lambda status, message: sent.append((status, message))

    handler._proxy_sidecar_get("/models")

    assert sent == [(502, "sidecar redirect refused")]
    assert handler.close_connection is True
    assert response.closed is True


def test_config_writer_rejects_line_break_injection(tmp_path):
    path = tmp_path / "prompt-harbor.ini"
    original = "[gateway]\nmax_body = 1024\n"
    path.write_text(original, encoding="utf-8")
    values = settings_values(Settings())
    values["sidecar_command"] = "node sidecar.mjs\n[evil]\nkey = value"

    with pytest.raises(ConfigError, match="sidecar_command must not contain line breaks"):
        write_config(str(path), values)

    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:9876",
        "http://user@127.0.0.1:9876",
        "file:///tmp/sidecar.sock",
        "http://127.0.0.1:9876/?token=secret",
    ],
)
def test_sidecar_url_must_be_an_uncredentialed_loopback_url(url):
    with pytest.raises(ConfigError):
        validate_sidecar_url(url)


def test_sidecar_process_rejects_external_url():
    with pytest.raises(SidecarStartupError, match="must target loopback"):
        SidecarProcess(url="http://example.com:9876").start()


def test_sidecar_process_wraps_missing_executable(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("not found")

    monkeypatch.setattr(subprocess, "Popen", missing)
    with pytest.raises(SidecarStartupError, match="cannot start pi sidecar"):
        SidecarProcess(command=["missing-sidecar"]).start()


def test_managed_sidecar_rejects_non_loopback_ready_address():
    manager = SidecarProcess(
        command=[
            sys.executable,
            "-c",
            "import time; print('READY 0.0.0.0:9876', flush=True); time.sleep(30)",
        ],
        timeout=2,
    )
    with pytest.raises(SidecarStartupError, match="invalid address"):
        manager.start()
    assert manager.process is None


def test_main_stops_managed_sidecar_when_gateway_bind_fails(monkeypatch):
    settings = Settings(database="ignored.db", sidecar_command="fake-sidecar")

    class Cursor:
        lastrowid = 1

    class Connection:
        def execute(self, *args, **kwargs):
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    managers = []

    class Manager:
        configured = True

        def __init__(self, **kwargs):
            self.process = None
            self.stopped = False
            managers.append(self)

        def start(self):
            self.process = object()
            return "http://127.0.0.1:9876"

        def stop(self):
            self.stopped = True
            self.process = None

    monkeypatch.setattr(core, "load_settings", lambda args: settings)
    monkeypatch.setattr(core, "init", lambda *args: None)
    monkeypatch.setattr(core, "db", lambda *args: Connection())
    monkeypatch.setattr(core, "resolve_sources", lambda args: {})
    monkeypatch.setattr(core, "purge", lambda *args: 0)
    monkeypatch.setattr(core, "SidecarProcess", Manager)
    monkeypatch.setattr(core, "GatewayHTTPServer", lambda *args: (_ for _ in ()).throw(OSError("address in use")))

    with pytest.raises(OSError, match="address in use"):
        core.main(["start"])

    assert len(managers) == 1
    assert managers[0].stopped is True
    assert core.Handler.gateway_server is None


@pytest.mark.parametrize(
    "host",
    [None, "evil.example", "evil.example@127.0.0.1", "127.0.0.1/path", "127.0.0.1:invalid"],
)
def test_host_authority_validation_rejects_missing_or_malformed_values(host):
    handler = object.__new__(core.Handler)
    handler.headers = Message()
    if host is not None:
        handler.headers["Host"] = host
    handler.path = "/api/overview"
    sent = []
    handler._send_payload = lambda status, body, **kwargs: sent.append((status, body, kwargs))

    assert handler._reject_untrusted_host() is True
    assert sent[0][0] == 403
    assert sent[0][2]["api"] is True


@pytest.mark.parametrize("host", ["localhost", "localhost.:8787", "127.0.0.1:8787", "[::1]:8787"])
def test_host_authority_validation_accepts_local_service(host):
    handler = object.__new__(core.Handler)
    handler.headers = Message()
    handler.headers["Host"] = host
    handler.path = "/"
    handler._send_payload = lambda *args, **kwargs: pytest.fail("trusted Host was rejected")

    assert handler._reject_untrusted_host() is False


def test_host_authority_validation_rejects_duplicate_headers():
    handler = object.__new__(core.Handler)
    handler.headers = Message()
    handler.headers["Host"] = "localhost"
    handler.headers["Host"] = "127.0.0.1"
    handler.path = "/"
    handler._send_payload = lambda *args, **kwargs: None

    assert handler._reject_untrusted_host() is True


def test_send_error_escapes_message_and_handles_missing_path():
    handler = object.__new__(core.Handler)
    sent = []
    handler._send_payload = lambda *args, **kwargs: sent.append((args, kwargs))

    handler.send_error(400, "<script>alert(1)</script>")

    assert b"<script>" not in sent[0][0][1]
    assert b"&lt;script&gt;" in sent[0][0][1]
    assert sent[0][1]["api"] is False


def test_iso_preserves_unix_epoch():
    assert core.iso(0) == "1970-01-01T00:00:00+00:00"


@pytest.mark.parametrize(
    "headers",
    [
        [("Content-Length", "invalid")],
        [("Content-Length", "-1")],
        [("Content-Length", "1"), ("Content-Length", "1")],
        [("Transfer-Encoding", "chunked")],
    ],
)
def test_request_body_rejects_ambiguous_framing(headers):
    handler = object.__new__(core.Handler)
    handler.headers = Message()
    for name, value in headers:
        handler.headers[name] = value
    handler.rfile = io.BytesIO(b"x")

    with pytest.raises(ValueError):
        handler._read_body()


def test_request_body_rejects_incomplete_payload():
    handler = object.__new__(core.Handler)
    handler.headers = Message()
    handler.headers["Content-Length"] = "2"
    handler.rfile = io.BytesIO(b"x")

    with pytest.raises(ValueError, match="incomplete request body"):
        handler._read_body()


def test_connection_nominated_headers_are_not_forwarded():
    headers = Message()
    headers["Connection"] = "keep-alive, X-Internal"
    headers["X-Internal"] = "secret"

    excluded = core.hop_by_hop_headers(headers)

    assert {"connection", "keep-alive", "x-internal"} <= excluded


def test_upstream_content_length_must_be_unambiguous():
    headers = Message()
    headers["Content-Length"] = "2"
    assert core.Handler._response_content_length(headers) == 2

    headers["Content-Length"] = "3"
    with pytest.raises(ValueError, match="invalid upstream Content-Length"):
        core.Handler._response_content_length(headers)


def test_pi_messages_parser_handles_crlf_split_across_chunks():
    state = PiMessagesState()
    payload = b'data: {"type":"start"}\r\n\r\ndata: {"type":"done","reason":"stop","usage":{"totalTokens":2}}\r\n\r\n'
    split = payload.index(b"\r\n") + 1
    state.feed(payload[:split])
    state.feed(payload[split:])
    state.finish()

    assert state.terminal_type == "done"
    assert state.usage["total_tokens"] == 2


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_configuration_rejects_non_finite_numbers(value):
    values = settings_values(Settings())
    values["upstream_timeout"] = value
    with pytest.raises(ConfigError, match="upstream_timeout has invalid value"):
        validate_values(values)


def test_sse_writes_are_serialized_per_client():
    class ConcurrentWriteDetector:
        def __init__(self):
            self.active = 0
            self.overlapped = False

        def write(self, payload):
            self.active += 1
            if self.active > 1:
                self.overlapped = True
            time.sleep(0.001)
            self.active -= 1

        def flush(self):
            pass

    handler = object.__new__(core.Handler)
    handler.wfile = ConcurrentWriteDetector()
    handler._sse_write_lock = threading.Lock()
    threads = [
        threading.Thread(target=lambda: [handler._send_event("calls") for _ in range(20)]),
        threading.Thread(target=lambda: [handler._write_sse(b": keepalive\n\n") for _ in range(20)]),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert handler.wfile.overlapped is False


def test_sse_setup_failure_removes_client_and_closes_connection(monkeypatch):
    handler = object.__new__(core.Handler)
    handler.path = "/api/events"
    handler.headers = Message()
    handler.headers["Host"] = "localhost"
    handler.rfile = io.BytesIO()
    handler.wfile = io.BytesIO()
    handler.clients = []
    handler.close_connection = False
    handler.send_response = lambda status: None
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: None
    monkeypatch.setattr(core, "db", lambda *args: (_ for _ in ()).throw(RuntimeError("database unavailable")))

    handler.do_GET()

    assert handler.clients == []
    assert handler._sse_connection is None
    assert handler.close_connection is True


def test_compatibility_server_resets_all_runtime_handler_fields(monkeypatch):
    class Cursor:
        lastrowid = 7

    class Connection:
        def execute(self, *args, **kwargs):
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    class Server:
        pass

    monkeypatch.setattr(compatibility_server, "init", lambda *args: None)
    monkeypatch.setattr(compatibility_server, "purge", lambda *args: 0)
    monkeypatch.setattr(compatibility_server, "db", lambda *args: Connection())
    monkeypatch.setattr(compatibility_server, "GatewayHTTPServer", lambda *args: Server())
    monkeypatch.setattr(compatibility_server, "start_purge_worker", lambda *args: None)
    core.Handler.capture_max_body = 1
    core.Handler.upstream_timeout = 1
    core.Handler.sidecar_timeout = 1
    core.Handler.sse_keepalive = 1
    core.Handler.api_call_limit = 1
    core.Handler.ui_path = "stale.html"

    server = compatibility_server.serve("127.0.0.1:8787", "https://api.openai.com", "ignored.db")

    defaults = Settings()
    assert core.Handler.capture_max_body == defaults.max_body
    assert core.Handler.upstream_timeout == defaults.upstream_timeout
    assert core.Handler.sidecar_timeout == defaults.sidecar_timeout
    assert core.Handler.sse_keepalive == defaults.sse_keepalive
    assert core.Handler.api_call_limit == defaults.api_call_limit
    assert core.Handler.ui_path is None
    assert core.Handler.gateway_server is server


def test_compatibility_server_closes_listener_when_worker_start_fails(monkeypatch):
    class Cursor:
        lastrowid = 7

    class Connection:
        def execute(self, *args, **kwargs):
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    class Server:
        closed = False

        def server_close(self):
            self.closed = True

    server = Server()
    monkeypatch.setattr(compatibility_server, "init", lambda *args: None)
    monkeypatch.setattr(compatibility_server, "purge", lambda *args: 0)
    monkeypatch.setattr(compatibility_server, "db", lambda *args: Connection())
    monkeypatch.setattr(compatibility_server, "GatewayHTTPServer", lambda *args: server)
    monkeypatch.setattr(compatibility_server, "start_purge_worker", lambda *args: (_ for _ in ()).throw(RuntimeError("worker failed")))

    with pytest.raises(RuntimeError, match="worker failed"):
        compatibility_server.serve("127.0.0.1:8787", "https://api.openai.com", "ignored.db")

    assert server.closed is True
    assert core.Handler.gateway_server is None


def test_compatibility_retention_closes_connection_on_failure():
    class Connection:
        closed = False

        def close(self):
            self.closed = True

    connection = Connection()
    with pytest.raises(ValueError, match="retention_days must be positive"):
        compatibility_retention.purge("ignored.db", lambda *args: connection, retention_days=0)
    assert connection.closed is True


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("PI_AI_SIDECAR_HOST", "0.0.0.0", "must target loopback"),
        ("PI_AI_SIDECAR_MAX_BODY", "not-a-number", "must be a positive integer"),
    ],
)
def test_node_sidecar_rejects_unsafe_startup_settings(name, value, message):
    environment = dict(os.environ, PI_AI_SIDECAR_FIXTURE="1", **{name: value})
    result = subprocess.run(
        ["node", str(ROOT / "sidecar" / "server.mjs")],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_sidecar_returns_413_without_resetting_connection():
    manager = SidecarProcess(
        command=["node", str(ROOT / "sidecar" / "server.mjs")],
        env={"PI_AI_SIDECAR_FIXTURE": "1", "PI_AI_SIDECAR_MAX_BODY": "4"},
        timeout=3,
    )
    url = manager.start()
    parsed = urlsplit(url)
    try:
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        connection.request("POST", "/messages", body=b"12345", headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read()
        connection.close()
        assert response.status == 413
        assert b"request body too large" in body
    finally:
        manager.stop()
