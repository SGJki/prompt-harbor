"""Gateway server construction and lifecycle."""
from .proxy import Handler
from .core import GatewayHTTPServer, db, init, iso, purge, start_purge_worker
from .config import DEFAULT_DB_TIMEOUT, DEFAULT_PURGE_INTERVAL, DEFAULT_RETENTION_DAYS, validate_listen

def serve(listen, upstream, database, sidecar_url=None, sidecar_token=None, retention_days=DEFAULT_RETENTION_DAYS, purge_interval=DEFAULT_PURGE_INTERVAL, db_timeout=DEFAULT_DB_TIMEOUT):
    host, port = validate_listen(listen)
    init(database, db_timeout)
    purge(database, retention_days, db_timeout)
    c = db(database)
    cur = c.execute(
        'INSERT INTO sessions(agent,started_at,last_seen_at,cwd,metadata_json) VALUES(?,?,?,?,?)',
        ('server', iso(), iso(), None, '{}'),
    )
    c.commit()
    c.close()
    Handler.db_path = database
    Handler.upstream = upstream
    Handler.sidecar_url = sidecar_url
    Handler.sidecar_token = sidecar_token
    Handler.session_id = cur.lastrowid
    Handler.db_timeout = db_timeout
    Handler.clients = []
    server = GatewayHTTPServer((host, port), Handler)
    start_purge_worker(server, database, retention_days, purge_interval, db_timeout)
    return server
