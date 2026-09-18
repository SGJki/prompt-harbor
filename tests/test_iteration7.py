import configparser
import json

from prompt_harbor.config import Settings, settings_values, write_config
from prompt_harbor.core import Handler, update_runtime_config


def test_config_writer_covers_ini_fields_and_uses_private_atomic_file(tmp_path):
    path = tmp_path / "prompt-harbor.ini"
    values = settings_values(Settings())
    values.update({"max_body": 1234, "sidecar_token": "local-secret"})
    settings = write_config(str(path), values)
    parser = configparser.ConfigParser()
    parser.read(path)
    assert settings.max_body == 1234
    assert parser.getint("gateway", "max_body") == 1234
    assert parser.get("sidecar", "token") == "local-secret"
    assert path.stat().st_mode & 0o077 == 0


def test_runtime_config_update_redacts_secret_and_reports_restart_fields(tmp_path):
    previous = (
        Handler.config_path, Handler.config_settings, Handler.gateway_server, Handler.upstream,
        Handler.capture_max_body, Handler.db_timeout, Handler.upstream_timeout,
        Handler.sidecar_timeout, Handler.sse_keepalive, Handler.api_call_limit, Handler.ui_path,
    )
    try:
        path = tmp_path / "prompt-harbor.ini"
        Handler.config_path = str(path)
        Handler.config_settings = settings_values(Settings())
        Handler.gateway_server = None
        result = update_runtime_config({"gateway": {"max_body": "2048", "listen": "127.0.0.1:9898"}, "sidecar": {"token": "secret"}})
        assert result["restart_required"] == ["listen", "sidecar_token"]
        assert result["fields"]["sidecar_token"]["value"] is None
        assert result["fields"]["sidecar_token"]["configured"] is True
        assert "local-secret" not in json.dumps(result)
        assert Handler.capture_max_body == 2048
    finally:
        (
            Handler.config_path, Handler.config_settings, Handler.gateway_server, Handler.upstream,
            Handler.capture_max_body, Handler.db_timeout, Handler.upstream_timeout,
            Handler.sidecar_timeout, Handler.sse_keepalive, Handler.api_call_limit, Handler.ui_path,
        ) = previous
