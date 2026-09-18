import http.client, json, os, sqlite3, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from test_gateway import JsonUpstream, free, run_gateway, wait_for, wait_for_rows


class BoundaryRequestUpstream(BaseHTTPRequestHandler):
    seen = []
    def do_POST(self):
        n = int(self.headers.get('Content-Length', '0')); type(self).seen.append(self.rfile.read(n))
        body = b'{"ok":true}'; self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass


class LimitResponseUpstream(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('Content-Length', '0')); request = self.rfile.read(n)
        size = int(request.decode() or '0')
        body = b'x' * size
        self.send_response(200); self.send_header('Content-Type', 'application/octet-stream'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass


class LegacyUsageUpstream(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('Content-Length', '0')); self.rfile.read(n)
        body = b'{"usage":{"prompt_tokens":7,"completion_tokens":8,"total_tokens":15}}'
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass


def test_api_id_and_unknown_paths_have_explicit_errors(tmp_path):
    up = ThreadingHTTPServer(('127.0.0.1', 0), JsonUpstream); threading.Thread(target=up.serve_forever, daemon=True).start(); proc, port, db = run_gateway(tmp_path, up)
    try:
        for path, status in (('/api/calls/abc', 400), ('/api/does-not-exist', 404)):
            c = http.client.HTTPConnection('127.0.0.1', port, timeout=3); c.request('GET', path); r = c.getresponse(); assert r.status == status; r.read(); c.close()
    finally:
        proc.terminate(); proc.wait(timeout=3); up.shutdown()


def test_request_limit_boundaries_preserve_online_body_and_store_prefix(tmp_path):
    BoundaryRequestUpstream.seen=[]; up = ThreadingHTTPServer(('127.0.0.1', 0), BoundaryRequestUpstream); threading.Thread(target=up.serve_forever, daemon=True).start(); proc, port, db = run_gateway(tmp_path, up, {'PROMPT_HARBOR_MAX_BODY': '4'})
    try:
        sent = [b'a' * n for n in (3, 4, 5)]
        for body in sent:
            c = http.client.HTTPConnection('127.0.0.1', port); c.request('POST', '/v1/responses', body); r = c.getresponse(); assert r.status == 200; r.read(); c.close()
        rows = wait_for_rows(db, 'select c.status,a.status,p.request_body,p.request_truncated from calls c join attempts a on a.call_id=c.id join payloads p on p.attempt_id=a.id order by p.id', lambda rows: len(rows)==3 and all(row[0]=='succeeded' and row[1]=='succeeded' for row in rows), proc=proc)
        assert [(len(row[2]), row[3]) for row in rows] == [(3, 0), (4, 0), (4, 1)]
        assert BoundaryRequestUpstream.seen == sent
    finally:
        proc.terminate(); proc.wait(timeout=3); up.shutdown()


def test_response_limit_boundaries_preserve_online_bytes_and_store_prefix(tmp_path):
    up = ThreadingHTTPServer(('127.0.0.1', 0), LimitResponseUpstream); threading.Thread(target=up.serve_forever, daemon=True).start(); proc, port, db = run_gateway(tmp_path, up, {'PROMPT_HARBOR_MAX_BODY': '4'})
    try:
        online = []
        for n in (3, 4, 5):
            body = str(n).encode(); c = http.client.HTTPConnection('127.0.0.1', port); c.request('POST', '/v1/responses', body); r = c.getresponse(); assert r.status == 200; online.append(r.read()); c.close()
        rows = wait_for_rows(db, 'select c.status,a.status,p.response_body,p.response_truncated from calls c join attempts a on a.call_id=c.id join payloads p on p.attempt_id=a.id order by p.id', lambda rows: len(rows)==3 and all(row[0]=='succeeded' and row[1]=='succeeded' for row in rows), proc=proc)
        assert online == [b'x' * n for n in (3, 4, 5)]
        assert [(len(row[2]), row[3]) for row in rows] == [(3, 0), (4, 0), (4, 1)]
    finally:
        proc.terminate(); proc.wait(timeout=3); up.shutdown()


def test_legacy_usage_names_are_returned_by_detail_api(tmp_path):
    up = ThreadingHTTPServer(('127.0.0.1', 0), LegacyUsageUpstream); threading.Thread(target=up.serve_forever, daemon=True).start(); proc, port, db = run_gateway(tmp_path, up)
    try:
        c = http.client.HTTPConnection('127.0.0.1', port); c.request('POST', '/v1/responses', b'{"model":"usage"}'); r = c.getresponse(); assert r.status == 200; r.read(); c.close()
        cid = wait_for(db, 'select id from calls', lambda x: x is not None)[0]
        c = http.client.HTTPConnection('127.0.0.1', port); c.request('GET', f'/api/calls/{cid}'); r = c.getresponse(); detail = json.loads(r.read()); c.close()
        assert (detail['input_tokens'], detail['output_tokens'], detail['total_tokens']) == (7, 8, 15)
    finally:
        proc.terminate(); proc.wait(timeout=3); up.shutdown()


def test_invalid_max_body_is_rejected_at_startup(tmp_path):
    database = tmp_path / 'g.db'
    environment = dict(os.environ, PROMPT_HARBOR_MAX_BODY='not-an-integer')
    proc = subprocess.Popen(
        [sys.executable, 'prompt_harbor.py', 'start', '--database', str(database), '--listen', '127.0.0.1:0'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    stdout, stderr = proc.communicate(timeout=3)
    assert proc.returncode != 0
    assert 'max_body' in stderr
