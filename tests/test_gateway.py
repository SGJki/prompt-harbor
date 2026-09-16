import http.client, json, socket, sqlite3, subprocess, sys, threading, time, pytest, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).parents[1]
class Upstream(BaseHTTPRequestHandler):
    def do_POST(self):
        n=int(self.headers.get('Content-Length','0')); self.rfile.read(n)
        self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Connection','close'); self.end_headers(); self.wfile.write(b'data: {"usage":{"input_tokens":2,"output_tokens":3,"total_tokens":5}}\n\n'); self.wfile.flush(); time.sleep(.15); self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
    def log_message(self,*a): pass

class JsonUpstream(BaseHTTPRequestHandler):
    seen=b''
    def do_POST(self):
        n=int(self.headers.get('Content-Length','0')); type(self).seen=self.rfile.read(n)
        self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('X-Up','yes'); body=b'{"ok":true}'; self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,*a): pass

class StatusUpstream(BaseHTTPRequestHandler):
    code=400
    def do_POST(self):
        self.send_response(type(self).code); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(b'{"error":"bad"}')
    def log_message(self,*a): pass

class BrokenUpstream(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Content-Length','100'); self.end_headers(); self.wfile.write(b'data: partial\\n\\n'); self.wfile.flush(); self.connection.shutdown(2); self.connection.close()
    def log_message(self,*a): pass

def free():
    s=socket.socket();s.bind(('127.0.0.1',0)); p=s.getsockname()[1];s.close();return p

def test_sse_capture_and_auth(tmp_path):
    try:
        up=ThreadingHTTPServer(('127.0.0.1',0),Upstream)
    except PermissionError as exc:
        pytest.fail(f'loopback unavailable: {exc}')
    threading.Thread(target=up.serve_forever,daemon=True).start()
    port=free(); db=tmp_path/'g.db'; proc=subprocess.Popen([sys.executable,str(ROOT/'agent_gateway.py'),'start','--database',str(db),'--listen',f'127.0.0.1:{port}','--upstream',f'http://127.0.0.1:{up.server_port}'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.time()+5
        while time.time()<deadline:
            if db.exists():
                try:
                    c=http.client.HTTPConnection('127.0.0.1',port,timeout=2); c.connect(); c.close(); break
                except OSError: pass
            time.sleep(.05)
        if proc.poll() is not None:
            pytest.fail('gateway failed to bind loopback listener')
        c=http.client.HTTPConnection('127.0.0.1',port,timeout=5); c.request('POST','/v1/responses',json.dumps({'model':'test','stream':True}),{'Authorization':'Bearer SECRET','Content-Type':'application/json'}); r=c.getresponse(); first=r.read(8); assert first.startswith(b'data: {"'); rest=r.read(); assert b'[DONE]' in first+rest; c.close()
        deadline=time.time()+3
        rows=None
        while time.time()<deadline:
            rows=sqlite3.connect(db).execute('select request_headers_json,response_body from attempts join payloads on payloads.attempt_id=attempts.id').fetchone()
            if rows and rows[1] is not None: break
            time.sleep(.05)
        proc.terminate(); proc.wait(); print('ERR', proc.stderr.read())
        assert rows and rows[1] is not None
        rows=rows; assert 'SECRET' not in rows[0]; assert 'SECRET' not in rows[1].decode(); assert b'[DONE]' in rows[1]
        assert sqlite3.connect(db).execute('select input_tokens,output_tokens,total_tokens from usage').fetchone()==(2,3,5)
    finally: proc.terminate(); proc.wait(timeout=3); up.shutdown()

def run_gateway(tmp_path, upstream, extra_env=None):
    port=free(); db=tmp_path/'g.db'; env=dict(os.environ); env.update(extra_env or {}); proc=subprocess.Popen([sys.executable,str(ROOT/'agent_gateway.py'),'start','--database',str(db),'--listen',f'127.0.0.1:{port}','--upstream',f'http://127.0.0.1:{upstream.server_port}'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
    deadline=time.time()+5
    while time.time()<deadline:
        try:
            c=http.client.HTTPConnection('127.0.0.1',port,timeout=1); c.connect(); c.close(); return proc,port,db
        except OSError: time.sleep(.05)
    proc.kill(); raise AssertionError('gateway did not start')

def test_json_transparent_forward(tmp_path):
    up=ThreadingHTTPServer(('127.0.0.1',0),JsonUpstream); threading.Thread(target=up.serve_forever,daemon=True).start(); proc,port,db=run_gateway(tmp_path,up)
    try:
        body=b'{"model":"m","input":"hello"}'; c=http.client.HTTPConnection('127.0.0.1',port); c.request('POST','/v1/responses',body,{'Content-Type':'application/json','Authorization':'Bearer TEST_SECRET_123'}); r=c.getresponse(); assert r.status==200 and r.read()==b'{"ok":true}'; c.close(); assert JsonUpstream.seen==body
    finally: proc.terminate(); proc.wait(); up.shutdown()

def test_storage_limit_sets_truncated_flags(tmp_path):
    up=ThreadingHTTPServer(('127.0.0.1',0),JsonUpstream); threading.Thread(target=up.serve_forever,daemon=True).start(); proc,port,db=run_gateway(tmp_path,up,{'AGENT_GATEWAY_MAX_BODY':'4'})
    try:
        c=http.client.HTTPConnection('127.0.0.1',port); c.request('POST','/v1/responses',b'123456789'); r=c.getresponse(); r.read(); c.close(); time.sleep(.2)
        row=sqlite3.connect(db).execute('select request_truncated,response_truncated,length(request_body),length(response_body) from payloads').fetchone(); assert row==(1,1,4,4)
    finally: proc.terminate(); proc.wait(); up.shutdown()

def test_multiple_requests_share_startup_session(tmp_path):
    up=ThreadingHTTPServer(('127.0.0.1',0),JsonUpstream); threading.Thread(target=up.serve_forever,daemon=True).start(); proc,port,db=run_gateway(tmp_path,up)
    try:
        for _ in range(2):
            c=http.client.HTTPConnection('127.0.0.1',port); c.request('POST','/v1/responses',b'{"model":"m"}'); r=c.getresponse(); assert r.status==200; r.read(); c.close()
        deadline=time.time()+2
        while time.time()<deadline and sqlite3.connect(db).execute('select count(*) from calls').fetchone()[0] < 2: time.sleep(.05)
        c=sqlite3.connect(db); assert c.execute('select count(*) from sessions').fetchone()==(1,); assert c.execute('select count(distinct session_id) from calls').fetchone()==(1,)
    finally: proc.terminate(); proc.wait(); up.shutdown()

import pytest
@pytest.mark.parametrize('code',[400,401,429,500])
def test_upstream_error_status_preserved(tmp_path, code):
    StatusUpstream.code=code
    up=ThreadingHTTPServer(('127.0.0.1',0),StatusUpstream); threading.Thread(target=up.serve_forever,daemon=True).start(); proc,port,db=run_gateway(tmp_path,up)
    try:
        c=http.client.HTTPConnection('127.0.0.1',port); c.request('POST','/v1/responses',b'{}'); r=c.getresponse(); assert r.status==code and b'bad' in r.read(); c.close()
    finally: proc.terminate(); proc.wait(); up.shutdown()

def test_upstream_connection_failure_returns_502(tmp_path):
    port=free(); db=tmp_path/'g.db'; proc=subprocess.Popen([sys.executable,str(ROOT/'agent_gateway.py'),'start','--database',str(db),'--listen',f'127.0.0.1:{port}','--upstream','http://127.0.0.1:1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.time()+5
        while time.time()<deadline:
            try:
                c=http.client.HTTPConnection('127.0.0.1',port,timeout=1); c.request('POST','/v1/responses',b'{}'); r=c.getresponse(); assert r.status==502; r.read(); c.close(); break
            except (ConnectionRefusedError,OSError): time.sleep(.05)
        else: raise AssertionError('gateway did not respond')
    finally: proc.terminate(); proc.wait()

def test_upstream_disconnect_records_partial_failure(tmp_path):
    up=ThreadingHTTPServer(('127.0.0.1',0),BrokenUpstream); threading.Thread(target=up.serve_forever,daemon=True).start(); proc,port,db=run_gateway(tmp_path,up)
    try:
        c=http.client.HTTPConnection('127.0.0.1',port,timeout=3); c.request('POST','/v1/responses',b'{}'); r=c.getresponse(); data=r.read(); c.close(); assert b'partial' in data
        deadline=time.time()+3
        row=None
        while time.time()<deadline:
            row=sqlite3.connect(db).execute('select status,error_type,response_body from attempts join payloads on payloads.attempt_id=attempts.id').fetchone()
            if row and row[0] != 'running': break
            time.sleep(.05)
        assert row and row[0]=='failed' and row[2] and b'partial' in row[2]
    finally: proc.terminate(); proc.wait(); up.shutdown()

def test_client_disconnect_does_not_crash_gateway(tmp_path):
    up=ThreadingHTTPServer(('127.0.0.1',0),Upstream); threading.Thread(target=up.serve_forever,daemon=True).start(); proc,port,db=run_gateway(tmp_path,up)
    try:
        s=socket.create_connection(('127.0.0.1',port)); s.sendall(b'POST /v1/responses HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\nContent-Type: application/json\r\n\r\n{}'); s.shutdown(socket.SHUT_RDWR); s.close(); time.sleep(.4); assert proc.poll() is None
    finally: proc.terminate(); proc.wait(); up.shutdown()
