"""Gateway server construction and lifecycle."""
from http.server import ThreadingHTTPServer
from .proxy import Handler
from .core import db, init, iso

def serve(listen, upstream, database):
    host, port = listen.rsplit(':', 1)
    init(database)
    c = db(database)
    cur = c.execute(
        'INSERT INTO sessions(agent,started_at,last_seen_at,cwd,metadata_json) VALUES(?,?,?,?,?)',
        ('server', iso(), iso(), None, '{}'),
    )
    c.commit()
    c.close()
    Handler.db_path = database
    Handler.upstream = upstream
    Handler.session_id = cur.lastrowid
    return ThreadingHTTPServer((host, int(port)), Handler)
