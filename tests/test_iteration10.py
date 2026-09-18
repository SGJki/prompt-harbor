import http.client
import json

from prompt_harbor.config import Settings, settings_values, write_config

from test_ui_static import get, start_gateway


def request(port, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    payload = response.read()
    result = response.status, dict(response.getheaders()), payload
    connection.close()
    return result


def test_host_allowlist_and_security_headers(tmp_path):
    proc, port, _db, upstream = start_gateway(tmp_path)
    try:
        status, headers, _ = get(port, "/api/overview")
        assert status == 200
        assert headers["Content-Security-Policy"] == "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert headers["Cache-Control"] == "no-store"
        status, _, _ = request(port, "GET", "/", headers={"Host": "evil.com"})
        assert status == 403
        status, _, _ = request(port, "GET", "/api/overview", headers={"Host": "evil.com"})
        assert status == 403
        status, _, _ = request(port, "PUT", "/api/config", body=b"{}", headers={"Host": "evil.com"})
        assert status == 403
    finally:
        proc.terminate()
        proc.wait()
        upstream.shutdown()


def test_put_requires_csrf_header(tmp_path):
    proc, port, _db, upstream = start_gateway(tmp_path)
    try:
        status, _, _ = request(port, "PUT", "/api/config", body=b'{"api_call_limit": 12}', headers={"Content-Type": "application/json"})
        assert status == 403
    finally:
        proc.terminate()
        proc.wait()
        upstream.shutdown()


def test_ini_write_preserves_comments_and_unknown_keys(tmp_path):
    path = tmp_path / "prompt-harbor.ini"
    path.write_text("; header\n[gateway]\n# keep this\nunknown = yes\nmax_body = 4\n", encoding="utf-8")
    values = settings_values(Settings())
    values["max_body"] = 8
    write_config(str(path), values)
    text = path.read_text(encoding="utf-8")
    assert "; header" in text and "# keep this" in text and "unknown = yes" in text
    assert "max_body = 8" in text
    assert path.stat().st_mode & 0o077 == 0


def test_config_source_is_reported_and_env_override_is_locked(tmp_path):
    proc, port, _db, upstream = start_gateway(tmp_path, {"PROMPT_HARBOR_API_CALL_LIMIT": "17"})
    try:
        status, _, body = get(port, "/api/config")
        data = json.loads(body)
        assert status == 200
        assert data["fields"]["api_call_limit"]["source"] == "env"
        assert data["fields"]["api_call_limit"]["value"] == 17
        status, _, _ = request(
            port,
            "PUT",
            "/api/config",
            body=b'{"api_call_limit": 18}',
            headers={"Content-Type": "application/json", "X-Prompt-Harbor-Request": "1"},
        )
        assert status == 400
    finally:
        proc.terminate()
        proc.wait()
        upstream.shutdown()
