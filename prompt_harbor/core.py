#!/usr/bin/env python3
"""PromptHarbor gateway and command-line entrypoint."""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from .config import (
    DEFAULT_DB,
    DEFAULT_DB_TIMEOUT,
    DEFAULT_LISTEN,
    DEFAULT_MAX_BODY,
    DEFAULT_API_CALL_LIMIT,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_SSE_KEEPALIVE,
    DEFAULT_SIDECAR_TIMEOUT,
    DEFAULT_UPSTREAM,
    DEFAULT_UPSTREAM_TIMEOUT,
    ConfigError,
    load_settings,
)
from .headers import sanitize
from .pi_messages import PiMessagesState, normalize_usage, split_model_id
from .sidecar import SidecarProcess, SidecarStartupError

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
HOP_BY_HOP = {"transfer-encoding", "connection", "content-length"}
DEFAULT_DB = DEFAULT_DB
DEFAULT_LISTEN = DEFAULT_LISTEN
DEFAULT_UPSTREAM = DEFAULT_UPSTREAM


def iso(ts=None):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else datetime.now(timezone.utc).isoformat()


def db(path, timeout=DEFAULT_DB_TIMEOUT):
    connection = sqlite3.connect(path, timeout=timeout)
    connection.row_factory = sqlite3.Row
    return connection


def schema(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY AUTOINCREMENT,agent TEXT,started_at TEXT,last_seen_at TEXT,cwd TEXT,project_name TEXT,metadata_json TEXT);
        CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id INTEGER,created_at TEXT,completed_at TEXT,provider TEXT,api_family TEXT,endpoint TEXT,model TEXT,stream INTEGER,status TEXT,status_code INTEGER,first_byte_at TEXT,duration_ms INTEGER,input_bytes INTEGER DEFAULT 0,output_bytes INTEGER DEFAULT 0,error_type TEXT,error_message TEXT);
        CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,call_id INTEGER,attempt_no INTEGER,started_at TEXT,completed_at TEXT,upstream_url TEXT,status TEXT,status_code INTEGER,request_headers_json TEXT,response_headers_json TEXT,first_byte_at TEXT,duration_ms INTEGER,input_bytes INTEGER DEFAULT 0,output_bytes INTEGER DEFAULT 0,error_type TEXT,error_message TEXT);
        CREATE TABLE IF NOT EXISTS payloads(id INTEGER PRIMARY KEY AUTOINCREMENT,attempt_id INTEGER UNIQUE,request_body BLOB,response_body BLOB,request_content_type TEXT,response_content_type TEXT,response_complete INTEGER DEFAULT 0,request_truncated INTEGER DEFAULT 0,response_truncated INTEGER DEFAULT 0,created_at TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY AUTOINCREMENT,attempt_id INTEGER UNIQUE,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,raw_usage_json TEXT);
        CREATE INDEX IF NOT EXISTS calls_created_idx ON calls(created_at);
        """
    )
    if connection.execute("PRAGMA user_version").fetchone()[0] < 1:
        connection.execute("PRAGMA user_version = 1")


def init(path, timeout=DEFAULT_DB_TIMEOUT):
    connection = db(path, timeout)
    schema(connection)
    connection.commit()
    connection.close()


def headers(header_map):
    return sanitize(header_map)


def extract_usage(data):
    """Extract legacy OpenAI usage from a complete JSON or SSE response."""
    try:
        value = json.loads(data).get("usage")
        if value:
            return value
    except Exception:
        pass
    for line in data.splitlines():
        if line.startswith(b"data:"):
            try:
                value = line[5:].strip()
                if value and value != b"[DONE]":
                    usage = json.loads(value).get("usage")
                    if usage:
                        return usage
            except Exception:
                pass
    return None


def body_limit(configured=None):
    if configured is not None:
        return configured
    return int(os.getenv("PROMPT_HARBOR_MAX_BODY", str(DEFAULT_MAX_BODY)))


class Handler(BaseHTTPRequestHandler):
    clients = []
    protocol_version = "HTTP/1.1"
    db_path = DEFAULT_DB
    upstream = DEFAULT_UPSTREAM
    sidecar_url = None
    sidecar_token = None
    session_id = None
    db_timeout = DEFAULT_DB_TIMEOUT
    capture_max_body = DEFAULT_MAX_BODY
    upstream_timeout = DEFAULT_UPSTREAM_TIMEOUT
    sidecar_timeout = DEFAULT_SIDECAR_TIMEOUT
    sse_keepalive = DEFAULT_SSE_KEEPALIVE
    api_call_limit = DEFAULT_API_CALL_LIMIT
    ui_path = None

    def do_POST(self):
        if self.path == "/messages":
            self._handle_pi_messages()
        else:
            self._handle_openai()

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        return self.rfile.read(max(0, length))

    def _request_metadata(self, body):
        model = None
        stream = False
        parsed = None
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                model = parsed.get("model")
                stream = bool(parsed.get("stream"))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        return parsed, model, stream

    def _begin_attempt(self, body, model, stream, provider, api_family, target, input_bytes=None):
        started = time.time()
        created = iso(started)
        limit = body_limit(self.capture_max_body)
        request_size = input_bytes if input_bytes is not None else len(body)
        connection = db(self.db_path, self.db_timeout)
        connection.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (created, self.session_id))
        cursor = connection.execute(
            "INSERT INTO calls(session_id,created_at,provider,api_family,endpoint,model,stream,status,input_bytes) VALUES(?,?,?,?,?,?,?,?,?)",
            (self.session_id, created, provider, api_family, self.path, model, int(bool(stream)), "running", request_size),
        )
        call_id = cursor.lastrowid
        cursor = connection.execute(
            "INSERT INTO attempts(call_id,attempt_no,started_at,upstream_url,status,request_headers_json,input_bytes) VALUES(?,?,?,?,?,?,?)",
            (call_id, 1, created, target, "running", json.dumps(headers(self.headers), ensure_ascii=False), request_size),
        )
        attempt_id = cursor.lastrowid
        connection.execute(
            "INSERT INTO payloads(attempt_id,request_body,request_content_type,request_truncated,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (attempt_id, body[:limit], self.headers.get("Content-Type"), int(request_size > limit), created, created),
        )
        connection.commit()
        connection.close()
        return {
            "started": started, "call_id": call_id, "attempt_id": attempt_id, "limit": limit,
            "out": bytearray(), "total_out": 0, "response_truncated": False, "first": None,
            "status": None, "response_headers": {}, "response_content_type": None,
            "state": "failed", "error_type": None, "error_message": None, "response_complete": False,
        }

    def _capture_chunk(self, record, chunk):
        if not chunk:
            return
        if record["first"] is None:
            record["first"] = time.time()
        record["total_out"] += len(chunk)
        remaining = max(0, record["limit"] - len(record["out"]))
        if remaining:
            record["out"].extend(chunk[:remaining])
        if record["total_out"] > record["limit"]:
            record["response_truncated"] = True

    def _finish_attempt(self, record, usage=None, raw_usage=None):
        done = time.time()
        values = (
            iso(done), record["state"], record["status"], json.dumps(record["response_headers"], ensure_ascii=False),
            iso(record["first"]) if record["first"] else None, int((done - record["started"]) * 1000),
            record["total_out"], record["error_type"], record["error_message"], record["attempt_id"],
        )
        connection = db(self.db_path, self.db_timeout)
        connection.execute("UPDATE attempts SET completed_at=?,status=?,status_code=?,response_headers_json=?,first_byte_at=?,duration_ms=?,output_bytes=?,error_type=?,error_message=? WHERE id=?", values)
        connection.execute("UPDATE calls SET completed_at=?,status=?,status_code=?,first_byte_at=?,duration_ms=?,output_bytes=?,error_type=?,error_message=? WHERE id=?", values[:1] + values[1:2] + values[2:3] + values[4:9] + (record["call_id"],))
        connection.execute("UPDATE payloads SET response_body=?,response_content_type=?,response_complete=?,response_truncated=?,updated_at=? WHERE attempt_id=?", (bytes(record["out"]), record["response_content_type"], int(record["response_complete"]), int(record["response_truncated"]), iso(done), record["attempt_id"]))
        normalized = normalize_usage(usage)
        if normalized:
            raw_value = raw_usage if isinstance(raw_usage, dict) else usage if isinstance(usage, dict) else normalized
            connection.execute("INSERT OR REPLACE INTO usage(attempt_id,input_tokens,output_tokens,total_tokens,raw_usage_json) VALUES(?,?,?,?,?)", (record["attempt_id"], normalized.get("input_tokens"), normalized.get("output_tokens"), normalized.get("total_tokens"), json.dumps(raw_value, ensure_ascii=False)))
        connection.commit()
        connection.close()
        self._notify_calls()

    def _notify_calls(self):
        for client in list(self.clients):
            try:
                client.wfile.write(b'event: invalidate\ndata: {"resource":"calls"}\n\n')
                client.wfile.flush()
            except Exception:
                try:
                    self.clients.remove(client)
                except ValueError:
                    pass

    def _handle_openai(self):
        body = self._read_body()
        _, model, stream = self._request_metadata(body)
        target = self.upstream.rstrip("/") + self.path
        record = self._begin_attempt(body, model, stream, "openai", "openai", target)
        response = None
        usage = None
        try:
            request_headers = {key: value for key, value in self.headers.items() if key.lower() != "host"}
            try:
                response = urlopen(Request(target, data=body, method=self.command, headers=request_headers), timeout=self.upstream_timeout)
            except HTTPError as exc:
                response = exc
            record["status"] = response.status
            record["response_headers"] = headers(response.headers)
            record["response_content_type"] = response.headers.get("Content-Type")
            self.send_response(response.status)
            for key, value in response.headers.items():
                if key.lower() not in HOP_BY_HOP:
                    self.send_header(key, value)
            self.end_headers()
            expected = response.headers.get("Content-Length")
            while True:
                chunk = response.read(8192)
                if not chunk:
                    break
                self._capture_chunk(record, chunk)
                self.wfile.write(chunk)
                self.wfile.flush()
            record["response_complete"] = expected is None or record["total_out"] == int(expected)
            incomplete = not record["response_complete"]
            record["state"] = "succeeded" if record["status"] < 400 and not incomplete else "failed"
            if incomplete:
                record["error_type"], record["error_message"] = "upstream_incomplete", "incomplete upstream response"
            elif record["status"] >= 400:
                record["error_type"], record["error_message"] = "upstream_http", "upstream HTTP error"
            usage = extract_usage(bytes(record["out"]))
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            record["error_type"], record["error_message"], record["state"] = "client_cancel", "client disconnected", "failed"
            if response is not None:
                response.close()
        except Exception as exc:
            record["error_type"], record["error_message"], record["state"] = "upstream_error", str(exc), "failed"
            record["status"] = record["status"] or 502
            try:
                self.send_error(502, "upstream unavailable")
            except Exception:
                pass
        finally:
            if response is not None:
                response.close()
            self._finish_attempt(record, usage)

    def _handle_pi_messages(self):
        body = self._read_body()
        parsed, model, _ = self._request_metadata(body)
        provider, _ = split_model_id(model)
        target = (self.sidecar_url.rstrip("/") + "/messages") if self.sidecar_url else "sidecar://unavailable/messages"
        safe_body = self._safe_pi_body(parsed, body)
        record = self._begin_attempt(safe_body, model, True, provider or "pi-ai", "pi-messages", target, input_bytes=len(body))
        state = PiMessagesState()
        response = None
        if not isinstance(parsed, dict) or not isinstance(model, str) or not isinstance(parsed.get("context"), dict):
            record["status"], record["error_type"], record["error_message"] = 400, "invalid_request", "model and context are required"
            self.send_error(400, "model and context are required")
            self._finish_attempt(record)
            return
        if not self.sidecar_url:
            record["status"], record["error_type"], record["error_message"] = 503, "sidecar_unavailable", "pi-ai sidecar is not configured"
            self.send_error(503, "pi-ai sidecar unavailable")
            self._finish_attempt(record)
            return
        try:
            request_headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
            if self.sidecar_token:
                request_headers["Authorization"] = "Bearer " + self.sidecar_token
            try:
                response = urlopen(Request(target, data=safe_body, method="POST", headers=request_headers), timeout=self.upstream_timeout)
            except HTTPError as exc:
                response = exc
            record["status"] = response.status
            record["response_headers"] = headers(response.headers)
            record["response_content_type"] = response.headers.get("Content-Type")
            self.send_response(response.status)
            for key, value in response.headers.items():
                if key.lower() not in HOP_BY_HOP:
                    self.send_header(key, value)
            self.end_headers()
            while True:
                chunk = response.read(8192)
                if not chunk:
                    break
                self._capture_chunk(record, chunk)
                state.feed(chunk)
                self.wfile.write(chunk)
                self.wfile.flush()
            state.finish()
            record["response_complete"] = state.complete or record["status"] >= 400
            if record["status"] >= 400:
                record["state"], record["error_type"] = "failed", "sidecar_http"
                record["error_message"] = "sidecar HTTP error"
            elif state.terminal_type == "done" and state.protocol_error is None:
                record["state"] = "succeeded"
            elif state.terminal_type == "done":
                record["state"], record["error_type"], record["error_message"] = "failed", "pi_protocol", state.protocol_error
            elif state.terminal_type == "error":
                record["state"], record["error_type"] = "failed", "pi_error"
                record["error_message"] = state.error_message or state.terminal_reason or "pi-ai error"
            elif state.protocol_error:
                record["state"], record["error_type"], record["error_message"] = "failed", "pi_protocol", state.protocol_error
            else:
                record["state"], record["error_type"], record["error_message"] = "failed", "upstream_incomplete", "pi-messages stream ended without a terminal event"
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            record["state"], record["error_type"], record["error_message"] = "failed", "client_cancel", "client disconnected"
            if response is not None:
                response.close()
        except Exception as exc:
            record["state"], record["status"], record["error_type"], record["error_message"] = "failed", record["status"] or 503, "sidecar_unavailable", str(exc)
            try:
                self.send_error(503, "pi-ai sidecar unavailable")
            except Exception:
                pass
        finally:
            if response is not None:
                response.close()
            self._finish_attempt(record, state.usage, state.raw_usage)

    @staticmethod
    def _safe_pi_body(parsed, original):
        if not isinstance(parsed, dict):
            return original

        sensitive_keys = {
            "access-token",
            "access_token",
            "accesstoken",
            "api-key",
            "apikey",
            "api_key",
            "authorization",
            "cookie",
            "credential",
            "credentials",
            "env",
            "fetch",
            "headers",
            "onpayload",
            "onresponse",
            "proxy-authorization",
            "proxy_authorization",
            "refresh-token",
            "refresh_token",
            "refreshtoken",
            "set-cookie",
            "signal",
            "transformheaders",
            "x-api-key",
            "x-auth-token",
            "x-goog-api-key",
        }

        def scrub(value):
            if isinstance(value, dict):
                return {
                    key: scrub(item)
                    for key, item in value.items()
                    if not isinstance(key, str) or key.lower() not in sensitive_keys
                }
            if isinstance(value, list):
                return [scrub(item) for item in value]
            return value

        safe = scrub(parsed)
        try:
            return json.dumps(safe, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            return original

    def do_GET(self):
        if self.path == "/models":
            self._proxy_sidecar_get("/models")
            return
        if self.path == "/health":
            self._proxy_sidecar_get("/health")
            return
        if self.path in ("/", "/index.html"):
            path = self.ui_path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
            try:
                raw = open(path, "rb").read()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if self.path.startswith("/api/calls/"):
            try:
                call_id = int(self.path.rsplit("/", 1)[1])
            except (TypeError, ValueError):
                self.send_error(400); return
            connection = db(self.db_path, self.db_timeout)
            row = connection.execute("SELECT c.*,a.request_headers_json,a.response_headers_json,p.request_body,p.response_body,p.request_truncated,p.response_truncated,u.input_tokens,u.output_tokens,u.total_tokens,u.raw_usage_json FROM calls c LEFT JOIN attempts a ON a.call_id=c.id LEFT JOIN payloads p ON p.attempt_id=a.id LEFT JOIN usage u ON u.attempt_id=a.id WHERE c.id=?", (call_id,)).fetchone()
            connection.close()
            if not row:
                self.send_error(404); return
            value = dict(row)
            value["request_headers_json"] = json.loads(value["request_headers_json"] or "{}"); value["response_headers_json"] = json.loads(value["response_headers_json"] or "{}")
            for key in ("request_body", "response_body"):
                if isinstance(value.get(key), bytes): value[key] = value[key].decode("utf-8", errors="replace")
            raw = json.dumps(value, ensure_ascii=False).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if self.path == "/api/events":
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache"); self.send_header("Connection", "keep-alive"); self.end_headers(); self.wfile.write(b"event: ready\ndata: {}\n\n"); self.wfile.flush(); self.clients.append(self)
            try:
                while True:
                    time.sleep(self.sse_keepalive); self.wfile.write(b": keepalive\n\n"); self.wfile.flush()
            except Exception:
                if self in self.clients: self.clients.remove(self)
            return
        if self.path not in ("/api/overview", "/api/calls", "/api/sessions"):
            if self.path.startswith("/api/"): self.send_error(404); return
            self.static(); return
        connection = db(self.db_path, self.db_timeout)
        calls = [dict(row) for row in connection.execute("SELECT id,session_id,created_at,completed_at,endpoint,model,status,status_code,duration_ms,input_bytes,output_bytes,error_type FROM calls ORDER BY id DESC LIMIT ?", (self.api_call_limit,))]
        sessions = [dict(row) for row in connection.execute("SELECT s.id,s.agent,s.started_at,s.last_seen_at,s.cwd,s.project_name,(SELECT COUNT(*) FROM calls x WHERE x.session_id=s.id) AS call_count FROM sessions s ORDER BY s.id DESC")]
        connection.close(); payload = {"calls": calls, "sessions": sessions}
        if self.path == "/api/calls": payload = {"calls": calls}
        if self.path == "/api/sessions": payload = {"sessions": sessions}
        raw = json.dumps(payload, ensure_ascii=False).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    def _proxy_sidecar_get(self, path):
        if not self.sidecar_url:
            self.send_error(503, "pi-ai sidecar unavailable"); return
        response = None
        try:
            request_headers = {"Accept": "application/json"}
            if self.sidecar_token: request_headers["Authorization"] = "Bearer " + self.sidecar_token
            try:
                response = urlopen(Request(self.sidecar_url.rstrip("/") + path, headers=request_headers), timeout=self.sidecar_timeout)
            except HTTPError as exc:
                response = exc
            body = response.read(); self.send_response(response.status)
            for key, value in response.headers.items():
                if key.lower() not in HOP_BY_HOP: self.send_header(key, value)
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        except Exception:
            self.send_error(503, "pi-ai sidecar unavailable")
        finally:
            if response is not None: response.close()

    def static(self):
        relative = unquote(urlparse(self.path).path).lstrip("/"); extension = os.path.splitext(relative)[1].lower()
        if not relative or ".." in relative.replace("\\", "/").split("/") or extension not in CONTENT_TYPES: self.send_error(404); return
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui"); path = os.path.normpath(os.path.join(root, relative))
        if not path.startswith(root + os.sep): self.send_error(404); return
        try: raw = open(path, "rb").read()
        except OSError: self.send_error(404); return
        self.send_response(200); self.send_header("Content-Type", CONTENT_TYPES[extension]); self.send_header("Content-Length", str(len(raw))); self.send_header("Cache-Control", "no-cache"); self.end_headers(); self.wfile.write(raw)

    def log_message(self, *args):
        pass


def purge(path, retention_days=DEFAULT_RETENTION_DAYS, timeout=DEFAULT_DB_TIMEOUT):
    if retention_days <= 0:
        raise ValueError("retention_days must be positive")
    connection = db(path, timeout)
    ids = [row[0] for row in connection.execute("SELECT id FROM calls WHERE julianday(created_at) < julianday('now', ?)", (f"-{retention_days} days",))]
    for call_id in ids:
        attempt_ids = [row[0] for row in connection.execute("SELECT id FROM attempts WHERE call_id=?", (call_id,))]
        for attempt_id in attempt_ids:
            connection.execute("DELETE FROM usage WHERE attempt_id=?", (attempt_id,)); connection.execute("DELETE FROM payloads WHERE attempt_id=?", (attempt_id,))
        connection.execute("DELETE FROM attempts WHERE call_id=?", (call_id,)); connection.execute("DELETE FROM calls WHERE id=?", (call_id,))
    connection.commit(); connection.close(); return len(ids)


def _add_config_options(parser, suppress=False):
    default = argparse.SUPPRESS if suppress else None
    parser.add_argument("--config", default=default, help="INI configuration file")
    parser.add_argument("--database", default=default)
    parser.add_argument("--listen", default=default)
    parser.add_argument("--upstream", default=default)
    parser.add_argument("--max-body", dest="max_body", type=int, default=default)
    parser.add_argument("--retention-days", dest="retention_days", type=int, default=default)
    parser.add_argument("--db-timeout", dest="db_timeout", type=float, default=default)
    parser.add_argument("--upstream-timeout", dest="upstream_timeout", type=float, default=default)
    parser.add_argument("--sidecar-timeout", dest="sidecar_timeout", type=float, default=default)
    parser.add_argument("--sidecar-start-timeout", dest="sidecar_start_timeout", type=float, default=default)
    parser.add_argument("--sidecar-stop-timeout", dest="sidecar_stop_timeout", type=float, default=default)
    parser.add_argument("--api-call-limit", dest="api_call_limit", type=int, default=default)
    parser.add_argument("--cli-call-limit", dest="cli_call_limit", type=int, default=default)
    parser.add_argument("--sse-keepalive", dest="sse_keepalive", type=float, default=default)
    parser.add_argument("--ui", dest="ui_path", default=default)
    parser.add_argument("--pi-sidecar-url", dest="pi_sidecar_url", default=default)
    parser.add_argument("--pi-sidecar-command", dest="pi_sidecar_command", default=default)
    parser.add_argument("--pi-sidecar-token", dest="pi_sidecar_token", default=default)


def _argument_parser():
    parser = argparse.ArgumentParser()
    _add_config_options(parser)
    subparsers = parser.add_subparsers(dest="cmd", required=True)
    for name in ("init", "start", "list", "purge"):
        subparser = subparsers.add_parser(name)
        _add_config_options(subparser, suppress=True)
    show = subparsers.add_parser("show")
    show.add_argument("call_id", type=int)
    _add_config_options(show, suppress=True)
    return parser


def main(argv=None):
    args = _argument_parser().parse_args(argv)
    try:
        settings = load_settings(args)
    except ConfigError as exc:
        raise SystemExit(f"configuration error: {exc}") from exc
    path = settings.database
    init(path, settings.db_timeout)
    if args.cmd == "init": print("initialized SQLite database:", path); return
    if args.cmd == "purge": print("purged", purge(path, settings.retention_days, settings.db_timeout), f"calls older than {settings.retention_days} days"); return
    if args.cmd == "list":
        for row in db(path, settings.db_timeout).execute("SELECT id,created_at,endpoint,model,status,status_code,duration_ms,input_bytes,output_bytes FROM calls ORDER BY id DESC LIMIT ?", (settings.cli_call_limit,)):
            print(f"{row['id']} {row['created_at']} {row['model'] or '-'} {row['status']} {row['status_code'] or '-'} {row['duration_ms'] or '-'}ms {row['input_bytes']}/{row['output_bytes']} {row['endpoint']}")
        return
    if args.cmd == "show":
        row = db(path, settings.db_timeout).execute("SELECT c.*,a.request_headers_json,a.response_headers_json,p.request_body,p.response_body,p.request_truncated,p.response_truncated FROM calls c JOIN attempts a ON a.call_id=c.id JOIN payloads p ON p.attempt_id=a.id WHERE c.id=?", (args.call_id,)).fetchone()
        if not row: raise SystemExit("call not found")
        print("[HEADERS]\n" + json.dumps({"request": json.loads(row["request_headers_json"] or "{}"), "response": json.loads(row["response_headers_json"] or "{}")}, indent=2)); print(json.dumps({key: row[key] for key in ("id", "session_id", "created_at", "completed_at", "endpoint", "model", "stream", "status", "status_code", "first_byte_at", "duration_ms", "input_bytes", "output_bytes", "error_type", "error_message")}, indent=2)); print(f"truncated: request={row['request_truncated'] or 0} response={row['response_truncated'] or 0}"); print("\n[REQUEST]\n" + (row["request_body"] or b"").decode(errors="replace")); print("\n[RESPONSE]\n" + (row["response_body"] or b"").decode(errors="replace")); return
    connection = db(path, settings.db_timeout); cursor = connection.execute("INSERT INTO sessions(agent,started_at,last_seen_at,cwd,metadata_json) VALUES(?,?,?,?,?)", ("codex", iso(), iso(), os.getcwd(), "{}")); session_id = cursor.lastrowid; connection.commit(); connection.close()
    sidecar_env = {"PI_AI_SIDECAR_TOKEN": settings.sidecar_token} if settings.sidecar_token else None
    manager = SidecarProcess(url=settings.sidecar_url, command=settings.sidecar_command, timeout=settings.sidecar_start_timeout, stop_timeout=settings.sidecar_stop_timeout, env=sidecar_env); configured_sidecar = None
    try:
        if manager.configured: configured_sidecar = manager.start()
    except SidecarStartupError as exc:
        print("pi-ai sidecar unavailable:", exc, file=sys.stderr)
    Handler.db_path = path; Handler.upstream = settings.upstream; Handler.sidecar_url = configured_sidecar; Handler.sidecar_token = settings.sidecar_token; Handler.session_id = session_id
    Handler.db_timeout = settings.db_timeout; Handler.capture_max_body = settings.max_body; Handler.upstream_timeout = settings.upstream_timeout; Handler.sidecar_timeout = settings.sidecar_timeout; Handler.sse_keepalive = settings.sse_keepalive; Handler.api_call_limit = settings.api_call_limit; Handler.ui_path = settings.ui_path
    host, port = settings.listen.rsplit(":", 1); print(f"gateway listening on http://{settings.listen}, upstream {settings.upstream}", flush=True); purge(path, settings.retention_days, settings.db_timeout)
    server = ThreadingHTTPServer((host, int(port)), Handler)
    try: server.serve_forever()
    finally: server.server_close(); manager.stop()


if __name__ == "__main__":
    main()
