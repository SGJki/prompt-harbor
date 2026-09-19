from .config import DEFAULT_DB_TIMEOUT, DEFAULT_RETENTION_DAYS

def purge(path, connect, retention_days=DEFAULT_RETENTION_DAYS, timeout=DEFAULT_DB_TIMEOUT):
    from .database import purge_calls
    try:
        connection = connect(path, timeout)
    except TypeError:
        connection = connect(path)
    try:
        count = purge_calls(connection, retention_days)
        connection.commit()
        return count
    finally:
        connection.close()
