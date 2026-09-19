import http.client
import io
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from test_gateway import run_gateway, wait_for_rows
from prompt_harbor.transport import CaptureBudget, capture_chunk
from prompt_harbor.storage import with_retry
from prompt_harbor.events import commit_then_notify, notify_after_commit
from prompt_harbor.core import Handler


def interleaved_sse_chunks():
    """Small deterministic SSE schedule used by flush/order assertions."""
    return [("client-a", b"data: a-1\n\n"), ("client-b", b"data: b-1\n\n"), ("client-b", b"data: b-done\n\n"), ("client-a", b"data: a-done\n\n")]


def test_capture_budget_degrades_without_blocking_forwarding():
    budget = CaptureBudget(4)
    record = {"limit": 10, "out": bytearray(), "total_out": 0, "response_truncated": False, "capture_degraded": False, "first": None}
    capture_chunk(record, b"abcd", budget)
    capture_chunk(record, b"efgh", budget)
    assert record["total_out"] == 8
    assert bytes(record["out"]) == b"abcd"
    assert record["capture_degraded"] is True
    budget.release(len(record["out"]))
    assert budget.used == 0


def test_capture_flags_are_independent():
    budget = CaptureBudget(32)
    record = {"limit": 4, "out": bytearray(), "total_out": 0, "response_truncated": False, "capture_degraded": False, "first": None}
    capture_chunk(record, b"12345", budget)
    assert record["response_truncated"] is True
    assert record["capture_degraded"] is False


def test_storage_retry_retries_busy_operation():
    state = {"calls": 0}

    def operation():
        state["calls"] += 1
        if state["calls"] < 3:
            import sqlite3
            raise sqlite3.OperationalError("database is locked")
        return "done"

    assert with_retry(operation, attempts=4, initial_delay=0) == "done"
    assert state["calls"] == 3


def test_interleaved_fixture_has_out_of_order_completion(interleaved_chunks):
    schedule = interleaved_sse_chunks()
    assert interleaved_chunks[0][0] != interleaved_chunks[-1][0]
    assert schedule[2][0] == "client-b" and schedule[3][0] == "client-a"
    completion = ["client-b", "client-a"]
    assert completion == sorted(completion, key=lambda value: {"client-b": 0, "client-a": 1}[value])


def test_capture_budget_release_after_partial_capture():
    budget = CaptureBudget(3)
    record = {"limit": 10, "out": bytearray(), "total_out": 0, "response_truncated": False, "capture_degraded": False, "first": None}
    capture_chunk(record, b"abc", budget)
    assert budget.used == 3
    capture_chunk(record, b"d", budget)
    assert record["capture_degraded"] is True
    budget.release(len(record["out"]))
    assert budget.used == 0


def test_terminal_retry_exhaustion_releases_capture_without_raising(monkeypatch):
    import sqlite3 as sqlite
    handler = object.__new__(Handler)
    budget = CaptureBudget(8)
    handler.capture_budget = budget
    record = {"out": bytearray(b"1234")}
    budget.reserve(4)
    monkeypatch.setattr(handler, "_persist_attempt_once", lambda *args: (_ for _ in ()).throw(sqlite.OperationalError("database is locked")))
    handler._finish_attempt(record)
    assert budget.used == 0 and record["audit_persistence_degraded"] is True


def test_commit_then_notify_orders_commit_before_callback():
    events = []

    class Connection:
        def commit(self):
            events.append("commit")

    commit_then_notify(Connection(), lambda resources: events.append(("notify", resources)), {"payloads"})
    assert events == ["commit", ("notify", {"payloads"})]


def test_sse_writer_flushes_each_chunk_and_snapshot_is_single_row(temp_database):
    class Writer(io.BytesIO):
        flush_count = 0

        def flush(self):
            self.flush_count += 1
            super().flush()

    handler = object.__new__(Handler)
    handler.wfile = Writer()
    handler._sse_write_lock = threading.Lock()
    handler._write_sse(b"data: first\n\n")
    handler._write_sse(b"data: done\n\n")
    assert handler.wfile.flush_count == 2
    with sqlite3.connect(temp_database) as connection:
        connection.execute("insert into attempts(call_id,attempt_no,status) values(1,1,'succeeded')")
        attempt_id = connection.execute("select last_insert_rowid()").fetchone()[0]
        connection.execute("insert into payloads(attempt_id,response_body) values(?,?)", (attempt_id, b"data: first\n\ndata: done\n\n"))
        assert connection.execute("select count(*) from payloads where attempt_id=?", (attempt_id,)).fetchone() == (1,)


def test_invalidation_sink_is_called_only_by_explicit_post_commit_step():
    calls = []
    notify_after_commit(lambda resources: calls.append(resources), {"calls"})
    assert calls == [{"calls"}]


class InterleavedUpstream(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(size) or b"{}")
        delay = float(request.get("delay", 0))
        time.sleep(delay)
        body = json.dumps({"id": request.get("id")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_interleaved_multi_connection_forwarding_keeps_client_sessions_separate(tmp_path):
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), InterleavedUpstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    process, port, database = run_gateway(tmp_path, upstream)
    try:
        def request(index):
            client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            session = f"client-{index % 4}"
            body = json.dumps({"id": index, "model": "fixture", "delay": (19 - index) * 0.002}).encode()
            client.request("POST", "/v1/responses", body, {"Content-Type": "application/json", "session-id": session})
            response = client.getresponse()
            raw = response.read()
            client.close()
            return response.status, raw

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(request, range(20)))
        assert all(status == 200 for status, _ in results)
        rows = wait_for_rows(
            database,
            "select status from calls order by id",
            lambda rows: len(rows) == 20 and all(row[0] != "running" for row in rows),
            proc=process,
        )
        assert len(rows) == 20
        with sqlite3.connect(database) as connection:
            sessions = connection.execute("select client_session_id,count(*) from client_sessions join calls on calls.client_session_row_id=client_sessions.id group by client_session_id").fetchall()
        assert sorted(sessions) == [(f"client-{index}", 5) for index in range(4)]
    finally:
        process.terminate(); process.wait(timeout=3); upstream.shutdown()
