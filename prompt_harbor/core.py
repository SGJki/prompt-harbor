#!/usr/bin/env python3
"""PromptHarbor gateway and command-line entrypoint."""

import argparse
import html
import json
import os
import sqlite3
import stat
import sys
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlparse, urlsplit
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
from .events import commit_then_notify
from .identity import IdentityRequiredError, resolve_identity
from .lifecycle import call_outcome
from .storage import configure as configure_db
from .transport import CaptureBudget, capture_chunk
from .pi_messages import PiMessagesState, normalize_usage, split_model_id
from .sidecar import SidecarProcess, SidecarStartupError

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
HOP_BY_HOP = {
    "connection", "content-length", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "proxy-connection", "te", "trailer",
    "transfer-encoding", "upgrade",
}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
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
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts is not None else datetime.now(timezone.utc).isoformat()


def db(path, timeout=DEFAULT_DB_TIMEOUT):
    return configure_db(sqlite3.connect(path, timeout=timeout), timeout)


def schema(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS runtime_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT,agent TEXT,started_at TEXT,last_seen_at TEXT,cwd TEXT,project_name TEXT,metadata_json TEXT);
        CREATE TABLE IF NOT EXISTS client_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT,client_session_id TEXT NOT NULL,identity_status TEXT NOT NULL,identity_source TEXT NOT NULL,first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,metadata_json TEXT NOT NULL DEFAULT '{}');
        CREATE UNIQUE INDEX IF NOT EXISTS client_sessions_explicit_idx ON client_sessions(client_session_id) WHERE identity_status='explicit';
        CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY AUTOINCREMENT,agent TEXT,started_at TEXT,last_seen_at TEXT,cwd TEXT,project_name TEXT,metadata_json TEXT);
        CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id INTEGER,runtime_session_id INTEGER,client_session_row_id INTEGER,thread_id TEXT,request_correlation_id TEXT,provider_session_context TEXT,created_at TEXT,completed_at TEXT,provider TEXT,api_family TEXT,endpoint TEXT,model TEXT,stream INTEGER,status TEXT,status_code INTEGER,first_byte_at TEXT,duration_ms INTEGER,input_bytes INTEGER DEFAULT 0,output_bytes INTEGER DEFAULT 0,error_type TEXT,error_message TEXT);
        CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,call_id INTEGER,attempt_no INTEGER,started_at TEXT,completed_at TEXT,upstream_url TEXT,status TEXT,status_code INTEGER,request_headers_json TEXT,response_headers_json TEXT,first_byte_at TEXT,duration_ms INTEGER,input_bytes INTEGER DEFAULT 0,output_bytes INTEGER DEFAULT 0,error_type TEXT,error_message TEXT);
        CREATE TABLE IF NOT EXISTS payloads(id INTEGER PRIMARY KEY AUTOINCREMENT,attempt_id INTEGER UNIQUE,request_body BLOB,response_body BLOB,request_content_type TEXT,response_content_type TEXT,response_complete INTEGER DEFAULT 0,request_truncated INTEGER DEFAULT 0,response_truncated INTEGER DEFAULT 0,capture_degraded INTEGER DEFAULT 0,created_at TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY AUTOINCREMENT,attempt_id INTEGER UNIQUE,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,raw_usage_json TEXT);
        CREATE TABLE IF NOT EXISTS change_log(id INTEGER PRIMARY KEY AUTOINCREMENT,resource TEXT NOT NULL,changed_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS calls_created_idx ON calls(created_at);
        CREATE INDEX IF NOT EXISTS calls_runtime_session_idx ON calls(runtime_session_id,created_at);
        CREATE INDEX IF NOT EXISTS calls_client_session_idx ON calls(client_session_row_id,created_at);
        CREATE INDEX IF NOT EXISTS calls_thread_idx ON calls(thread_id);
        CREATE INDEX IF NOT EXISTS calls_request_correlation_idx ON calls(request_correlation_id);
        CREATE INDEX IF NOT EXISTS change_log_id_idx ON change_log(id);
        CREATE INDEX IF NOT EXISTS change_log_changed_at_idx ON change_log(changed_at);
        CREATE TRIGGER IF NOT EXISTS calls_change_insert AFTER INSERT ON calls BEGIN INSERT INTO change_log(resource,changed_at) VALUES('calls',CURRENT_TIMESTAMP); END;
        CREATE TRIGGER IF NOT EXISTS calls_change_update AFTER UPDATE ON calls BEGIN INSERT INTO change_log(resource,changed_at) VALUES('calls',CURRENT_TIMESTAMP); END;
        CREATE TRIGGER IF NOT EXISTS calls_change_delete AFTER DELETE ON calls BEGIN INSERT INTO change_log(resource,changed_at) VALUES('calls',CURRENT_TIMESTAMP); END;
        CREATE TRIGGER IF NOT EXISTS sessions_change_insert AFTER INSERT ON sessions BEGIN INSERT INTO change_log(resource,changed_at) VALUES('sessions',CURRENT_TIMESTAMP); END;
        CREATE TRIGGER IF NOT EXISTS sessions_change_delete AFTER DELETE ON sessions BEGIN INSERT INTO change_log(resource,changed_at) VALUES('sessions',CURRENT_TIMESTAMP); END;
        CREATE TRIGGER IF NOT EXISTS runtime_sessions_change_insert AFTER INSERT ON runtime_sessions BEGIN INSERT INTO change_log(resource,changed_at) VALUES('runtime-sessions',CURRENT_TIMESTAMP); END;
        CREATE TRIGGER IF NOT EXISTS runtime_sessions_change_update AFTER UPDATE ON runtime_sessions BEGIN INSERT INTO change_log(resource,changed_at) VALUES('runtime-sessions',CURRENT_TIMESTAMP); END;
        CREATE TRIGGER IF NOT EXISTS runtime_sessions_change_delete AFTER DELETE ON runtime_sessions BEGIN INSERT INTO change_log(resource,changed_at) VALUES('runtime-sessions',CURRENT_TIMESTAMP); END;
        CREATE TRIGGER IF NOT EXISTS client_sessions_change_insert AFTER INSERT ON client_sessions BEGIN INSERT INTO change_log(resource,changed_at) VALUES('client-sessions',CURRENT_TIMESTAMP); END;
        CREATE TRIGGER IF NOT EXISTS client_sessions_change_update AFTER UPDATE ON client_sessions BEGIN INSERT INTO change_log(resource,changed_at) VALUES('client-sessions',CURRENT_TIMESTAMP); END;
        CREATE TRIGGER IF NOT EXISTS client_sessions_change_delete AFTER DELETE ON client_sessions BEGIN INSERT INTO change_log(resource,changed_at) VALUES('client-sessions',CURRENT_TIMESTAMP); END;
        """
    )
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
    if os.path.exists(path):
        with closing(sqlite3.connect(path)) as probe:
            tables = {row[0] for row in probe.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            call_columns = {row[1] for row in probe.execute("PRAGMA table_info(calls)")} if "calls" in tables else set()
            payload_columns = {row[1] for row in probe.execute("PRAGMA table_info(payloads)")} if "payloads" in tables else set()
            legacy = "runtime_sessions" not in tables or "client_sessions" not in tables or {
                "runtime_session_id", "client_session_row_id", "thread_id", "request_correlation_id", "provider_session_context",
            } - call_columns or "capture_degraded" not in payload_columns
        if legacy:
            # The project is pre-deployment: discard the pre-feature schema
            # instead of attempting a partial migration.
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(path + suffix)
                except FileNotFoundError:
                    pass
    with closing(db(path, timeout)) as connection:
        schema(connection)
        connection.commit()
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


def hop_by_hop_headers(header_map):
    excluded = set(HOP_BY_HOP)
    for value in header_map.get_all("Connection", []):
        excluded.update(token.strip().lower() for token in value.split(",") if token.strip())
    return excluded


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


def finalize_pi_stream(record, state, *, sidecar_crashed=False):
    """Apply protocol outcome without overwriting a managed-child crash."""
    state.finish()
    if sidecar_crashed:
        return
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
    runtime_session_id = None
    session_id = None  # legacy mirror; new API semantics use runtime_session_id
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
    sidecar_managed = False
    sidecar_manager = None
    gateway_server = None
    identity_mode = "default"
    capture_budget_limit = DEFAULT_MAX_BODY * 4
    capture_budget = None

    @staticmethod
    def _json_error(message):
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
        values = self.headers.get_all("Host", [])
        trusted = False
        try:
            if len(values) == 1:
                parsed = urlsplit("//" + values[0])
                hostname = parsed.hostname.lower().rstrip(".") if parsed.hostname else ""
                parsed.port  # Validate the optional port before trusting the authority.
                trusted = (
                    not parsed.username
                    and not parsed.password
                    and not parsed.path
                    and not parsed.query
                    and not parsed.fragment
                    and hostname in {"127.0.0.1", "localhost", "::1"}
                )
        except (AttributeError, ValueError):
            pass
        if trusted:
            return False
        path = getattr(self, "path", "")
        self._send_payload(403, self._json_error("untrusted Host header"), api=path.startswith("/api/"))
        self.close_connection = True
        return True

    def do_POST(self):
        if self._reject_untrusted_host():
            return
        try:
            body = self._read_body()
        except ValueError as exc:
            self._send_json_error(400, str(exc))
            self.close_connection = True
            return
        if self.path == "/messages":
            self._handle_pi_messages(body)
        else:
            self._handle_openai(body)

    def _read_body(self, max_length=None):
        if self.headers.get_all("Transfer-Encoding", []):
            raise ValueError("Transfer-Encoding is not supported")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1:
            raise ValueError("multiple Content-Length headers are not supported")
        raw_length = lengths[0].strip() if lengths else "0"
        if not raw_length.isdigit():
            raise ValueError("invalid Content-Length header")
        length = int(raw_length)
        if max_length is not None and length > max_length:
            raise ValueError("request body is too large")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("incomplete request body")
        return body

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
        identity = resolve_identity(self.headers, body, strict=self.identity_mode == "strict")
        from .models import resolve_client_session
        with closing(db(self.db_path, self.db_timeout)) as connection:
            client_session_row_id = resolve_client_session(connection, identity.client_session_id, identity.identity_status, identity.identity_source, created)
            connection.execute("UPDATE runtime_sessions SET last_seen_at=? WHERE id=?", (created, self.runtime_session_id))
            connection.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (created, self.runtime_session_id))
            cursor = connection.execute(
                "INSERT INTO calls(session_id,runtime_session_id,client_session_row_id,thread_id,request_correlation_id,provider_session_context,created_at,provider,api_family,endpoint,model,stream,status,input_bytes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.runtime_session_id, self.runtime_session_id, client_session_row_id, identity.thread_id, identity.request_correlation_id, identity.provider_session_context, created, provider, api_family, self.path, model, int(bool(stream)), "running", request_size),
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
        return {
            "started": started, "call_id": call_id, "attempt_id": attempt_id, "limit": limit,
            "out": bytearray(), "total_out": 0, "response_truncated": False, "first": None,
            "status": None, "response_headers": {}, "response_content_type": None,
            "state": "failed", "error_type": None, "error_message": None, "response_complete": False,
            "capture_degraded": False, "client_session_row_id": client_session_row_id, "capture_budget": self.capture_budget,
        }

    def _begin_configuration_failure(self, body, model, stream, provider, api_family, target, error, input_bytes=None):
        """Persist a terminal lifecycle even when request-time capture config is invalid."""
        started = time.time()
        created = iso(started)
        request_size = len(body) if input_bytes is None else input_bytes
        # Configuration failures still belong to both explicit session scopes;
        # use default identity resolution so a malformed request is not left
        # without an auditable client-session row.
        identity = resolve_identity(self.headers, body, strict=False)
        from .models import resolve_client_session
        with closing(db(self.db_path, self.db_timeout)) as connection:
            client_session_row_id = resolve_client_session(connection, identity.client_session_id, identity.identity_status, identity.identity_source, created)
            connection.execute("UPDATE runtime_sessions SET last_seen_at=? WHERE id=?", (created, self.runtime_session_id))
            connection.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (created, self.runtime_session_id))
            cursor = connection.execute(
                "INSERT INTO calls(session_id,runtime_session_id,client_session_row_id,thread_id,request_correlation_id,provider_session_context,created_at,provider,api_family,endpoint,model,stream,status,input_bytes,error_type,error_message) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.runtime_session_id, self.runtime_session_id, client_session_row_id, identity.thread_id, identity.request_correlation_id, identity.provider_session_context, created, provider, api_family, self.path, model, int(bool(stream)), "failed", request_size, "configuration_error", str(error)),
            )
            call_id = cursor.lastrowid
            cursor = connection.execute(
                "INSERT INTO attempts(call_id,attempt_no,started_at,completed_at,upstream_url,status,status_code,request_headers_json,input_bytes,error_type,error_message) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (call_id, 1, created, created, target, "failed", 400, json.dumps(headers(self.headers), ensure_ascii=False), request_size, "configuration_error", str(error)),
            )
            attempt_id = cursor.lastrowid
            connection.execute("INSERT INTO payloads(attempt_id,request_body,request_content_type,response_complete,request_truncated,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (attempt_id, body[:DEFAULT_MAX_BODY], self.headers.get("Content-Type"), 1, 0, created, created))
            connection.commit()
        return {
            "started": started, "call_id": call_id, "attempt_id": attempt_id, "limit": DEFAULT_MAX_BODY,
            "out": bytearray(), "total_out": 0, "response_truncated": False, "first": None,
            "status": 400, "response_headers": {}, "response_content_type": "application/json",
            "state": "failed", "error_type": "configuration_error", "error_message": str(error), "response_complete": True, "capture_degraded": False,
            "capture_budget": self.capture_budget,
        }

    def _send_json_error(self, status, message):
        raw = self._json_error(message)
        self._send_payload(status, raw, api=self.path.startswith("/api/"))
        self.wfile.flush()

    def _capture_chunk(self, record, chunk):
        capture_chunk(record, chunk, record.get("capture_budget", self.capture_budget))

    @staticmethod
    def _response_content_length(header_map):
        values = header_map.get_all("Content-Length", [])
        if not values:
            return None
        if len(values) != 1 or not values[0].strip().isdigit():
            raise ValueError("invalid upstream Content-Length header")
        return int(values[0].strip())

    def _persist_attempt_once(self, record, usage=None, raw_usage=None):
        done = time.time()
        values = (
            iso(done), record["state"], record["status"], json.dumps(record["response_headers"], ensure_ascii=False),
            iso(record["first"]) if record["first"] else None, int((done - record["started"]) * 1000),
            record["total_out"], record["error_type"], record["error_message"], record["attempt_id"],
        )
        with closing(db(self.db_path, self.db_timeout)) as connection:
            connection.execute("UPDATE attempts SET completed_at=?,status=?,status_code=?,response_headers_json=?,first_byte_at=?,duration_ms=?,output_bytes=?,error_type=?,error_message=? WHERE id=?", values)
            connection.execute("UPDATE calls SET completed_at=?,status=?,status_code=?,first_byte_at=?,duration_ms=?,output_bytes=?,error_type=?,error_message=? WHERE id=?", values[:1] + values[1:2] + values[2:3] + values[4:9] + (record["call_id"],))
            connection.execute("UPDATE payloads SET response_body=?,response_content_type=?,response_complete=?,response_truncated=?,capture_degraded=?,updated_at=? WHERE attempt_id=?", (bytes(record["out"]), record["response_content_type"], int(record["response_complete"]), int(record["response_truncated"]), int(record.get("capture_degraded", False)), iso(done), record["attempt_id"]))
            normalized = normalize_usage(usage)
            if normalized:
                raw_value = raw_usage if isinstance(raw_usage, dict) else usage if isinstance(usage, dict) else normalized
                connection.execute("INSERT OR REPLACE INTO usage(attempt_id,input_tokens,output_tokens,total_tokens,raw_usage_json) VALUES(?,?,?,?,?)", (record["attempt_id"], normalized.get("input_tokens"), normalized.get("output_tokens"), normalized.get("total_tokens"), json.dumps(raw_value, ensure_ascii=False)))
            commit_then_notify(connection, self._notify_resources, {"calls"})
        capture_budget = record.get("capture_budget", self.capture_budget)
        if capture_budget is not None:
            capture_budget.release(len(record.get("out", b"")))

    def _finish_attempt(self, record, usage=None, raw_usage=None):
        """Persist terminal state with bounded retries for transient SQLite locks."""
        from .storage import with_retry
        try:
            with_retry(lambda: self._persist_attempt_once(record, usage, raw_usage), attempts=5)
        except sqlite3.OperationalError as exc:
            # Forwarding has already completed. Do not turn an exhausted audit
            # retry into a request-thread failure or retain the capture buffer.
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            capture_budget = record.get("capture_budget", self.capture_budget)
            if capture_budget is not None:
                capture_budget.release(len(record.get("out", b"")))
            record["audit_persistence_degraded"] = True

    @staticmethod
    def _event_bytes(resource):
        return f'event: invalidate\ndata: {{"resource":"{resource}"}}\n\n'.encode()

    def _send_event(self, resource):
        self._write_sse(self._event_bytes(resource))

    def _write_sse(self, payload):
        with self._sse_write_lock:
            self.wfile.write(payload)
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

    def _handle_openai(self, body):
        _, model, stream = self._request_metadata(body)
        target = self.upstream.rstrip("/") + self.path
        try:
            record = self._begin_attempt(body, model, stream, "openai", "openai", target)
        except IdentityRequiredError as exc:
            self._send_json_error(400, str(exc))
            self.close_connection = True
            return
        except (ConfigError, ValueError) as exc:
            record = self._begin_configuration_failure(body, model, stream, "openai", "openai", target, exc)
            self._send_json_error(400, str(exc))
            self._finish_attempt(record)
            return
        response = None
        response_started = False
        usage = None
        try:
            excluded_request_headers = hop_by_hop_headers(self.headers) | {"host"}
            request_headers = {key: value for key, value in self.headers.items() if key.lower() not in excluded_request_headers}
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
            expected = self._response_content_length(response.headers)
            self.send_response(response.status)
            excluded_response_headers = hop_by_hop_headers(response.headers)
            for key, value in response.headers.items():
                if key.lower() not in excluded_response_headers:
                    self.send_header(key, value)
            self.end_headers()
            response_started = True
            while True:
                chunk = response.read(8192)
                if not chunk:
                    break
                self._capture_chunk(record, chunk)
                self.wfile.write(chunk)
                self.wfile.flush()
            record["response_complete"] = expected is None or record["total_out"] == expected
            incomplete = not record["response_complete"]
            # Transparent upstream disconnects retain the historical failed
            # call status; the error_type carries the incomplete distinction.
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
            self.close_connection = True
            if not response_started:
                record["status"] = 502
                try:
                    self.send_error(502, "upstream unavailable")
                except Exception:
                    pass
        finally:
            if response is not None:
                response.close()
            self._finish_attempt(record, usage)

    def _handle_pi_messages(self, body):
        parsed, model, _ = self._request_metadata(body)
        provider, _ = split_model_id(model)
        manager = getattr(self.gateway_server, "sidecar_manager", None)
        if manager is not None and manager.managed and not manager.alive():
            try:
                self.sidecar_url = manager.reprobe()
                self.sidecar_managed = True
                if self.gateway_server is not None:
                    self.gateway_server.sidecar_process = manager.process
            except SidecarStartupError:
                self.sidecar_url = None
        target = (self.sidecar_url.rstrip("/") + "/messages") if self.sidecar_url else "sidecar://unavailable/messages"
        safe_body = self._safe_pi_body(parsed, body) if isinstance(parsed, dict) else b""
        try:
            record = self._begin_attempt(safe_body, model, True, provider or "pi-ai", "pi-messages", target, input_bytes=len(body))
        except IdentityRequiredError as exc:
            self._send_json_error(400, str(exc))
            self.close_connection = True
            return
        except (ConfigError, ValueError) as exc:
            record = self._begin_configuration_failure(safe_body, model, True, provider or "pi-ai", "pi-messages", target, exc, input_bytes=len(body))
            self._send_json_error(400, str(exc))
            self._finish_attempt(record)
            return
        state = PiMessagesState()
        response = None
        response_started = False
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
            excluded_response_headers = hop_by_hop_headers(response.headers)
            for key, value in response.headers.items():
                if key.lower() not in excluded_response_headers:
                    self.send_header(key, value)
            self.end_headers()
            response_started = True
            sidecar_crashed = False
            while True:
                if self.sidecar_managed and getattr(self.gateway_server, "sidecar_process", None) is not None and self.gateway_server.sidecar_process.poll() is not None:
                    record["state"], record["error_type"], record["error_message"] = "failed", "sidecar_crash", "managed sidecar exited during stream"
                    record["response_complete"] = False
                    sidecar_crashed = True
                    break
                chunk = response.read(8192)
                if not chunk:
                    break
                self._capture_chunk(record, chunk)
                state.feed(chunk)
                self.wfile.write(chunk)
                self.wfile.flush()
            finalize_pi_stream(record, state, sidecar_crashed=sidecar_crashed)
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            record["state"], record["error_type"], record["error_message"] = "failed", "client_cancel", "client disconnected"
            if response is not None:
                response.close()
        except Exception as exc:
            record["state"], record["error_type"], record["error_message"] = "failed", "sidecar_unavailable", str(exc)
            self.close_connection = True
            if not response_started:
                record["status"] = 503
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
        try:
            self._read_body(max_length=0)
        except ValueError as exc:
            self._send_json_error(400, str(exc))
            self.close_connection = True
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
                with open(path, "rb") as stream:
                    raw = stream.read()
            except OSError:
                self.send_error(404)
                return
            self._send_payload(200, raw, "text/html; charset=utf-8"); return
        if self.path.startswith("/api/calls/"):
            try:
                call_id = int(self.path.rsplit("/", 1)[1])
            except (TypeError, ValueError):
                self.send_error(400); return
            with closing(db(self.db_path, self.db_timeout)) as connection:
                row = connection.execute("SELECT c.*,cs.client_session_id,a.request_headers_json,a.response_headers_json,p.request_body,p.response_body,p.request_truncated,p.response_truncated,p.capture_degraded,u.input_tokens,u.output_tokens,u.total_tokens,u.raw_usage_json FROM calls c LEFT JOIN client_sessions cs ON cs.id=c.client_session_row_id LEFT JOIN attempts a ON a.call_id=c.id LEFT JOIN payloads p ON p.attempt_id=a.id LEFT JOIN usage u ON u.attempt_id=a.id WHERE c.id=?", (call_id,)).fetchone()
            if not row:
                self.send_error(404); return
            value = dict(row)
            value["request_headers_json"] = headers(json.loads(value["request_headers_json"] or "{}")); value["response_headers_json"] = headers(json.loads(value["response_headers_json"] or "{}"))
            for key in ("request_body", "response_body"):
                if isinstance(value.get(key), bytes): value[key] = value[key].decode("utf-8", errors="replace")
            raw = json.dumps(value, ensure_ascii=False).encode(); self._send_payload(200, raw, api=True); return
        if self.path == "/api/events":
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-store"); self.send_header("Connection", "keep-alive"); self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'"); self.send_header("X-Content-Type-Options", "nosniff"); self.send_header("Referrer-Policy", "no-referrer"); self.end_headers()
            self._sse_write_lock = threading.Lock()
            self._write_sse(b"event: ready\ndata: {}\n\n")
            self.clients.append(self)
            self._sse_connection = None
            try:
                self._sse_connection = db(self.db_path, self.db_timeout)
                self._sse_change_id = self._sse_connection.execute("SELECT COALESCE(MAX(id), 0) FROM change_log").fetchone()[0]
                self._sse_last_notified = set()
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
                    self._write_sse(b": keepalive\n\n")
            except Exception:
                self.close_connection = True
            finally:
                if self in self.clients:
                    self.clients.remove(self)
                if self._sse_connection is not None:
                    self._sse_connection.close()
                self._sse_connection = None
            return
        parsed_path = urlparse(self.path)
        route = parsed_path.path
        if route == "/api/sessions":
            self.send_error(404)
            return
        if route not in ("/api/overview", "/api/calls", "/api/runtime-sessions", "/api/client-sessions"):
            if self.path.startswith("/api/"): self.send_error(404); return
            self.static(); return
        query = {key: values[0] for key, values in parse_qs(parsed_path.query).items() if values}

        def bounded_limit(raw):
            if raw in (None, ""):
                return self.api_call_limit
            try:
                value = int(raw)
            except (TypeError, ValueError):
                raise ValueError("limit must be a positive integer") from None
            if value <= 0:
                raise ValueError("limit must be a positive integer")
            return min(value, self.api_call_limit)

        try:
            limit = bounded_limit(query.get("limit"))
        except ValueError as exc:
            self._send_payload(400, self._json_error(str(exc)), api=True)
            return
        with closing(db(self.db_path, self.db_timeout)) as connection:
            params = []
            where = []
            for field in ("runtime_session_id", "thread_id", "request_correlation_id"):
                if query.get(field):
                    where.append(f"c.{field}=?"); params.append(query[field])
            if query.get("client_session_id"):
                where.append("cs.client_session_id=?"); params.append(query["client_session_id"])
            if query.get("status"):
                where.append("c.status=?"); params.append(query["status"])
            if query.get("before"):
                try:
                    before = int(query["before"])
                except ValueError:
                    self._send_payload(400, self._json_error("before must be an integer cursor"), api=True)
                    return
                where.append("c.id<?"); params.append(before)
            where_sql = " WHERE " + " AND ".join(where) if where else ""
            calls = [dict(row) for row in connection.execute("SELECT c.id,c.session_id,c.runtime_session_id,c.client_session_row_id,cs.client_session_id,c.thread_id,c.request_correlation_id,c.provider_session_context,c.created_at,c.completed_at,c.endpoint,c.model,c.status,c.status_code,c.duration_ms,c.input_bytes,c.output_bytes,c.error_type FROM calls c LEFT JOIN client_sessions cs ON cs.id=c.client_session_row_id" + where_sql + " ORDER BY c.id DESC LIMIT ?", (*params, limit))]

            runtime_where = []
            runtime_params = []
            if query.get("id"):
                try:
                    runtime_where.append("s.id=?"); runtime_params.append(int(query["id"]))
                except ValueError:
                    self._send_payload(400, self._json_error("id must be an integer"), api=True)
                    return
            if query.get("before"):
                try:
                    runtime_where.append("s.id<?"); runtime_params.append(int(query["before"]))
                except ValueError:
                    pass
            runtime_sql = " WHERE " + " AND ".join(runtime_where) if runtime_where else ""
            runtime_sessions = [dict(row) for row in connection.execute("SELECT s.id,s.agent,s.started_at,s.last_seen_at,s.cwd,s.project_name,(SELECT COUNT(*) FROM calls x WHERE x.runtime_session_id=s.id OR (x.runtime_session_id IS NULL AND x.session_id=s.id)) AS call_count FROM runtime_sessions s" + runtime_sql + " ORDER BY s.id DESC LIMIT ?", (*runtime_params, limit))]

            client_where = []
            client_params = []
            if query.get("client_session_id"):
                client_where.append("s.client_session_id=?"); client_params.append(query["client_session_id"])
            if query.get("identity_status"):
                client_where.append("s.identity_status=?"); client_params.append(query["identity_status"])
            if query.get("before"):
                try:
                    client_where.append("s.id<?"); client_params.append(int(query["before"]))
                except ValueError:
                    pass
            client_sql = " WHERE " + " AND ".join(client_where) if client_where else ""
            client_sessions = [dict(row) for row in connection.execute("SELECT s.id,s.client_session_id,s.identity_status,s.identity_source,s.first_seen_at,s.last_seen_at,(SELECT COUNT(*) FROM calls x WHERE x.client_session_row_id=s.id) AS call_count FROM client_sessions s" + client_sql + " ORDER BY s.id DESC LIMIT ?", (*client_params, limit))]
        payload = {"calls": calls, "runtime_sessions": runtime_sessions, "client_sessions": client_sessions}
        if route == "/api/overview":
            payload["security_warnings"] = list(self.security_warnings)
        if route == "/api/calls": payload = {"calls": calls}
        if route == "/api/runtime-sessions": payload = {"runtime_sessions": runtime_sessions}
        if route == "/api/client-sessions": payload = {"client_sessions": client_sessions}
        raw = json.dumps(payload, ensure_ascii=False).encode(); self._send_payload(200, raw, api=True)

    def do_PUT(self):
        if self._reject_untrusted_host():
            return
        if self.path != "/api/config":
            self.send_error(404)
            self.close_connection = True
            return
        if self.headers.get("X-Prompt-Harbor-Request") != "1":
            self._send_payload(403, self._json_error("missing X-Prompt-Harbor-Request header"), api=True)
            self.close_connection = True
            return
        try:
            payload = json.loads(self._read_body(max_length=1024 * 1024))
            if not isinstance(payload, dict):
                raise ValueError("configuration payload must be an object")
            result = update_runtime_config(payload)
        except (ConfigError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raw = self._json_error(str(exc))
            self._send_payload(400, raw, api=True)
            self.close_connection = True
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
            if response.status in REDIRECT_STATUSES:
                self.send_error(502, "sidecar redirect refused")
                self.close_connection = True
                return
            body = response.read(); self.send_response(response.status)
            excluded_response_headers = hop_by_hop_headers(response.headers)
            for key, value in response.headers.items():
                if key.lower() not in excluded_response_headers: self.send_header(key, value)
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
        try:
            with open(path, "rb") as stream:
                raw = stream.read()
        except OSError: self.send_error(404); return
        self._send_payload(200, raw, CONTENT_TYPES[extension], cache_control="no-cache")

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        """Keep error responses under the same browser security policy."""
        short = message or self.responses.get(code, ("",))[0]
        body = f"<html><head><title>Error {code}</title></head><body><h1>Error {code}</h1><p>{html.escape(str(short))}</p></body></html>".encode()
        path = getattr(self, "path", "")
        is_api = path.startswith("/api/")
        self._send_payload(code, body, "text/html; charset=utf-8", api=is_api, cache_control="no-store" if is_api else None)


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
    Handler.identity_mode = settings.client_identity_mode
    Handler.capture_budget_limit = settings.capture_budget
    Handler.capture_budget = CaptureBudget(settings.capture_budget)
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
    persist_fields = set(CONFIG_FIELDS)
    token_source = Handler.config_sources.get("sidecar_token", "default")
    if "sidecar_token" not in updates and token_source in {"cli", "env", "default"}:
        # Do not materialize a secret supplied only through process startup
        # overrides while persisting an unrelated UI change.
        persist_fields.discard("sidecar_token")
    settings = write_config(Handler.config_path, values, persist_fields=persist_fields)
    Handler.config_settings = settings_values(settings)
    Handler.config_sources = {
        field: (source if source in {"cli", "env"} or field not in persist_fields else "ini")
        for field, source in Handler.config_sources.items()
    }
    _apply_runtime_settings(settings)
    if "sidecar_url" in updates and not Handler.sidecar_managed:
        Handler.sidecar_url = settings.sidecar_url
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
    with closing(db(path, timeout)) as connection:
        active_runtime_session_id = None
        active_server = getattr(Handler, "gateway_server", None)
        if active_server is not None and os.fspath(getattr(Handler, "db_path", "")) == os.fspath(path):
            active_runtime_session_id = getattr(Handler, "runtime_session_id", None)
        count = purge_calls(connection, retention_days, protected_runtime_session_id=active_runtime_session_id)
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
        try:
            super().server_close()
        finally:
            if Handler.gateway_server is self:
                Handler.gateway_server = None


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
    parser.add_argument("--client-identity-mode", dest="client_identity_mode", choices=("default", "strict"), default=default)
    parser.add_argument("--capture-budget", dest="capture_budget", type=int, default=default)
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
        with closing(db(path, settings.db_timeout)) as connection:
            rows = connection.execute("SELECT c.id,c.runtime_session_id,c.client_session_row_id,cs.client_session_id,c.thread_id,c.request_correlation_id,c.created_at,c.endpoint,c.model,c.status,c.status_code,c.duration_ms,c.input_bytes,c.output_bytes FROM calls c LEFT JOIN client_sessions cs ON cs.id=c.client_session_row_id ORDER BY c.id DESC LIMIT ?", (settings.cli_call_limit,)).fetchall()
        for row in rows:
            print(f"{row['id']} runtime={row['runtime_session_id'] or '-'} client={row['client_session_id'] or '-'} thread={row['thread_id'] or '-'} request={row['request_correlation_id'] or '-'} {row['created_at']} {row['model'] or '-'} {row['status']} {row['status_code'] or '-'} {row['duration_ms'] or '-'}ms {row['input_bytes']}/{row['output_bytes']} {row['endpoint']}")
        return
    if args.cmd == "show":
        with closing(db(path, settings.db_timeout)) as connection:
            row = connection.execute("SELECT c.*,cs.client_session_id,a.request_headers_json,a.response_headers_json,p.request_body,p.response_body,p.request_truncated,p.response_truncated,p.capture_degraded FROM calls c LEFT JOIN client_sessions cs ON cs.id=c.client_session_row_id JOIN attempts a ON a.call_id=c.id JOIN payloads p ON p.attempt_id=a.id WHERE c.id=?", (args.call_id,)).fetchone()
        if not row: raise SystemExit("call not found")
        safe_request_headers = headers(json.loads(row["request_headers_json"] or "{}"))
        safe_response_headers = headers(json.loads(row["response_headers_json"] or "{}"))
        print("[HEADERS]\n" + json.dumps({"request": safe_request_headers, "response": safe_response_headers}, indent=2)); print(json.dumps({key: row[key] for key in ("id", "runtime_session_id", "client_session_id", "client_session_row_id", "thread_id", "request_correlation_id", "created_at", "completed_at", "endpoint", "model", "stream", "status", "status_code", "first_byte_at", "duration_ms", "input_bytes", "output_bytes", "error_type", "error_message")}, indent=2)); print(f"capture: request_truncated={row['request_truncated'] or 0} response_truncated={row['response_truncated'] or 0} capture_degraded={row['capture_degraded'] or 0}"); print("\n[REQUEST]\n" + (row["request_body"] or b"").decode(errors="replace")); print("\n[RESPONSE]\n" + (row["response_body"] or b"").decode(errors="replace")); return
    sidecar_env = {"PI_AI_SIDECAR_TOKEN": settings.sidecar_token} if settings.sidecar_token else None
    manager = SidecarProcess(url=settings.sidecar_url, command=settings.sidecar_command, timeout=settings.sidecar_start_timeout, stop_timeout=settings.sidecar_stop_timeout, env=sidecar_env)
    configured_sidecar = None
    try:
        try:
            if manager.configured:
                configured_sidecar = manager.start()
        except SidecarStartupError as exc:
            print("pi-ai sidecar unavailable:", exc, file=sys.stderr)
        Handler.clients = []
        Handler.db_path = path; Handler.upstream = settings.upstream; Handler.security_warnings = settings.upstream_warnings; Handler.sidecar_url = configured_sidecar; Handler.sidecar_token = settings.sidecar_token; Handler.runtime_session_id = None; Handler.session_id = None
        Handler.sidecar_managed = manager.process is not None
        Handler.db_timeout = settings.db_timeout; Handler.capture_max_body = settings.max_body; Handler.capture_budget_limit = settings.capture_budget; Handler.capture_budget = CaptureBudget(settings.capture_budget); Handler.identity_mode = settings.client_identity_mode; Handler.upstream_timeout = settings.upstream_timeout; Handler.sidecar_timeout = settings.sidecar_timeout; Handler.sse_keepalive = settings.sse_keepalive; Handler.api_call_limit = settings.api_call_limit; Handler.ui_path = settings.ui_path
        Handler.config_path = settings.config_path
        Handler.config_settings = settings_values(settings)
        Handler.config_sources = resolve_sources(args)
        host, port = validate_listen(settings.listen)
        purge(path, settings.retention_days, settings.db_timeout)
        server = None
        try:
            server = GatewayHTTPServer((host, port), Handler)
            Handler.gateway_server = server
            server.sidecar_process = manager.process
            server.sidecar_manager = manager
            with closing(db(path, settings.db_timeout)) as connection:
                cursor = connection.execute("INSERT INTO runtime_sessions(agent,started_at,last_seen_at,cwd,metadata_json) VALUES(?,?,?,?,?)", ("codex", iso(), iso(), os.getcwd(), "{}"))
                Handler.runtime_session_id = cursor.lastrowid
                Handler.session_id = Handler.runtime_session_id
                connection.execute("INSERT OR IGNORE INTO sessions(id,agent,started_at,last_seen_at,cwd,metadata_json) VALUES(?,?,?,?,?,?)", (Handler.runtime_session_id, "codex", iso(), iso(), os.getcwd(), "{}"))
                connection.commit()
            start_purge_worker(server, path, settings.retention_days, settings.purge_interval, settings.db_timeout)
            print(f"gateway listening on http://{settings.listen}, upstream {settings.upstream}", flush=True)
            for warning in settings.upstream_warnings:
                print("WARNING: " + warning, file=sys.stderr, flush=True)
            server.serve_forever()
        finally:
            Handler.gateway_server = None
            if server is not None:
                server.server_close()
    finally:
        manager.stop()


if __name__ == "__main__":
    main()
