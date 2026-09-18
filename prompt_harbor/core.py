#!/usr/bin/env python3
"""PromptHarbor gateway and command-line entrypoint."""

import argparse
import json
import os
import sqlite3
import stat
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import (
    DEFAULT_DB,
    DEFAULT_DB_TIMEOUT,
    DEFAULT_LISTEN,
    DEFAULT_MAX_BODY,
    DEFAULT_API_CALL_LIMIT,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_SSE_KEEPALIVE,
    DEFAULT_SIDECAR_TIMEOUT,
    DEFAULT_PURGE_INTERVAL,
    DEFAULT_UPSTREAM,
    DEFAULT_UPSTREAM_TIMEOUT,
    ConfigError,
    CONFIG_FIELDS,
    load_settings,
    resolve_sources,
    settings_values,
    validate_listen,
    validate_upstream,
    write_config,
)
from .headers import sanitize
from .pi_messages import PiMessagesState, normalize_usage, split_model_id
from .sidecar import SidecarProcess, SidecarStartupError

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
HOP_BY_HOP = {"transfer-encoding", "connection", "content-length"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
DEFAULT_DB = DEFAULT_DB
DEFAULT_LISTEN = DEFAULT_LISTEN
DEFAULT_UPSTREAM = DEFAULT_UPSTREAM
CONFIG_LOCK = threading.RLock()


class NoRedirectHandler(HTTPRedirectHandler):
    """Do not carry credentials to an URL selected by an upstream redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


UPSTREAM_OPENER = build_opener(NoRedirectHandler)


def open_url(request, timeout):
    try:
        return UPSTREAM_OPENER.open(request, timeout=timeout)
    finally:
        # urllib keeps request headers on the Request object while the
        # response is consumed; drop credential fields as soon as the
        # connection has been established or failed.
        for header_map in (getattr(request, "headers", {}), getattr(request, "unredirected_hdrs", {})):
            for key in list(header_map):
                if key.lower() in {"authorization", "proxy-authorization"}:
                    del header_map[key]


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
        CREATE TABLE IF NOT EXISTS change_log(id INTEGER PRIMARY KEY AUTOINCREMENT,resource TEXT NOT NULL,changed_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS calls_created_idx ON calls(created_at);
        CREATE INDEX IF NOT EXISTS change_log_id_idx ON change_log(id);
        CREATE INDEX IF NOT EXISTS change_log_changed_at_idx ON change_log(changed_at);
        CREATE TRIGGER IF NOT EXISTS calls_change_insert AFTER INSERT ON calls BEGIN INSERT INTO change_log(resource,changed_at) VALUES('calls',CURRENT_TIMESTAMP); END;
        CREATE TRIGGER IF NOT EXISTS calls_change_update AFTER UPDATE ON calls BEGIN INSERT INTO change_log(resource,changed_at) VALUES('calls',CURRENT_TIMESTAMP); END;
        CREATE TRIGGER IF NOT EXISTS calls_change_delete AFTER DELETE ON calls BEGIN INSERT INTO change_log(resource,changed_at) VALUES('calls',CURRENT_TIMESTAMP); END;
        CREATE TRIGGER IF NOT EXISTS sessions_change_insert AFTER INSERT ON sessions BEGIN INSERT INTO change_log(resource,changed_at) VALUES('sessions',CURRENT_TIMESTAMP); END;
        CREATE TRIGGER IF NOT EXISTS sessions_change_delete AFTER DELETE ON sessions BEGIN INSERT INTO change_log(resource,changed_at) VALUES('sessions',CURRENT_TIMESTAMP); END;
        """
    )
    # Recreate this trigger so existing databases receive the complete mutable
    # column set, including last_seen_at added after the original schema.
    connection.executescript(
        """
        DROP TRIGGER IF EXISTS sessions_change_update;
        CREATE TRIGGER sessions_change_update
        AFTER UPDATE OF agent,started_at,last_seen_at,cwd,project_name,metadata_json ON sessions
        BEGIN INSERT INTO change_log(resource,changed_at) VALUES('sessions',CURRENT_TIMESTAMP); END;
        """
    )
    if connection.execute("PRAGMA user_version").fetchone()[0] < 1:
        connection.execute("PRAGMA user_version = 1")


def init(path, timeout=DEFAULT_DB_TIMEOUT):
    connection = db(path, timeout)
    schema(connection)
    connection.commit()
    connection.close()
    # Audit payloads can contain sensitive prompts; keep the local database
    # private even when the process inherits a permissive umask.
    try:
        os.chmod(path, 0o600)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode != 0o600:
            raise PermissionError(f"database mode is {oct(mode)}, expected 0o600")
    except OSError as exc:
        raise ConfigError(f"cannot secure database file {path}: {exc}") from exc


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
    raw = configured if configured is not None else os.getenv("PROMPT_HARBOR_MAX_BODY", str(DEFAULT_MAX_BODY))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"max_body has invalid value: {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"max_body has invalid value: {raw!r}")
    return value


class Handler(BaseHTTPRequestHandler):
    clients = []
    protocol_version = "HTTP/1.1"
    db_path = DEFAULT_DB
    upstream = DEFAULT_UPSTREAM
    sidecar_url = None
    sidecar_token = None
    session_id = None
    db_timeout = DEFAULT_DB_TIMEOUT
    capture_max_body = None
    upstream_timeout = DEFAULT_UPSTREAM_TIMEOUT
    sidecar_timeout = DEFAULT_SIDECAR_TIMEOUT
    sse_keepalive = DEFAULT_SSE_KEEPALIVE
    api_call_limit = DEFAULT_API_CALL_LIMIT
    ui_path = None
    security_warnings = ()
    config_path = "prompt-harbor.ini"
    config_settings = {}
    config_sources = {}
    gateway_server = None

    @staticmethod
    def _json_error(status, message):
        raw = json.dumps({"error": {"type": "invalid_request", "message": message}}, ensure_ascii=False).encode()
        return raw

    def _send_payload(self, status, raw, content_type="application/json; charset=utf-8", *, api=False, cache_control=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if api:
            self.send_header("Cache-Control", cache_control or "no-store")
        elif cache_control:
            self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(raw)

    def _reject_untrusted_host(self):
        """Reject browser requests whose Host does not identify this loopback service."""
        host = self.headers.get("Host")
        if not host:
            return False
        try:
            from urllib.parse import urlsplit
            hostname = urlsplit("//" + host).hostname
        except ValueError:
            hostname = None
        if hostname and hostname.lower().rstrip(".") in {"127.0.0.1", "localhost", "::1"}:
            return False
        self._send_payload(403, self._json_error(403, "untrusted Host header"), api=self.path.startswith("/api/"))
        return True

    def do_POST(self):
        if self._reject_untrusted_host():
            return
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

    def _forget_authorization(self):
        """Remove client credentials from the request object after capture."""
        for key in list(self.headers):
            if key.lower() in {"authorization", "proxy-authorization"}:
                del self.headers[key]

    def _reject_redirect(self, response, record, error_type="upstream_redirect"):
        """Never expose an upstream Location to clients that may follow it."""
        if response.status not in REDIRECT_STATUSES:
            return False
        record["status"] = 502
        record["state"] = "failed"
        record["error_type"] = error_type
        record["error_message"] = "upstream redirect refused"
        record["response_complete"] = True
        self.send_error(502, "upstream redirect refused")
        self.close_connection = True
        return True

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

    def _begin_configuration_failure(self, body, model, stream, provider, api_family, target, error, input_bytes=None):
        """Persist a terminal lifecycle even when request-time capture config is invalid."""
        started = time.time()
        created = iso(started)
        request_size = len(body) if input_bytes is None else input_bytes
        connection = db(self.db_path, self.db_timeout)
        connection.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (created, self.session_id))
        cursor = connection.execute(
            "INSERT INTO calls(session_id,created_at,provider,api_family,endpoint,model,stream,status,input_bytes,error_type,error_message) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (self.session_id, created, provider, api_family, self.path, model, int(bool(stream)), "failed", request_size, "configuration_error", str(error)),
        )
        call_id = cursor.lastrowid
        cursor = connection.execute(
            "INSERT INTO attempts(call_id,attempt_no,started_at,completed_at,upstream_url,status,status_code,request_headers_json,input_bytes,error_type,error_message) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (call_id, 1, created, created, target, "failed", 400, json.dumps(headers(self.headers), ensure_ascii=False), request_size, "configuration_error", str(error)),
        )
        attempt_id = cursor.lastrowid
        connection.execute(
            "INSERT INTO payloads(attempt_id,request_body,request_content_type,response_complete,request_truncated,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (attempt_id, body[:DEFAULT_MAX_BODY], self.headers.get("Content-Type"), 1, 0, created, created),
        )
        connection.commit()
        connection.close()
        return {
            "started": started, "call_id": call_id, "attempt_id": attempt_id, "limit": DEFAULT_MAX_BODY,
            "out": bytearray(), "total_out": 0, "response_truncated": False, "first": None,
            "status": 400, "response_headers": {}, "response_content_type": "application/json",
            "state": "failed", "error_type": "configuration_error", "error_message": str(error), "response_complete": True,
        }

    def _send_json_error(self, status, message):
        raw = self._json_error(status, message)
        self._send_payload(status, raw, api=self.path.startswith("/api/"))
        self.wfile.flush()

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
        self._notify_resources({"calls"})

    @staticmethod
    def _event_bytes(resource):
        return f'event: invalidate\ndata: {{"resource":"{resource}"}}\n\n'.encode()

    def _send_event(self, resource):
        self.wfile.write(self._event_bytes(resource))
        self.wfile.flush()

    def _notify_resources(self, resources):
        for client in list(self.clients):
            try:
                for resource in sorted(resources):
                    client._send_event(resource)
                client._sse_last_notified = getattr(client, "_sse_last_notified", set()) | set(resources)
            except Exception:
                try:
                    self.clients.remove(client)
                except ValueError:
                    pass

    def _handle_openai(self):
        body = self._read_body()
        _, model, stream = self._request_metadata(body)
        target = self.upstream.rstrip("/") + self.path
        try:
            record = self._begin_attempt(body, model, stream, "openai", "openai", target)
        except (ConfigError, ValueError) as exc:
            record = self._begin_configuration_failure(body, model, stream, "openai", "openai", target, exc)
            self._send_json_error(400, str(exc))
            self._finish_attempt(record)
            return
        response = None
        usage = None
        try:
            request_headers = {key: value for key, value in self.headers.items() if key.lower() != "host"}
            try:
                response = open_url(Request(target, data=body, method=self.command, headers=request_headers), timeout=self.upstream_timeout)
            except HTTPError as exc:
                response = exc
            finally:
                request_headers.clear()
                self._forget_authorization()
            record["status"] = response.status
            record["response_headers"] = headers(response.headers)
            record["response_content_type"] = response.headers.get("Content-Type")
            if self._reject_redirect(response, record):
                return
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
        try:
            record = self._begin_attempt(safe_body, model, True, provider or "pi-ai", "pi-messages", target, input_bytes=len(body))
        except (ConfigError, ValueError) as exc:
            record = self._begin_configuration_failure(safe_body, model, True, provider or "pi-ai", "pi-messages", target, exc, input_bytes=len(body))
            self._send_json_error(400, str(exc))
            self._finish_attempt(record)
            return
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
                response = open_url(Request(target, data=safe_body, method="POST", headers=request_headers), timeout=self.upstream_timeout)
            except HTTPError as exc:
                response = exc
            finally:
                request_headers.clear()
                self._forget_authorization()
            record["status"] = response.status
            record["response_headers"] = headers(response.headers)
            record["response_content_type"] = response.headers.get("Content-Type")
            if self._reject_redirect(response, record, "sidecar_redirect"):
                return
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
        if self._reject_untrusted_host():
            return
        if self.path == "/models":
            self._proxy_sidecar_get("/models")
            return
        if self.path == "/health":
            self._proxy_sidecar_get("/health")
            return
        if self.path == "/api/config":
            self._send_config()
            return
        if self.path in ("/", "/index.html"):
            path = self.ui_path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "index.html")
            try:
                raw = open(path, "rb").read()
            except OSError:
                self.send_error(404)
                return
            self._send_payload(200, raw, "text/html; charset=utf-8"); return
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
            raw = json.dumps(value, ensure_ascii=False).encode(); self._send_payload(200, raw, api=True); return
        if self.path == "/api/events":
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-store"); self.send_header("Connection", "keep-alive"); self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'"); self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Referrer-Policy", "no-referrer"); self.end_headers(); self.wfile.write(b"event: ready\ndata: {}\n\n"); self.wfile.flush(); self.clients.append(self)
            self._sse_connection = db(self.db_path, self.db_timeout)
            self._sse_change_id = self._sse_connection.execute("SELECT COALESCE(MAX(id), 0) FROM change_log").fetchone()[0]
            self._sse_last_notified = set()
            try:
                while True:
                    interval = max(0.01, float(self.sse_keepalive))
                    time.sleep(interval)
                    changes = self._sse_connection.execute(
                        "SELECT id,resource FROM change_log WHERE id>? ORDER BY id", (self._sse_change_id,)
                    ).fetchall()
                    if changes:
                        self._sse_change_id = changes[-1][0]
                        resources = {row[1] for row in changes}
                        resources -= self._sse_last_notified
                        for resource in sorted(resources):
                            self._send_event(resource)
                        self._sse_last_notified = set()
                    self.wfile.write(b": keepalive\n\n"); self.wfile.flush()
            except Exception:
                if self in self.clients: self.clients.remove(self)
            finally:
                self._sse_connection.close()
                self._sse_connection = None
            return
        if self.path not in ("/api/overview", "/api/calls", "/api/sessions"):
            if self.path.startswith("/api/"): self.send_error(404); return
            self.static(); return
        connection = db(self.db_path, self.db_timeout)
        calls = [dict(row) for row in connection.execute("SELECT id,session_id,created_at,completed_at,endpoint,model,status,status_code,duration_ms,input_bytes,output_bytes,error_type FROM calls ORDER BY id DESC LIMIT ?", (self.api_call_limit,))]
        sessions = [dict(row) for row in connection.execute("SELECT s.id,s.agent,s.started_at,s.last_seen_at,s.cwd,s.project_name,(SELECT COUNT(*) FROM calls x WHERE x.session_id=s.id) AS call_count FROM sessions s ORDER BY s.id DESC")]
        connection.close(); payload = {"calls": calls, "sessions": sessions}
        if self.path == "/api/overview":
            payload["security_warnings"] = list(self.security_warnings)
        if self.path == "/api/calls": payload = {"calls": calls}
        if self.path == "/api/sessions": payload = {"sessions": sessions}
        raw = json.dumps(payload, ensure_ascii=False).encode(); self._send_payload(200, raw, api=True)

    def do_PUT(self):
        if self._reject_untrusted_host():
            return
        if self.path != "/api/config":
            self.send_error(404)
            return
        if self.headers.get("X-Prompt-Harbor-Request") != "1":
            self._send_payload(403, self._json_error(403, "missing X-Prompt-Harbor-Request header"), api=True)
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > 1024 * 1024:
                raise ValueError("configuration payload is too large")
            payload = json.loads(self.rfile.read(max(0, length)))
            if not isinstance(payload, dict):
                raise ValueError("configuration payload must be an object")
            result = update_runtime_config(payload)
        except (ConfigError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raw = self._json_error(400, str(exc))
            self._send_payload(400, raw, api=True)
            return
        raw = json.dumps(result, ensure_ascii=False).encode()
        self._send_payload(200, raw, api=True)

    def _send_config(self):
        values = dict(self.config_settings)
        fields = {}
        for name, spec in CONFIG_FIELDS.items():
            value = values.get(name)
            fields[name] = {
                "value": None if spec.get("secret") else value,
                "configured": bool(value) if spec.get("secret") else True,
                "restart_required": spec["restart_required"],
                "section": spec["section"],
                "key": spec["key"],
                "secret": bool(spec.get("secret")),
                "source": self.config_sources.get(name, "default"),
            }
        raw = json.dumps({"path": self.config_path, "fields": fields}, ensure_ascii=False).encode()
        self._send_payload(200, raw, api=True)

    def _proxy_sidecar_get(self, path):
        if not self.sidecar_url:
            self.send_error(503, "pi-ai sidecar unavailable"); return
        response = None
        try:
            request_headers = {"Accept": "application/json"}
            if self.sidecar_token: request_headers["Authorization"] = "Bearer " + self.sidecar_token
            try:
                response = open_url(Request(self.sidecar_url.rstrip("/") + path, headers=request_headers), timeout=self.sidecar_timeout)
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
        self._send_payload(200, raw, CONTENT_TYPES[extension], cache_control="no-cache")

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        """Keep error responses under the same browser security policy."""
        short = message or self.responses.get(code, ("",))[0]
        body = f"<html><head><title>Error {code}</title></head><body><h1>Error {code}</h1><p>{short}</p></body></html>".encode()
        self._send_payload(code, body, "text/html; charset=utf-8", api=self.path.startswith("/api/"), cache_control="no-store" if self.path.startswith("/api/") else None)


def _config_updates(payload):
    """Flatten either the UI's sectioned payload or a flat API payload."""
    updates = {}
    for field, spec in CONFIG_FIELDS.items():
        if field in payload:
            updates[field] = payload[field]
            continue
        section = payload.get(spec["section"])
        if isinstance(section, dict) and spec["key"] in section:
            updates[field] = section[spec["key"]]
    return updates


def _apply_runtime_settings(settings):
    """Apply settings whose resources are owned by the running gateway."""
    Handler.upstream = settings.upstream
    Handler.security_warnings = settings.upstream_warnings
    Handler.capture_max_body = settings.max_body
    Handler.db_timeout = settings.db_timeout
    Handler.upstream_timeout = settings.upstream_timeout
    Handler.sidecar_timeout = settings.sidecar_timeout
    Handler.sse_keepalive = settings.sse_keepalive
    Handler.api_call_limit = settings.api_call_limit
    Handler.ui_path = settings.ui_path
    server = Handler.gateway_server
    if server is not None:
        server._purge_config.update({
            "retention_days": settings.retention_days,
            "interval": settings.purge_interval,
            "timeout": settings.db_timeout,
        })


def update_runtime_config(payload):
    with CONFIG_LOCK:
        return _update_runtime_config(payload)


def _update_runtime_config(payload):
    """Persist a partial configuration update and hot-apply safe fields."""
    updates = _config_updates(payload)
    if not updates:
        raise ConfigError("configuration update is empty")
    values = dict(Handler.config_settings)
    if not values:
        raise ConfigError("gateway configuration is not initialized")
    for field, value in updates.items():
        source = Handler.config_sources.get(field, "default")
        current = values.get(field)
        if source in {"cli", "env"} and str(value) != str(current):
            raise ConfigError(f"{field} is locked by {source} override")
        if field == "sidecar_token" and value == "":
            value = None
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ConfigError(f"{field} has invalid value: {value!r}")
        values[field] = value
    settings = write_config(Handler.config_path, values)
    Handler.config_settings = settings_values(settings)
    Handler.config_sources = {
        field: (source if source in {"cli", "env"} else "ini")
        for field, source in Handler.config_sources.items()
    }
    _apply_runtime_settings(settings)
    if "sidecar_url" in updates:
        Handler.sidecar_url = settings.sidecar_url
    if "sidecar_token" in updates:
        Handler.sidecar_token = settings.sidecar_token
    restart_required = sorted(field for field in updates if CONFIG_FIELDS[field]["restart_required"])
    public = {
        field: {"value": None if CONFIG_FIELDS[field].get("secret") else getattr(settings, field),
                "configured": bool(getattr(settings, field)) if CONFIG_FIELDS[field].get("secret") else True,
                "restart_required": CONFIG_FIELDS[field]["restart_required"],
                "section": CONFIG_FIELDS[field]["section"], "key": CONFIG_FIELDS[field]["key"],
                "secret": bool(CONFIG_FIELDS[field].get("secret"))}
                | {"source": Handler.config_sources.get(field, "default")}
        for field in CONFIG_FIELDS
    }
    return {"path": Handler.config_path, "fields": public, "restart_required": restart_required}


def purge(path, retention_days=DEFAULT_RETENTION_DAYS, timeout=DEFAULT_DB_TIMEOUT):
    from .database import purge_calls
    connection = db(path, timeout)
    count = purge_calls(connection, retention_days)
    cursors = [
        getattr(client, "_sse_change_id")
        for client in list(getattr(Handler, "clients", []))
        if os.fspath(getattr(client, "db_path", "")) == os.fspath(path)
        and isinstance(getattr(client, "_sse_change_id", None), int)
    ]
    if cursors:
        # Every active client has consumed all entries up to its cursor. Keep
        # anything newer than the slowest client so polling cannot skip it.
        connection.execute("DELETE FROM change_log WHERE id <= ?", (min(cursors),))
    else:
        # With no subscribers, retention bounds the append-only log on disk.
        connection.execute(
            "DELETE FROM change_log WHERE julianday(changed_at) < julianday('now', ?)",
            (f"-{retention_days} days",),
        )
    connection.commit()
    connection.close()
    return count


class GatewayHTTPServer(ThreadingHTTPServer):
    """Threading server with explicit ownership of the retention worker."""

    daemon_threads = True

    def handle_error(self, request, client_address):
        exc_type = sys.exc_info()[0]
        if exc_type in (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        super().handle_error(request, client_address)

    def server_close(self):
        stop_purge_worker(self)
        super().server_close()


def start_purge_worker(server, path, retention_days=DEFAULT_RETENTION_DAYS, interval=DEFAULT_PURGE_INTERVAL, timeout=DEFAULT_DB_TIMEOUT):
    stop = threading.Event()
    server._purge_config = {"retention_days": retention_days, "interval": interval, "timeout": timeout}

    def run():
        while not stop.wait(max(0.01, float(server._purge_config["interval"]))):
            try:
                config = server._purge_config
                purge(path, config["retention_days"], config["timeout"])
            except Exception:
                # A transient SQLite lock must not terminate the gateway's cleanup loop.
                continue

    thread = threading.Thread(target=run, name="prompt-harbor-purge", daemon=True)
    server._purge_stop = stop
    server._purge_thread = thread
    thread.start()
    return thread


def stop_purge_worker(server):
    stop = getattr(server, "_purge_stop", None)
    thread = getattr(server, "_purge_thread", None)
    if stop is None:
        return
    stop.set()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=2)
    server._purge_stop = None
    server._purge_thread = None


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
    parser.add_argument("--purge-interval", dest="purge_interval", type=float, default=default)
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
    Handler.clients = []
    Handler.db_path = path; Handler.upstream = settings.upstream; Handler.security_warnings = settings.upstream_warnings; Handler.sidecar_url = configured_sidecar; Handler.sidecar_token = settings.sidecar_token; Handler.session_id = session_id
    Handler.db_timeout = settings.db_timeout; Handler.capture_max_body = settings.max_body; Handler.upstream_timeout = settings.upstream_timeout; Handler.sidecar_timeout = settings.sidecar_timeout; Handler.sse_keepalive = settings.sse_keepalive; Handler.api_call_limit = settings.api_call_limit; Handler.ui_path = settings.ui_path
    Handler.config_path = settings.config_path
    Handler.config_settings = settings_values(settings)
    Handler.config_sources = resolve_sources(args)
    host, port = validate_listen(settings.listen); print(f"gateway listening on http://{settings.listen}, upstream {settings.upstream}", flush=True)
    for warning in settings.upstream_warnings:
        print("WARNING: " + warning, file=sys.stderr, flush=True)
    purge(path, settings.retention_days, settings.db_timeout)
    server = GatewayHTTPServer((host, port), Handler)
    Handler.gateway_server = server
    start_purge_worker(server, path, settings.retention_days, settings.purge_interval, settings.db_timeout)
    try: server.serve_forever()
    finally: server.server_close(); manager.stop()


if __name__ == "__main__":
    main()
