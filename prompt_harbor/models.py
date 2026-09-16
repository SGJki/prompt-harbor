"""Small data access helpers used by the gateway."""
from .database import connect

def create_session(c, agent='codex', started_at=None, cwd=None):
    from .core import iso
    t = started_at or iso()
    cur = c.execute('INSERT INTO sessions(agent,started_at,last_seen_at,cwd,metadata_json) VALUES(?,?,?,?,?)', (agent,t,t,cwd or '', '{}'))
    return cur.lastrowid

