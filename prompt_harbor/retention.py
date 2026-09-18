from .database import purge_calls
from .config import DEFAULT_DB_TIMEOUT, DEFAULT_RETENTION_DAYS

def purge(path, connect, retention_days=DEFAULT_RETENTION_DAYS, timeout=DEFAULT_DB_TIMEOUT):
    try:
        c = connect(path, timeout)
    except TypeError:
        c = connect(path)
    count = purge_calls(c, retention_days)
    c.commit(); c.close()
    return count
