from .database import purge_calls

def purge(path, connect):
    c = connect(path)
    count = purge_calls(c)
    c.commit(); c.close()
    return count

