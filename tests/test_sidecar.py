from types import SimpleNamespace

from prompt_harbor.core import finalize_pi_stream
from prompt_harbor.pi_messages import PiMessagesState
from prompt_harbor.sidecar import SidecarProcess


def test_external_sidecar_is_never_reprobed_or_reaped(monkeypatch):
    manager = SidecarProcess(url="http://127.0.0.1:9876")
    monkeypatch.setattr(manager, "start", lambda: (_ for _ in ()).throw(AssertionError("external URL must not start")))
    assert manager.managed is False
    assert manager.reprobe() == "http://127.0.0.1:9876"
    manager.stop()


def test_managed_sidecar_reprobe_restarts_only_after_exit(monkeypatch):
    manager = SidecarProcess(command=["fixture-sidecar"])
    exited = SimpleNamespace(poll=lambda: 1)
    manager.process = exited
    started = []

    def fake_start():
        started.append(True)
        manager.url = "http://127.0.0.1:9877"
        manager.process = SimpleNamespace(poll=lambda: None)
        return manager.url

    monkeypatch.setattr(manager, "start", fake_start)
    assert manager.reprobe() == "http://127.0.0.1:9877"
    assert started == [True]
    assert manager.alive() is True
    assert manager.reprobe() == "http://127.0.0.1:9877"
    assert started == [True]


def test_sidecar_crash_outcome_survives_protocol_finalization():
    state = PiMessagesState()
    record = {"status": 200, "state": "failed", "error_type": "sidecar_crash", "error_message": "child exited", "response_complete": False}
    finalize_pi_stream(record, state, sidecar_crashed=True)
    assert record["error_type"] == "sidecar_crash"
    assert record["response_complete"] is False
