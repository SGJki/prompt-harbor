from .config import DEFAULT_DB_TIMEOUT, DEFAULT_RETENTION_DAYS

def connect(path, timeout=DEFAULT_DB_TIMEOUT):
    from .core import db
    return db(path, timeout)

def purge_calls(c, retention_days=DEFAULT_RETENTION_DAYS):
    """Canonical SQL cascade used by compatibility callers with an open DB."""
    if retention_days <= 0:
        raise ValueError("retention_days must be positive")
    ids = [r[0] for r in c.execute("SELECT id FROM calls WHERE julianday(created_at) < julianday('now', ?)", (f"-{retention_days} days",))]
    for call_id in ids:
        aids = [r[0] for r in c.execute('SELECT id FROM attempts WHERE call_id=?', (call_id,))]
        for attempt_id in aids:
            c.execute('DELETE FROM usage WHERE attempt_id=?', (attempt_id,))
            c.execute('DELETE FROM payloads WHERE attempt_id=?', (attempt_id,))
        c.execute('DELETE FROM attempts WHERE call_id=?', (call_id,))
        c.execute('DELETE FROM calls WHERE id=?', (call_id,))
    c.execute("DELETE FROM client_sessions WHERE id NOT IN (SELECT DISTINCT client_session_row_id FROM calls WHERE client_session_row_id IS NOT NULL)")
    c.execute("DELETE FROM runtime_sessions WHERE id NOT IN (SELECT DISTINCT runtime_session_id FROM calls WHERE runtime_session_id IS NOT NULL)")
    return len(ids)
