from types import SimpleNamespace

import pytest

import prompt_harbor.core as core
from prompt_harbor.config import ConfigError, validate_upstream


def test_invalid_upstream_ports_are_rejected():
    with pytest.raises(ConfigError, match="invalid host or port"):
        validate_upstream("https://example.test:abc/v1")
    with pytest.raises(ConfigError, match="invalid host or port"):
        validate_upstream("https://example.test:65536/v1")


def test_default_origin_includes_port_in_warning_decision():
    assert validate_upstream("https://api.openai.com:443/v1") == ()
    warnings = validate_upstream("https://api.openai.com:8443/v1")
    assert warnings and "Authorization headers" in warnings[0]


def test_init_fails_if_database_cannot_be_private(tmp_path, monkeypatch):
    path = tmp_path / "gateway.db"

    def fail_chmod(_path, _mode):
        raise OSError("permission denied")

    monkeypatch.setattr(core.os, "chmod", fail_chmod)
    with pytest.raises(ConfigError, match="cannot secure database file"):
        core.init(str(path))


def test_redirect_response_is_not_sent_to_client():
    handler = object.__new__(core.Handler)
    sent = []
    handler.send_error = lambda status, message: sent.append((status, message))
    handler.close_connection = False
    record = {"status": 302, "state": "failed", "error_type": None, "error_message": None, "response_complete": False}
    response = SimpleNamespace(status=302)

    assert handler._reject_redirect(response, record) is True
    assert sent == [(502, "upstream redirect refused")]
    assert record == {
        "status": 502,
        "state": "failed",
        "error_type": "upstream_redirect",
        "error_message": "upstream redirect refused",
        "response_complete": True,
    }
    assert handler.close_connection is True

