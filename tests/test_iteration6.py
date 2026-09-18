import os
import stat

import pytest
from urllib.request import Request

from prompt_harbor.config import ConfigError, validate_upstream
import prompt_harbor.core as core
from prompt_harbor.core import NoRedirectHandler, init


def test_public_http_upstream_is_rejected():
    with pytest.raises(ConfigError, match="must use https"):
        validate_upstream("http://llm.example.test")


def test_loopback_http_fixture_is_allowed_with_warning():
    warnings = validate_upstream("http://127.0.0.1:8799")
    assert warnings and "unencrypted HTTP" in warnings[0]
    assert validate_upstream("http://localhost:8799")


def test_upstream_rejects_embedded_credentials_and_query():
    with pytest.raises(ConfigError, match="embedded credentials"):
        validate_upstream("https://key:secret@example.test")
    with pytest.raises(ConfigError, match="query parameters"):
        validate_upstream("https://example.test?api_key=secret")


def test_custom_https_upstream_warns_about_authorization_forwarding():
    warnings = validate_upstream("https://gateway.example.test/v1")
    assert warnings == ("custom upstream https://gateway.example.test will receive Authorization headers",)


def test_redirect_handler_does_not_create_follow_up_request():
    assert NoRedirectHandler().redirect_request(None, None, 302, "Found", {}, "https://evil.example.test") is None


def test_open_url_drops_authorization_from_request_after_connect(monkeypatch):
    class FakeOpener:
        def open(self, request, timeout):
            return object()

    request = Request("https://api.openai.com/v1/responses", headers={"Authorization": "Bearer secret"})
    monkeypatch.setattr(core, "UPSTREAM_OPENER", FakeOpener())
    core.open_url(request, timeout=1)
    assert all(key.lower() != "authorization" for key in request.headers)


def test_database_permissions_are_private(tmp_path):
    path = tmp_path / "gateway.db"
    init(str(path))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
