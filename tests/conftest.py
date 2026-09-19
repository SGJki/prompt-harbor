import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))


@pytest.fixture
def interleaved_chunks():
    """Deterministic chunk schedule for concurrent stream tests."""
    return [("client-a", b"a-1"), ("client-b", b"b-1"), ("client-a", b"a-2"), ("client-b", b"b-2")]


@pytest.fixture
def temp_database(tmp_path):
    """Fresh private database for model, retention, and lifecycle tests."""
    from prompt_harbor.core import init
    path = tmp_path / "fixture.db"
    init(str(path))
    return path


@pytest.fixture
def loopback_upstream():
    """Factory for short-lived loopback HTTP fixtures; callers own the handler."""
    servers = []

    def start(handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def concurrent_requests():
    """Run independent request callables concurrently and return completion order."""
    def run(callables):
        with ThreadPoolExecutor(max_workers=len(callables)) as pool:
            futures = [pool.submit(callable_) for callable_ in callables]
            return [future.result() for future in futures]
    return run
