import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


class PiSidecar(BaseHTTPRequestHandler):
    mode = "done"
    token = None

    def do_GET(self):
        if type(self).token and self.headers.get("Authorization") != "Bearer " + type(self).token:
            self.send_response(401)
            self.end_headers()
            return
        if self.path == "/health":
            body = b'{"ok":true}'
        elif self.path == "/models":
            body = b'{"models":[{"id":"fixture/model","provider":"fixture"}]}'
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        type(self).last_request = request
        type(self).last_authorization = self.headers.get("Authorization")
        if type(self).mode == "http_error":
            body = b'{"error":{"code":"provider_down","message":"provider unavailable"}}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if type(self).mode == "http_error_invalid_sse":
            body = b"data: {invalid-json}\n\n"
            self.send_response(503)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("X-Pi-Model", request["model"])
        self.end_headers()
        if type(self).mode == "error":
            events = [
                b'data: {"type":"start"}\n\n',
                b'data: {"type":"error","reason":"error","usage":{"input":2,"output":0,"totalTokens":2},"errorMessage":"provider failed"}\n\n',
            ]
        else:
            events = [
                b'data: {"type":"start"}\n\n',
                b'data: {"type":"text_start","contentIndex":0}\n\n',
                b'data: {"type":"text_delta","contentIndex":0,"delta":"hel',
                b'lo"}\n\n',
                b'data: {"type":"text_end","contentIndex":0,"content":"hello"}\n\n',
                b'data: {"type":"done","reason":"stop","usage":{"input":3,"output":2,"totalTokens":5,"cacheRead":1,"cost":{"total":0.01}}}\n\n',
            ]
        for event in events:
            self.wfile.write(event)
            self.wfile.flush()
            time.sleep(0.01)

    def log_message(self, *args):
        pass


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_row(database, query, timeout=3):
    deadline = time.time() + timeout
    row = None
    while time.time() < deadline:
        with sqlite3.connect(database) as connection:
            row = connection.execute(query).fetchone()
        if row and row[0] != "running":
            return row
        time.sleep(0.03)
    raise AssertionError((query, row))


def start_gateway(tmp_path, sidecar, token=None, command=None, env=None):
    port = free_port()
    database = tmp_path / "gateway.db"
    environment = dict(os.environ)
    environment.update(env or {})
    args = [sys.executable, str(ROOT / "prompt_harbor.py"), "start", "--database", str(database), "--listen", f"127.0.0.1:{port}"]
    if sidecar:
        args.extend(["--pi-sidecar-url", sidecar])
    if token:
        args.extend(["--pi-sidecar-token", token])
    if command:
        args.extend(["--pi-sidecar-command", command])
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            connection.connect()
            connection.close()
            return process, port, database
        except OSError:
            time.sleep(0.03)
    process.kill()
    raise AssertionError("gateway did not start")


def test_pi_messages_stream_maps_usage_and_preserves_bytes(tmp_path):
    PiSidecar.mode = "done"
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), PiSidecar)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    process, port, database = start_gateway(tmp_path, f"http://127.0.0.1:{upstream.server_port}")
    try:
        request = {"model": "fixture/model", "context": {"messages": [{"role": "user", "content": "hi"}]}, "options": {"temperature": 0.2}}
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("POST", "/messages", json.dumps(request), {"Content-Type": "application/json", "Authorization": "Bearer client-secret"})
        response = connection.getresponse()
        body = response.read()
        connection.close()
        assert response.status == 200
        assert response.getheader("Content-Type") == "text/event-stream"
        assert b'"delta":"hello"' in body and b'"type":"done"' in body
        row = wait_row(database, "select status from calls")
        assert row == ("succeeded",)
        with sqlite3.connect(database) as connection:
            call = connection.execute("select provider,api_family,model from calls").fetchone()
            usage = connection.execute("select input_tokens,output_tokens,total_tokens,raw_usage_json from usage").fetchone()
            payload = connection.execute("select response_complete,response_body from payloads").fetchone()
        assert call == ("fixture", "pi-messages", "fixture/model")
        assert usage[:3] == (3, 2, 5)
        assert json.loads(usage[3]) == {"input": 3, "output": 2, "totalTokens": 5, "cacheRead": 1, "cost": {"total": 0.01}}
        assert payload[0] == 1 and payload[1] == body
        assert PiSidecar.last_request == request
        assert PiSidecar.last_authorization is None
    finally:
        process.terminate()
        process.wait(timeout=3)
        upstream.shutdown()


def test_pi_messages_strips_provider_credentials_from_body(tmp_path):
    PiSidecar.mode = "done"
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), PiSidecar)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    process, port, database = start_gateway(tmp_path, f"http://127.0.0.1:{upstream.server_port}")
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("POST", "/messages", b'{"model":"fixture/model","headers":{"Authorization":"Bearer SECRET","X-Secret":"SECRET"},"proxy-authorization":"Basic SECRET","context":{"messages":[],"metadata":{"headers":{"X-Nested-Secret":"SECRET"}}},"options":{"apiKey":"SECRET","headers":{"X-Secret":"SECRET"}}}')
        response = connection.getresponse()
        response.read()
        connection.close()
        wait_row(database, "select status from calls")
        with sqlite3.connect(database) as connection:
            request_body = connection.execute("select request_body from payloads").fetchone()[0]
        assert b"SECRET" not in request_body
        assert "apiKey" not in PiSidecar.last_request.get("options", {})
        assert "headers" not in PiSidecar.last_request.get("options", {})
        assert "headers" not in PiSidecar.last_request
        assert "proxy-authorization" not in PiSidecar.last_request
        assert "headers" not in PiSidecar.last_request["context"].get("metadata", {})
    finally:
        process.terminate()
        process.wait(timeout=3)
        upstream.shutdown()


@pytest.mark.parametrize("mode,error_type", [("error", "pi_error"), ("http_error", "sidecar_http"), ("http_error_invalid_sse", "sidecar_http")])
def test_pi_messages_failure_is_recorded(tmp_path, mode, error_type):
    PiSidecar.mode = mode
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), PiSidecar)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    process, port, database = start_gateway(tmp_path, f"http://127.0.0.1:{upstream.server_port}")
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("POST", "/messages", b'{"model":"fixture/model","context":{"messages":[]}}')
        response = connection.getresponse()
        body = response.read()
        connection.close()
        row = wait_row(database, "select status,error_type,status_code from calls")
        assert row[0] == "failed" and row[1] == error_type
        if mode == "error":
            assert response.status == 200 and b"provider failed" in body and row[2] == 200
        elif mode == "http_error":
            assert response.status == 503 and b"provider_down" in body and row[2] == 503
        else:
            assert response.status == 503 and b"invalid-json" in body and row[2] == 503
    finally:
        process.terminate()
        process.wait(timeout=3)
        upstream.shutdown()


def test_pi_messages_sidecar_token_and_get_models(tmp_path):
    PiSidecar.mode = "done"
    PiSidecar.token = "local-token"
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), PiSidecar)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    process, port, database = start_gateway(tmp_path, f"http://127.0.0.1:{upstream.server_port}", token="local-token")
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/models")
        response = connection.getresponse()
        assert response.status == 200 and json.loads(response.read())["models"][0]["id"] == "fixture/model"
        connection.close()
    finally:
        PiSidecar.token = None
        process.terminate()
        process.wait(timeout=3)
        upstream.shutdown()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_managed_fixture_sidecar(tmp_path):
    process, port, database = start_gateway(
        tmp_path,
        None,
        command="node sidecar/server.mjs",
        env={"PI_AI_SIDECAR_FIXTURE": "1"},
    )
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("POST", "/messages", b'{"model":"fixture/model","context":{"messages":[]}}')
        response = connection.getresponse()
        assert response.status == 200 and b"fixture:fixture/model" in response.read()
        connection.close()
        assert wait_row(database, "select status from calls") == ("succeeded",)
    finally:
        process.terminate()
        process.wait(timeout=3)
