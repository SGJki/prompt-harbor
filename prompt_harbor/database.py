import sqlite3
from .config import DEFAULT_DB_TIMEOUT, DEFAULT_RETENTION_DAYS

def connect(path, timeout=DEFAULT_DB_TIMEOUT):
    c = sqlite3.connect(path, timeout=timeout)
    c.row_factory = sqlite3.Row
    return c

def purge_calls(c, retention_days=DEFAULT_RETENTION_DAYS, cutoff_sql=None):
    if retention_days <= 0:
        raise ValueError("retention_days must be positive")
    if cutoff_sql is not None:
        ids = [r[0] for r in c.execute(f"SELECT id FROM calls WHERE julianday(created_at) < {cutoff_sql}")]
    else:
        ids = [r[0] for r in c.execute("SELECT id FROM calls WHERE julianday(created_at) < julianday('now', ?)", (f"-{retention_days} days",))]
    for call_id in ids:
        aids = [r[0] for r in c.execute('SELECT id FROM attempts WHERE call_id=?', (call_id,))]
        for attempt_id in aids:
            c.execute('DELETE FROM usage WHERE attempt_id=?', (attempt_id,))
            c.execute('DELETE FROM payloads WHERE attempt_id=?', (attempt_id,))
        c.execute('DELETE FROM attempts WHERE call_id=?', (call_id,))
        c.execute('DELETE FROM calls WHERE id=?', (call_id,))
    return len(ids)
