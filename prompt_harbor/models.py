"""Small data access helpers used by the gateway."""

import json
import uuid


def create_runtime_session(c, agent='codex', started_at=None, cwd=None):
    from .core import iso
    t = started_at or iso()
    cur = c.execute('INSERT INTO runtime_sessions(agent,started_at,last_seen_at,cwd,metadata_json) VALUES(?,?,?,?,?)', (agent,t,t,cwd or '', '{}'))
    return cur.lastrowid
from .database import connect

# Compatibility seam: callers that still import ``create_session`` keep the
# legacy helper while new code uses explicit runtime/client rows.


def create_session(c, agent='codex', started_at=None, cwd=None):
    """Create the mirrored legacy row for pre-API compatibility callers."""
    from .core import iso
    t = started_at or iso()
    cur = c.execute('INSERT INTO sessions(agent,started_at,last_seen_at,cwd,metadata_json) VALUES(?,?,?,?,?)', (agent, t, t, cwd or '', '{}'))
    return cur.lastrowid

def resolve_client_session(c, client_session_id, identity_status, identity_source, seen_at=None):
    from .core import iso
    seen = seen_at or iso()
    if identity_status == 'explicit':
        c.execute('INSERT OR IGNORE INTO client_sessions(client_session_id,identity_status,identity_source,first_seen_at,last_seen_at,metadata_json) VALUES(?,?,?,?,?,?)', (client_session_id, 'explicit', identity_source, seen, seen, '{}'))
        row = c.execute('SELECT id FROM client_sessions WHERE client_session_id=? AND identity_status=?', (client_session_id, 'explicit')).fetchone()
        c.execute('UPDATE client_sessions SET last_seen_at=? WHERE id=?', (seen, row[0]))
        return row[0]
    cur = c.execute(
        'INSERT INTO client_sessions(client_session_id,identity_status,identity_source,first_seen_at,last_seen_at,metadata_json) VALUES(?,?,?,?,?,?)',
        (client_session_id, identity_status, identity_source, seen, seen, '{}'),
    )
    return cur.lastrowid
