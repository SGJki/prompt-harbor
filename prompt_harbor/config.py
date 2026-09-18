"""Runtime configuration with INI, environment, and CLI precedence."""

from __future__ import annotations

import argparse
import configparser
import os
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

DEFAULT_CONFIG = "prompt-harbor.ini"
DEFAULT_DB = "gateway.db"
DEFAULT_LISTEN = "127.0.0.1:8787"
DEFAULT_UPSTREAM = "https://api.openai.com"
DEFAULT_MAX_BODY = 10 * 1024 * 1024
DEFAULT_RETENTION_DAYS = 2
DEFAULT_DB_TIMEOUT = 30.0
DEFAULT_UPSTREAM_TIMEOUT = 600.0
DEFAULT_SIDECAR_TIMEOUT = 10.0
DEFAULT_SIDECAR_START_TIMEOUT = 5.0
DEFAULT_SIDECAR_STOP_TIMEOUT = 2.0
DEFAULT_API_CALL_LIMIT = 200
DEFAULT_CLI_CALL_LIMIT = 50
DEFAULT_SSE_KEEPALIVE = 15.0


class ConfigError(ValueError):
    """Raised when a configuration file or override is invalid."""


@dataclass(frozen=True)
class Settings:
    database: str = DEFAULT_DB
    listen: str = DEFAULT_LISTEN
    upstream: str = DEFAULT_UPSTREAM
    max_body: int = DEFAULT_MAX_BODY
    retention_days: int = DEFAULT_RETENTION_DAYS
    db_timeout: float = DEFAULT_DB_TIMEOUT
    upstream_timeout: float = DEFAULT_UPSTREAM_TIMEOUT
    sidecar_timeout: float = DEFAULT_SIDECAR_TIMEOUT
    sidecar_start_timeout: float = DEFAULT_SIDECAR_START_TIMEOUT
    sidecar_stop_timeout: float = DEFAULT_SIDECAR_STOP_TIMEOUT
    api_call_limit: int = DEFAULT_API_CALL_LIMIT
    cli_call_limit: int = DEFAULT_CLI_CALL_LIMIT
    sse_keepalive: float = DEFAULT_SSE_KEEPALIVE
    ui_path: Optional[str] = None
    sidecar_url: Optional[str] = None
    sidecar_command: Optional[str] = None
    sidecar_token: Optional[str] = None


T = TypeVar("T")


def _read_config(path: str, explicit: bool) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    if not os.path.exists(path):
        if explicit:
            raise ConfigError(f"configuration file not found: {path}")
        return parser
    try:
        with open(path, encoding="utf-8") as stream:
            parser.read_file(stream)
    except OSError as exc:
        raise ConfigError(f"cannot read configuration file {path}: {exc}") from exc
    except configparser.Error as exc:
        raise ConfigError(f"invalid configuration file {path}: {exc}") from exc
    return parser


def _raw_value(cli: argparse.Namespace, parser: configparser.ConfigParser, field: str, section: str, key: str, env_name: str, default: T) -> object:
    cli_value = getattr(cli, field, None)
    if cli_value is not None:
        return cli_value
    env_value = os.getenv(env_name)
    if env_value not in (None, ""):
        return env_value
    if parser.has_option(section, key):
        value = parser.get(section, key).strip()
        if value:
            return value
    return default


def _convert(field: str, raw: object, converter: Callable[[str], T], valid: Callable[[T], bool]) -> T:
    try:
        value = converter(str(raw))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} has invalid value: {raw!r}") from exc
    if not valid(value):
        raise ConfigError(f"{field} has invalid value: {raw!r}")
    return value


def _optional(raw: object) -> Optional[str]:
    value = str(raw).strip()
    return value or None


def load_settings(cli: argparse.Namespace) -> Settings:
    """Resolve settings using CLI > environment > INI > built-in defaults."""
    cli_config = getattr(cli, "config", None)
    env_config = os.getenv("PROMPT_HARBOR_CONFIG")
    config_path = cli_config or env_config or DEFAULT_CONFIG
    parser = _read_config(config_path, explicit=bool(cli_config or env_config))

    integer = lambda field, section, key, env, default: _convert(field, _raw_value(cli, parser, field, section, key, env, default), int, lambda value: value > 0)
    number = lambda field, section, key, env, default: _convert(field, _raw_value(cli, parser, field, section, key, env, default), float, lambda value: value > 0)
    string = lambda field, section, key, env, default: str(_raw_value(cli, parser, field, section, key, env, default)).strip()
    optional = lambda field, section, key, env: _optional(_raw_value(cli, parser, field, section, key, env, ""))

    return Settings(
        database=string("database", "gateway", "database", "PROMPT_HARBOR_DB", DEFAULT_DB),
        listen=string("listen", "gateway", "listen", "PROMPT_HARBOR_LISTEN", DEFAULT_LISTEN),
        upstream=string("upstream", "gateway", "upstream", "PROMPT_HARBOR_UPSTREAM", DEFAULT_UPSTREAM),
        max_body=integer("max_body", "gateway", "max_body", "PROMPT_HARBOR_MAX_BODY", DEFAULT_MAX_BODY),
        retention_days=integer("retention_days", "gateway", "retention_days", "PROMPT_HARBOR_RETENTION_DAYS", DEFAULT_RETENTION_DAYS),
        db_timeout=number("db_timeout", "gateway", "db_timeout", "PROMPT_HARBOR_DB_TIMEOUT", DEFAULT_DB_TIMEOUT),
        upstream_timeout=number("upstream_timeout", "gateway", "upstream_timeout", "PROMPT_HARBOR_UPSTREAM_TIMEOUT", DEFAULT_UPSTREAM_TIMEOUT),
        sidecar_timeout=number("sidecar_timeout", "gateway", "sidecar_timeout", "PROMPT_HARBOR_SIDECAR_TIMEOUT", DEFAULT_SIDECAR_TIMEOUT),
        sidecar_start_timeout=number("sidecar_start_timeout", "gateway", "sidecar_start_timeout", "PROMPT_HARBOR_PI_SIDECAR_START_TIMEOUT", DEFAULT_SIDECAR_START_TIMEOUT),
        sidecar_stop_timeout=number("sidecar_stop_timeout", "gateway", "sidecar_stop_timeout", "PROMPT_HARBOR_PI_SIDECAR_STOP_TIMEOUT", DEFAULT_SIDECAR_STOP_TIMEOUT),
        api_call_limit=integer("api_call_limit", "gateway", "api_call_limit", "PROMPT_HARBOR_API_CALL_LIMIT", DEFAULT_API_CALL_LIMIT),
        cli_call_limit=integer("cli_call_limit", "gateway", "cli_call_limit", "PROMPT_HARBOR_CLI_CALL_LIMIT", DEFAULT_CLI_CALL_LIMIT),
        sse_keepalive=number("sse_keepalive", "gateway", "sse_keepalive", "PROMPT_HARBOR_SSE_KEEPALIVE", DEFAULT_SSE_KEEPALIVE),
        ui_path=optional("ui_path", "gateway", "ui_path", "PROMPT_HARBOR_UI"),
        sidecar_url=optional("pi_sidecar_url", "sidecar", "url", "PROMPT_HARBOR_PI_SIDECAR_URL"),
        sidecar_command=optional("pi_sidecar_command", "sidecar", "command", "PROMPT_HARBOR_PI_SIDECAR_COMMAND"),
        sidecar_token=optional("pi_sidecar_token", "sidecar", "token", "PROMPT_HARBOR_MESSAGES_TOKEN"),
    )


def value(cli, env_name, default):
    """Backward-compatible scalar resolver for callers outside the gateway."""
    return cli or os.getenv(env_name) or default
