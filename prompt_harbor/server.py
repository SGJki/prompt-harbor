"""Gateway server construction and lifecycle."""
from contextlib import closing

from .proxy import Handler
from .core import GatewayHTTPServer, db, init, iso, purge, start_purge_worker
from .transport import CaptureBudget
from .models import create_runtime_session
from .config import DEFAULT_DB_TIMEOUT, DEFAULT_PURGE_INTERVAL, DEFAULT_RETENTION_DAYS, Settings, settings_values, validate_listen, validate_sidecar_url, validate_upstream

def serve(listen, upstream, database, sidecar_url=None, sidecar_token=None, retention_days=DEFAULT_RETENTION_DAYS, purge_interval=DEFAULT_PURGE_INTERVAL, db_timeout=DEFAULT_DB_TIMEOUT):
    host, port = validate_listen(listen)
    warnings = validate_upstream(upstream)
    if sidecar_url:
        validate_sidecar_url(sidecar_url)
    init(database, db_timeout)
    purge(database, retention_days, db_timeout)
    Handler.db_path = database
    Handler.upstream = upstream
    Handler.security_warnings = warnings
    Handler.sidecar_url = sidecar_url
    Handler.sidecar_token = sidecar_token
    Handler.sidecar_managed = False
    Handler.runtime_session_id = None
    Handler.session_id = None
    Handler.db_timeout = db_timeout
    Handler.clients = []
    Handler.gateway_server = None
    server = GatewayHTTPServer((host, port), Handler)
    runtime = Settings(database=database, listen=listen, upstream=upstream, retention_days=retention_days, purge_interval=purge_interval, db_timeout=db_timeout, sidecar_url=sidecar_url, sidecar_token=sidecar_token, upstream_warnings=warnings)
    Handler.config_path = runtime.config_path
    Handler.config_settings = settings_values(runtime)
    Handler.config_sources = {field: "default" for field in Handler.config_settings}
    Handler.capture_max_body = runtime.max_body
    Handler.capture_budget_limit = runtime.capture_budget
    Handler.capture_budget = CaptureBudget(runtime.capture_budget)
    Handler.identity_mode = runtime.client_identity_mode
    Handler.upstream_timeout = runtime.upstream_timeout
    Handler.sidecar_timeout = runtime.sidecar_timeout
    Handler.sse_keepalive = runtime.sse_keepalive
    Handler.api_call_limit = runtime.api_call_limit
    Handler.ui_path = runtime.ui_path
    Handler.gateway_server = server
    server.sidecar_process = None
    try:
        with closing(db(database, db_timeout)) as connection:
            # Keep startup ownership in one helper so runtime-session creation
            # stays compatible with the decomposed lifecycle seams.
            Handler.runtime_session_id = create_runtime_session(connection, agent='server', cwd=None)
            Handler.session_id = Handler.runtime_session_id
            connection.execute('INSERT OR IGNORE INTO sessions(id,agent,started_at,last_seen_at,cwd,metadata_json) VALUES(?,?,?,?,?,?)', (Handler.runtime_session_id, 'server', iso(), iso(), None, '{}'))
            connection.commit()
        start_purge_worker(server, database, retention_days, purge_interval, db_timeout)
    except Exception:
        Handler.gateway_server = None
        server.server_close()
        raise
    return server
