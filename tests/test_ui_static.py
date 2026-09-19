import http.client, json, os, socket, sqlite3, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parents[1]

class JsonUpstream(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('Content-Length', '0')); self.rfile.read(n)
        body = b'{"ok":true}'
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

def free():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p

def start_gateway(tmp_path, extra_env=None):
    up = ThreadingHTTPServer(('127.0.0.1', 0), JsonUpstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    port = free(); db = tmp_path / 'ui.db'
    env = dict(os.environ); env.update(extra_env or {})
    proc = subprocess.Popen([sys.executable, str(ROOT / 'prompt_harbor.py'), 'start', '--database', str(db), '--listen', f'127.0.0.1:{port}', '--upstream', f'http://127.0.0.1:{up.server_port}'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            c = http.client.HTTPConnection('127.0.0.1', port, timeout=1); c.connect(); c.close(); break
        except OSError: time.sleep(.05)
    else:
        proc.kill(); up.shutdown(); raise AssertionError('gateway did not start')
    return proc, port, db, up

def get(port, path):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    c.request('GET', path); r = c.getresponse(); body = r.read(); headers = dict(r.getheaders()); c.close()
    return r.status, headers, body

def post_call(port, body=b'{"model":"m"}'):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    c.request('POST', '/v1/responses', body, {'Content-Type': 'application/json'}); r = c.getresponse(); r.read(); c.close()

def test_static_assets_served_with_content_types(tmp_path):
    proc, port, db, up = start_gateway(tmp_path)
    try:
        s, h, body = get(port, '/')
        assert s == 200 and b'text/html' in h['Content-Type'].encode() and b'prompt harbor' in body
        s, h, body = get(port, '/css/app.css')
        assert s == 200 and b'text/css' in h['Content-Type'].encode() and b'.side' in body
        s, h, body = get(port, '/js/app.js')
        assert s == 200 and b'javascript' in h['Content-Type'].encode() and b'startRealtime' in body
        s, h, body = get(port, '/js/api.js')
        assert s == 200 and b'ApiError' in body
    finally:
        proc.terminate(); proc.wait(); up.shutdown()

def test_static_serving_blocks_traversal_and_unknown_types(tmp_path):
    proc, port, db, up = start_gateway(tmp_path)
    try:
        for path in ('/js/../../pyproject.toml', '/%2e%2e/pyproject.toml', '/foo/../js/app.js', '/js/%2e%2e/js/app.js', '/.git/config', '/js/api.py', '/prompt_harbor/core.py', '/css/nope.css'):
            s, _, _ = get(port, path)
            assert s == 404, path
    finally:
        proc.terminate(); proc.wait(); up.shutdown()

def test_detail_api_reports_truncation_flags(tmp_path):
    proc, port, db, up = start_gateway(tmp_path, {'PROMPT_HARBOR_MAX_BODY': '4'})
    try:
        post_call(port, b'{"model":"m","input":"payload"}')
        s, _, body = get(port, '/api/calls/1')
        d = json.loads(body)
        assert s == 200 and d['request_truncated'] == 1 and d['response_truncated'] == 1
        assert d['request_body'] == '{"mo'
    finally:
        proc.terminate(); proc.wait(); up.shutdown()

def test_runtime_sessions_api_includes_call_count(tmp_path):
    proc, port, db, up = start_gateway(tmp_path)
    try:
        post_call(port); post_call(port)
        deadline = time.time() + 3
        while time.time() < deadline and sqlite3.connect(db).execute('select count(*) from calls').fetchone()[0] < 2: time.sleep(.05)
        s, _, body = get(port, '/api/runtime-sessions')
        sessions = json.loads(body)['runtime_sessions']
        assert s == 200 and len(sessions) == 1 and sessions[0]['call_count'] == 2
        s, _, _ = get(port, '/api/sessions')
        assert s == 404
    finally:
        proc.terminate(); proc.wait(); up.shutdown()
