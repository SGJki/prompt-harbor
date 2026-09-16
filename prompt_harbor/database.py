import sqlite3

def connect(path):
    c = sqlite3.connect(path, timeout=30)
    c.row_factory = sqlite3.Row
    return c

def purge_calls(c, cutoff_sql="julianday('now','-2 days')"):
    ids = [r[0] for r in c.execute(f"SELECT id FROM calls WHERE julianday(created_at) < {cutoff_sql}")]
    for call_id in ids:
        aids = [r[0] for r in c.execute('SELECT id FROM attempts WHERE call_id=?', (call_id,))]
        for attempt_id in aids:
            c.execute('DELETE FROM usage WHERE attempt_id=?', (attempt_id,))
            c.execute('DELETE FROM payloads WHERE attempt_id=?', (attempt_id,))
        c.execute('DELETE FROM attempts WHERE call_id=?', (call_id,))
        c.execute('DELETE FROM calls WHERE id=?', (call_id,))
    return len(ids)

