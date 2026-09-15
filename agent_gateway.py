#!/usr/bin/env python3
import argparse, json, os, sqlite3, threading, time, uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

DB = os.environ.get('AGENT_GATEWAY_DB', 'gateway.db')
UPSTREAM = os.environ.get('AGENT_GATEWAY_UPSTREAM', 'https://api.openai.com')

def now(): return datetime.now(timezone.utc).isoformat()
def conn():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def init_db():
    c=conn(); c.executescript('''
    CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, started_at TEXT, last_seen_at TEXT, cwd TEXT, project_name TEXT, metadata_json TEXT);
    CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, created_at TEXT, completed_at TEXT, provider TEXT, api_family TEXT, endpoint TEXT, model TEXT, stream INTEGER, status TEXT, status_code INTEGER, first_byte_at TEXT, duration_ms INTEGER, input_bytes INTEGER DEFAULT 0, output_bytes INTEGER DEFAULT 0, error_type TEXT, error_message TEXT);
    CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY AUTOINCREMENT, call_id INTEGER, attempt_no INTEGER, started_at TEXT, completed_at TEXT, upstream_url TEXT, status TEXT, status_code INTEGER, request_headers_json TEXT, response_headers_json TEXT, first_byte_at TEXT, duration_ms INTEGER, input_bytes INTEGER DEFAULT 0, output_bytes INTEGER DEFAULT 0, error_type TEXT, error_message TEXT);
    CREATE TABLE IF NOT EXISTS payloads(id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id INTEGER UNIQUE, request_body BLOB, response_body BLOB, request_content_type TEXT, response_content_type TEXT, response_complete INTEGER DEFAULT 0, response_truncated INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id INTEGER UNIQUE, input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER, raw_usage_json TEXT);
    '''); c.commit(); c.close()
def safe_headers(h): return {k:v for k,v in h.items() if k.lower() not in ('authorization','proxy-authorization','cookie','set-cookie','host','content-length')}
def purge():
    c=conn(); c.execute("DELETE FROM calls WHERE created_at < datetime('now','-2 days')"); c.commit(); c.close()

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def do_POST(self):
        length=int(self.headers.get('Content-Length','0')); body=self.rfile.read(length)
        started=time.time(); c=conn(); created=now(); model=None; stream=False
        try:
            obj=json.loads(body); model=obj.get('model'); stream=bool(obj.get('stream'))
        except Exception: pass
        c.execute('INSERT INTO calls(created_at,provider,api_family,endpoint,model,stream,status,input_bytes) VALUES(?,?,?,?,?,?,?,?)',(created,'openai','responses',self.path,model,int(stream),'running',len(body)))
        call_id=c.lastrowid; c.execute('INSERT INTO attempts(call_id,attempt_no,started_at,upstream_url,status,request_headers_json,input_bytes) VALUES(?,?,?,?,?,?,?)',(call_id,1,created,UPSTREAM+self.path,'running',json.dumps(safe_headers(self.headers)),len(body))); attempt_id=c.lastrowid
        c.execute('INSERT INTO payloads(attempt_id,request_body,request_content_type,created_at,updated_at) VALUES(?,?,?,?,?)',(attempt_id,body,self.headers.get('Content-Type'),created,created)); c.commit(); c.close()
        req=Request(UPSTREAM+self.path,data=body,method='POST',headers={k:v for k,v in self.headers.items() if k.lower()!='host'})
        out=bytearray(); first=None; status=0; rh={}
        try:
            try: resp=urlopen(req,timeout=600)
            except HTTPError as e: resp=e
            status=resp.status; rh=safe_headers(resp.headers); self.send_response(status)
            for k,v in resp.headers.items():
                if k.lower() not in ('transfer-encoding','connection','content-length'): self.send_header(k,v)
            self.end_headers()
            while True:
                chunk=resp.read(8192)
                if not chunk: break
                if first is None: first=time.time();
                out.extend(chunk); self.wfile.write(chunk); self.wfile.flush()
            complete=True; state='succeeded'
        except Exception as e:
            complete=False; state='failed'; self.close_connection=True
            try: self.send_error(502,str(e))
            except Exception: pass
        finished=time.time(); c=conn(); c.execute('UPDATE attempts SET completed_at=?,status=?,status_code=?,response_headers_json=?,first_byte_at=?,duration_ms=?,output_bytes=?,error_message=? WHERE id=?',(now(),state,status,json.dumps(rh),datetime.fromtimestamp(first,timezone.utc).isoformat() if first else None,int((finished-started)*1000),len(out),None if complete else 'upstream failure',attempt_id)); c.execute('UPDATE calls SET completed_at=?,status=?,status_code=?,first_byte_at=?,duration_ms=?,output_bytes=? WHERE id=?',(now(),state,status,datetime.fromtimestamp(first,timezone.utc).isoformat() if first else None,int((finished-started)*1000),len(out),call_id)); c.execute('UPDATE payloads SET response_body=?,response_content_type=?,response_complete=?,updated_at=? WHERE attempt_id=?',(bytes(out),rh.get('content-type'),int(complete),now(),attempt_id)); c.commit(); c.close()
    def log_message(self,*a): pass

def main():
    global DB,UPSTREAM
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd',required=True)
    s=sub.add_parser('start'); s.add_argument('--listen',default='127.0.0.1:8787'); s.add_argument('--upstream',default=UPSTREAM)
    sub.add_parser('list'); x=sub.add_parser('show'); x.add_argument('id',type=int); sub.add_parser('purge'); sub.add_parser('init')
    a=p.parse_args(); init_db()
    if a.cmd=='init': print(f'initialized SQLite database: {DB}')
    elif a.cmd=='start':
        UPSTREAM=a.upstream; host,port=a.listen.rsplit(':',1); print(f'gateway listening on http://{a.listen}, upstream {UPSTREAM}'); ThreadingHTTPServer((host,int(port)),Handler).serve_forever()
    elif a.cmd=='purge': purge(); print('purged records older than 2 days')
    elif a.cmd=='list':
        for r in conn().execute('SELECT id,created_at,endpoint,model,status,status_code,duration_ms FROM calls ORDER BY id DESC LIMIT 50'): print(f"{r['id']} {r['created_at']} {r['model'] or '-'} {r['status']} {r['status_code'] or '-'} {r['duration_ms'] or '-'}ms {r['endpoint']}")
    else:
        c=conn(); r=c.execute('SELECT * FROM calls WHERE id=?',(a.id,)).fetchone(); q=c.execute('SELECT * FROM payloads WHERE attempt_id=(SELECT id FROM attempts WHERE call_id=? LIMIT 1)',(a.id,)).fetchone(); print(json.dumps(dict(r),indent=2)); print('\n[REQUEST]\n'+(q['request_body'].decode(errors='replace') if q else '')); print('\n[RESPONSE]\n'+(q['response_body'].decode(errors='replace') if q and q['response_body'] else ''))
if __name__=='__main__': main()
