"""Gateway server construction and lifecycle."""
from http.server import ThreadingHTTPServer
from .proxy import Handler

def serve(listen, upstream, database):
    host, port = listen.rsplit(':', 1)
    Handler.db_path = database
    Handler.upstream = upstream
    return ThreadingHTTPServer((host, int(port)), Handler)

