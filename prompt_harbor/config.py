"""Runtime configuration with INI, environment, and CLI precedence."""

from __future__ import annotations

import argparse
import configparser
import ipaddress
import os
import tempfile
import re
from urllib.parse import urlsplit
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
DEFAULT_PURGE_INTERVAL = 24 * 60 * 60.0


class ConfigError(ValueError):
    """Raised when a configuration file or override is invalid."""


def validate_upstream(value: str) -> tuple[str, ...]:
    """Validate the upstream origin and return user-facing security warnings.

    Plain HTTP is intentionally limited to loopback hosts so local test
    fixtures remain possible without allowing credentials onto the network.
    """
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ConfigError(f"upstream has invalid URL: {value!r}") from exc
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ConfigError("upstream must be an absolute https URL")
    if parsed.username or parsed.password:
        raise ConfigError("upstream must not contain embedded credentials")
    if parsed.query:
        raise ConfigError("upstream must not contain query parameters")
    if parsed.fragment:
        raise ConfigError("upstream must not contain a URL fragment")
    try:
        hostname = parsed.hostname.lower().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("upstream has invalid host or port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ConfigError("upstream has invalid port")
    loopback = hostname == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            loopback = False
    if parsed.scheme == "http" and not loopback:
        raise ConfigError("upstream must use https; http is allowed only for loopback test fixtures")
    warnings = []
    if parsed.scheme == "http":
        warnings.append("upstream uses unencrypted HTTP; this is allowed only because it targets loopback")
    normalized_origin = (parsed.scheme.lower(), hostname, port or (443 if parsed.scheme == "https" else 80))
    if parsed.scheme != "http" and normalized_origin != ("https", "api.openai.com", 443):
        warnings.append(f"custom upstream {parsed.scheme}://{parsed.netloc} will receive Authorization headers")
    return tuple(warnings)


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
    purge_interval: float = DEFAULT_PURGE_INTERVAL
    ui_path: Optional[str] = None
    sidecar_url: Optional[str] = None
    sidecar_command: Optional[str] = None
    sidecar_token: Optional[str] = None
    upstream_warnings: tuple[str, ...] = ()
    config_path: str = DEFAULT_CONFIG


# The UI and the INI writer use this single source of truth.  Values marked
# restart_required remain editable, but their new value is applied on the next
# process start because changing them in-place would break resource ownership.
CONFIG_FIELDS = {
    "database": {"section": "gateway", "key": "database", "kind": "string", "restart_required": True},
    "listen": {"section": "gateway", "key": "listen", "kind": "string", "restart_required": True},
    "upstream": {"section": "gateway", "key": "upstream", "kind": "string", "restart_required": False},
    "max_body": {"section": "gateway", "key": "max_body", "kind": "integer", "restart_required": False},
    "retention_days": {"section": "gateway", "key": "retention_days", "kind": "integer", "restart_required": False},
    "db_timeout": {"section": "gateway", "key": "db_timeout", "kind": "number", "restart_required": False},
    "upstream_timeout": {"section": "gateway", "key": "upstream_timeout", "kind": "number", "restart_required": False},
    "sidecar_timeout": {"section": "gateway", "key": "sidecar_timeout", "kind": "number", "restart_required": False},
    "sidecar_start_timeout": {"section": "gateway", "key": "sidecar_start_timeout", "kind": "number", "restart_required": True},
    "sidecar_stop_timeout": {"section": "gateway", "key": "sidecar_stop_timeout", "kind": "number", "restart_required": True},
    "api_call_limit": {"section": "gateway", "key": "api_call_limit", "kind": "integer", "restart_required": False},
    "cli_call_limit": {"section": "gateway", "key": "cli_call_limit", "kind": "integer", "restart_required": True},
    "sse_keepalive": {"section": "gateway", "key": "sse_keepalive", "kind": "number", "restart_required": False},
    "purge_interval": {"section": "gateway", "key": "purge_interval", "kind": "number", "restart_required": False},
    "ui_path": {"section": "gateway", "key": "ui_path", "kind": "optional", "restart_required": False},
    "sidecar_url": {"section": "sidecar", "key": "url", "kind": "optional", "restart_required": False},
    "sidecar_command": {"section": "sidecar", "key": "command", "kind": "optional", "restart_required": True},
    "sidecar_token": {"section": "sidecar", "key": "token", "kind": "optional", "restart_required": True, "secret": True},
}

CLI_FIELD_NAMES = {"sidecar_url": "pi_sidecar_url", "sidecar_command": "pi_sidecar_command", "sidecar_token": "pi_sidecar_token"}


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

    listen = string("listen", "gateway", "listen", "PROMPT_HARBOR_LISTEN", DEFAULT_LISTEN)
    validate_listen(listen)
    upstream = string("upstream", "gateway", "upstream", "PROMPT_HARBOR_UPSTREAM", DEFAULT_UPSTREAM)
    upstream_warnings = validate_upstream(upstream)
    return Settings(
        database=string("database", "gateway", "database", "PROMPT_HARBOR_DB", DEFAULT_DB),
        listen=listen,
        upstream=upstream,
        upstream_warnings=upstream_warnings,
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
        purge_interval=number("purge_interval", "gateway", "purge_interval", "PROMPT_HARBOR_PURGE_INTERVAL", DEFAULT_PURGE_INTERVAL),
        ui_path=optional("ui_path", "gateway", "ui_path", "PROMPT_HARBOR_UI"),
        sidecar_url=optional("pi_sidecar_url", "sidecar", "url", "PROMPT_HARBOR_PI_SIDECAR_URL"),
        sidecar_command=optional("pi_sidecar_command", "sidecar", "command", "PROMPT_HARBOR_PI_SIDECAR_COMMAND"),
        sidecar_token=optional("pi_sidecar_token", "sidecar", "token", "PROMPT_HARBOR_MESSAGES_TOKEN"),
        config_path=config_path,
    )


def resolve_sources(cli: argparse.Namespace) -> dict[str, str]:
    """Return the precedence source used for every editable setting."""
    cli_config = getattr(cli, "config", None)
    env_config = os.getenv("PROMPT_HARBOR_CONFIG")
    config_path = cli_config or env_config or DEFAULT_CONFIG
    parser = _read_config(config_path, explicit=bool(cli_config or env_config))
    sources = {}
    env_names = {
        "database": "PROMPT_HARBOR_DB", "listen": "PROMPT_HARBOR_LISTEN", "upstream": "PROMPT_HARBOR_UPSTREAM",
        "max_body": "PROMPT_HARBOR_MAX_BODY", "retention_days": "PROMPT_HARBOR_RETENTION_DAYS",
        "db_timeout": "PROMPT_HARBOR_DB_TIMEOUT", "upstream_timeout": "PROMPT_HARBOR_UPSTREAM_TIMEOUT",
        "sidecar_timeout": "PROMPT_HARBOR_SIDECAR_TIMEOUT", "sidecar_start_timeout": "PROMPT_HARBOR_PI_SIDECAR_START_TIMEOUT",
        "sidecar_stop_timeout": "PROMPT_HARBOR_PI_SIDECAR_STOP_TIMEOUT", "api_call_limit": "PROMPT_HARBOR_API_CALL_LIMIT",
        "cli_call_limit": "PROMPT_HARBOR_CLI_CALL_LIMIT", "sse_keepalive": "PROMPT_HARBOR_SSE_KEEPALIVE",
        "purge_interval": "PROMPT_HARBOR_PURGE_INTERVAL", "ui_path": "PROMPT_HARBOR_UI",
        "sidecar_url": "PROMPT_HARBOR_PI_SIDECAR_URL", "sidecar_command": "PROMPT_HARBOR_PI_SIDECAR_COMMAND",
        "sidecar_token": "PROMPT_HARBOR_MESSAGES_TOKEN",
    }
    for field, spec in CONFIG_FIELDS.items():
        cli_name = CLI_FIELD_NAMES.get(field, field)
        if getattr(cli, cli_name, None) is not None:
            sources[field] = "cli"
        elif os.getenv(env_names[field]) not in (None, ""):
            sources[field] = "env"
        elif parser.has_option(spec["section"], spec["key"]) and parser.get(spec["section"], spec["key"]).strip():
            sources[field] = "ini"
        else:
            sources[field] = "default"
    return sources


def settings_values(settings: Settings) -> dict[str, object]:
    """Return the complete editable value set for runtime/config updates."""
    return {field: getattr(settings, field) for field in CONFIG_FIELDS}


def validate_values(values: dict[str, object]) -> Settings:
    """Validate a complete value set using the same parser as startup."""
    cli = argparse.Namespace(config=None)
    for field in CONFIG_FIELDS:
        value = values.get(field)
        if value is None and CONFIG_FIELDS[field]["kind"] == "optional":
            value = ""
        setattr(cli, CLI_FIELD_NAMES.get(field, field), value)
    return load_settings(cli)


def write_config(path: str, values: dict[str, object], *, persist_fields: Optional[set[str]] = None) -> Settings:
    """Validate and atomically persist editable settings to an INI file."""
    settings = validate_values(values)
    try:
        with open(path, encoding="utf-8", newline="") as stream:
            original = stream.read()
    except FileNotFoundError:
        original = ""
    except OSError as exc:
        raise ConfigError(f"cannot read configuration file {path}: {exc}") from exc
    text = _update_ini_text(original, settings, persist_fields)
    directory = os.path.dirname(os.path.abspath(path)) or "."
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".prompt-harbor-", suffix=".ini", dir=directory, text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    except OSError as exc:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        raise ConfigError(f"cannot write configuration file {path}: {exc}") from exc
    return settings


_SECTION_RE = re.compile(r"^\s*\[([^]]+)\]\s*(?:\r?\n|$)")
_KEY_RE = re.compile(r"^(\s*)([^=:#\s]+)(\s*[=:]\s*)(.*?)(\r?\n|$)$")


def _update_ini_text(original: str, settings: Settings, persist_fields: Optional[set[str]] = None) -> str:
    """Edit managed keys in-place while retaining comments and unknown lines."""
    lines = original.splitlines(keepends=True)
    if not lines:
        lines = []
    locations = {}
    current = None
    for index, line in enumerate(lines):
        section = _SECTION_RE.match(line)
        if section:
            current = section.group(1).strip().lower()
            continue
        key = _KEY_RE.match(line)
        if key and current:
            locations[(current, key.group(2).strip().lower())] = index
    additions: dict[str, list[str]] = {}
    for field, spec in CONFIG_FIELDS.items():
        if persist_fields is not None and field not in persist_fields:
            continue
        section = spec["section"].lower()
        key = spec["key"]
        value = getattr(settings, field)
        location = locations.get((section, key.lower()))
        if location is not None:
            if value is None:
                lines[location] = ""
            else:
                match = _KEY_RE.match(lines[location])
                newline = match.group(5) if match else "\n"
                lines[location] = f"{match.group(1) if match else ''}{key}{match.group(3) if match else ' = '}{value}{newline}"
        elif value is not None:
            additions.setdefault(section, []).append(f"{key} = {value}\n")
    for section, entries in additions.items():
        section_index = next((i for i, line in enumerate(lines) if (_SECTION_RE.match(line) and _SECTION_RE.match(line).group(1).strip().lower() == section)), None)
        if section_index is None:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += "\n"
            if lines and lines[-1].strip():
                lines.append("\n")
            lines.append(f"[{section}]\n")
            lines.extend(entries)
            continue
        end = len(lines)
        for i in range(section_index + 1, len(lines)):
            if _SECTION_RE.match(lines[i]):
                end = i
                break
        insert_at = end
        while insert_at > section_index + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines[insert_at:insert_at] = entries
    return "".join(lines)


def validate_listen(value: str) -> tuple[str, int]:
    """Validate the deliberately narrow local-only listener contract."""
    try:
        host, raw_port = value.rsplit(":", 1)
        port = int(raw_port)
    except (AttributeError, ValueError):
        raise ConfigError("listen must be 127.0.0.1:PORT") from None
    if host != "127.0.0.1" or not 0 <= port <= 65535:
        raise ConfigError("listen must use host 127.0.0.1 and port 0-65535")
    return host, port


def value(cli, env_name, default):
    """Backward-compatible scalar resolver for callers outside the gateway."""
    return cli or os.getenv(env_name) or default
