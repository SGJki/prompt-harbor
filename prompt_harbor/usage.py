import json

def extract(data):
    try:
        obj = json.loads(data); usage = obj.get('usage')
        if usage: return usage
    except Exception: pass
    for line in data.splitlines():
        if line.startswith(b'data:'):
            try:
                item = line[5:].strip()
                if item and item != b'[DONE]':
                    usage = json.loads(item).get('usage')
                    if usage: return usage
            except Exception: pass
    return None

