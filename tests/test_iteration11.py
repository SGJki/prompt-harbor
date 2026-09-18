from prompt_harbor.config import Settings, settings_values
from prompt_harbor.core import Handler, update_runtime_config


def _snapshot():
    return {
        name: getattr(Handler, name)
        for name in (
            "config_path", "config_settings", "config_sources", "gateway_server", "sidecar_managed",
            "sidecar_url", "sidecar_token", "capture_max_body", "upstream", "security_warnings",
            "db_timeout", "upstream_timeout", "sidecar_timeout", "sse_keepalive", "api_call_limit", "ui_path",
        )
    }


def _restore(snapshot):
    for name, value in snapshot.items():
        setattr(Handler, name, value)


def test_managed_sidecar_runtime_url_survives_unrelated_config_save(tmp_path):
    previous = _snapshot()
    try:
        Handler.config_path = str(tmp_path / "prompt-harbor.ini")
        Handler.config_settings = settings_values(Settings(sidecar_command="node sidecar/server.mjs"))
        Handler.config_sources = {field: "default" for field in Handler.config_settings}
        Handler.gateway_server = None
        Handler.sidecar_managed = True
        Handler.sidecar_url = "http://127.0.0.1:9876"
        update_runtime_config({"max_body": "2048", "sidecar_url": ""})
        assert Handler.sidecar_url == "http://127.0.0.1:9876"
    finally:
        _restore(previous)


def test_startup_token_is_not_persisted_by_unrelated_config_save(tmp_path):
    previous = _snapshot()
    try:
        path = tmp_path / "prompt-harbor.ini"
        Handler.config_path = str(path)
        Handler.config_settings = settings_values(Settings(sidecar_token="environment-secret"))
        Handler.config_sources = {field: "default" for field in Handler.config_settings}
        Handler.config_sources["sidecar_token"] = "env"
        Handler.gateway_server = None
        Handler.sidecar_managed = False
        update_runtime_config({"max_body": "2048"})
        assert "environment-secret" not in path.read_text(encoding="utf-8")
    finally:
        _restore(previous)
