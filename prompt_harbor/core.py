#!/usr/bin/env python3
import argparse,json,os,sqlite3,sys,time
from .config import DEFAULT_DB, DEFAULT_LISTEN, DEFAULT_UPSTREAM, DEFAULT_MAX_BODY
from .headers import sanitize
from .usage import extract as extract_usage_mod
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request,urlopen
DEFAULT_DB=DEFAULT_DB; DEFAULT_LISTEN=DEFAULT_LISTEN; DEFAULT_UPSTREAM=DEFAULT_UPSTREAM
def iso(ts=None): return datetime.fromtimestamp(ts,timezone.utc).isoformat() if ts else datetime.now(timezone.utc).isoformat()
def db(path):
 c=sqlite3.connect(path,timeout=30); c.row_factory=sqlite3.Row; return c
def schema(c):
 c.executescript('''CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY AUTOINCREMENT,agent TEXT,started_at TEXT,last_seen_at TEXT,cwd TEXT,project_name TEXT,metadata_json TEXT);CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY AUTOINCREMENT,session_id INTEGER,created_at TEXT,completed_at TEXT,provider TEXT,api_family TEXT,endpoint TEXT,model TEXT,stream INTEGER,status TEXT,status_code INTEGER,first_byte_at TEXT,duration_ms INTEGER,input_bytes INTEGER DEFAULT 0,output_bytes INTEGER DEFAULT 0,error_type TEXT,error_message TEXT);CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,call_id INTEGER,attempt_no INTEGER,started_at TEXT,completed_at TEXT,upstream_url TEXT,status TEXT,status_code INTEGER,request_headers_json TEXT,response_headers_json TEXT,first_byte_at TEXT,duration_ms INTEGER,input_bytes INTEGER DEFAULT 0,output_bytes INTEGER DEFAULT 0,error_type TEXT,error_message TEXT);CREATE TABLE IF NOT EXISTS payloads(id INTEGER PRIMARY KEY AUTOINCREMENT,attempt_id INTEGER UNIQUE,request_body BLOB,response_body BLOB,request_content_type TEXT,response_content_type TEXT,response_complete INTEGER DEFAULT 0,request_truncated INTEGER DEFAULT 0,response_truncated INTEGER DEFAULT 0,created_at TEXT,updated_at TEXT);CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY AUTOINCREMENT,attempt_id INTEGER UNIQUE,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,raw_usage_json TEXT);CREATE INDEX IF NOT EXISTS calls_created_idx ON calls(created_at);''')
def init(path): c=db(path);schema(c);c.commit();c.close()
def headers(h): return sanitize(h)
def extract_usage(data):
 try:
  obj=json.loads(data); u=obj.get('usage');
  if u:return u
 except Exception: pass
 for line in data.splitlines():
  if line.startswith(b'data:'):
   try:
    v=line[5:].strip()
    if v and v!=b'[DONE]':
     u=json.loads(v).get('usage')
     if u:return u
   except Exception: pass
class Handler(BaseHTTPRequestHandler):
 clients=[]
 protocol_version='HTTP/1.1'; db_path=DEFAULT_DB; upstream=DEFAULT_UPSTREAM; session_id=None
 def do_POST(self):
  started=time.time(); n=int(self.headers.get('Content-Length','0') or 0); body=self.rfile.read(n); model=stream=None
  try: o=json.loads(body);model=o.get('model');stream=bool(o.get('stream'))
  except Exception: pass
  limit=int(os.getenv('PROMPT_HARBOR_MAX_BODY',str(DEFAULT_MAX_BODY))); request_truncated=len(body)>limit; stored_request=body[:limit]
  c=db(self.db_path); created=iso(started); c.execute('UPDATE sessions SET last_seen_at=? WHERE id=?',(created,self.session_id)); cur=c.execute('INSERT INTO calls(session_id,created_at,provider,api_family,endpoint,model,stream,status,input_bytes) VALUES(?,?,?,?,?,?,?,?,?)',(self.session_id,created,'openai','openai',self.path,model,int(bool(stream)),'running',len(body))); call=cur.lastrowid; cur=c.execute('INSERT INTO attempts(call_id,attempt_no,started_at,upstream_url,status,request_headers_json,input_bytes) VALUES(?,?,?,?,?,?,?)',(call,1,created,self.upstream.rstrip('/')+self.path,'running',json.dumps(headers(self.headers)),len(body))); aid=cur.lastrowid; c.execute('INSERT INTO payloads(attempt_id,request_body,request_content_type,request_truncated,created_at,updated_at) VALUES(?,?,?,?,?,?)',(aid,stored_request,self.headers.get('Content-Type'),int(request_truncated),created,created));c.commit();c.close()
  out=bytearray(); total_out=0; response_truncated=False; first=None; status=None; rh={}; state='failed'; err=None; etype=None; transport_complete=False
  try:
   try:r=urlopen(Request(self.upstream.rstrip('/')+self.path,data=body,method=self.command,headers={k:v for k,v in self.headers.items() if k.lower()!='host'}),timeout=600)
   except HTTPError as e:r=e
   status=r.status;rh=headers(r.headers); expected_length=r.headers.get('Content-Length'); self.send_response(status)
   for k,v in r.headers.items():
    if k.lower() not in {'transfer-encoding','connection','content-length'}:self.send_header(k,v)
   self.end_headers()
   while True:
    chunk=r.read(8192)
    if not chunk:break
    if first is None:first=time.time()
    total_out += len(chunk)
    if len(out) < limit: out.extend(chunk[:max(0,limit-len(out))])
    if total_out > limit: response_truncated=True
    self.wfile.write(chunk);self.wfile.flush()
   self.close_connection=True
   incomplete = expected_length is not None and total_out != int(expected_length)
   transport_complete = not incomplete
   state='succeeded' if status<400 and not incomplete else 'failed'; etype='upstream_incomplete' if incomplete else ('upstream_http' if status>=400 else None);err='incomplete upstream response' if incomplete else ('upstream HTTP error' if status>=400 else None)
  except (BrokenPipeError,ConnectionResetError): etype='client_cancel';err='client disconnected'
  except Exception as e:
   etype='upstream_error';err=str(e)
   try:self.send_error(502,'upstream unavailable')
   except Exception:pass
  done=time.time();c=db(self.db_path); vals=(iso(done),state,status,json.dumps(rh),iso(first) if first else None,int((done-started)*1000),total_out,etype,err,aid);c.execute('UPDATE attempts SET completed_at=?,status=?,status_code=?,response_headers_json=?,first_byte_at=?,duration_ms=?,output_bytes=?,error_type=?,error_message=? WHERE id=?',vals);c.execute('UPDATE calls SET completed_at=?,status=?,status_code=?,first_byte_at=?,duration_ms=?,output_bytes=?,error_type=?,error_message=? WHERE id=?',(vals[0],vals[1],vals[2],vals[4],vals[5],vals[6],vals[7],vals[8],vals[9]));c.execute('UPDATE payloads SET response_body=?,response_content_type=?,response_complete=?,response_truncated=?,updated_at=? WHERE attempt_id=?',(bytes(out),rh.get('content-type'),int(transport_complete),int(response_truncated),iso(done),aid));u=extract_usage(bytes(out))
  if u:c.execute('INSERT OR REPLACE INTO usage(attempt_id,input_tokens,output_tokens,total_tokens,raw_usage_json) VALUES(?,?,?,?,?)',(aid,u.get('input_tokens',u.get('prompt_tokens')),u.get('output_tokens',u.get('completion_tokens')),u.get('total_tokens'),json.dumps(u)))
  c.commit();c.close()
  for client in list(self.clients):
   try: client.wfile.write(b'event: invalidate\ndata: {"resource":"calls"}\n\n'); client.wfile.flush()
   except Exception:
    try:self.clients.remove(client)
    except ValueError:pass
 def do_GET(self):
  if self.path.startswith('/api/calls/'):
   try: cid=int(self.path.rsplit('/',1)[1])
   except: self.send_error(400); return
   c=db(self.db_path); r=c.execute('SELECT c.*,a.request_headers_json,a.response_headers_json,p.request_body,p.response_body,u.input_tokens,u.output_tokens,u.total_tokens FROM calls c LEFT JOIN attempts a ON a.call_id=c.id LEFT JOIN payloads p ON p.attempt_id=a.id LEFT JOIN usage u ON u.attempt_id=a.id WHERE c.id=?',(cid,)).fetchone(); c.close()
   if not r:self.send_error(404); return
   d=dict(r); d['request_headers_json']=json.loads(d['request_headers_json'] or '{}'); d['response_headers_json']=json.loads(d['response_headers_json'] or '{}'); d.pop('request_body',None); d.pop('response_body',None)
   raw=json.dumps(d).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
  if self.path=='/api/events':
   self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Cache-Control','no-cache'); self.send_header('Connection','keep-alive'); self.end_headers(); self.wfile.write(b'event: ready\ndata: {}\n\n'); self.wfile.flush(); self.clients.append(self)
   try:
    while True: time.sleep(15); self.wfile.write(b': keepalive\n\n'); self.wfile.flush()
   except Exception:
    if self in self.clients:self.clients.remove(self)
   return
  if self.path not in ('/api/overview','/api/calls','/api/sessions'):
   self.send_error(404); return
  c=db(self.db_path)
  calls=[dict(r) for r in c.execute('SELECT id,session_id,created_at,completed_at,endpoint,model,status,status_code,duration_ms,input_bytes,output_bytes,error_type FROM calls ORDER BY id DESC LIMIT 200')]
  sessions=[dict(r) for r in c.execute('SELECT id,agent,started_at,last_seen_at,cwd,project_name FROM sessions ORDER BY id DESC')]
  c.close(); payload={'calls':calls,'sessions':sessions}
  if self.path=='/api/calls': payload={'calls':calls}
  if self.path=='/api/sessions': payload={'sessions':sessions}
  raw=json.dumps(payload).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
 def log_message(self,*a):pass
def purge(path):
 c=db(path);ids=[r[0] for r in c.execute("SELECT id FROM calls WHERE julianday(created_at) < julianday('now','-2 days')")]
 for x in ids:
  aids=[r[0] for r in c.execute('SELECT id FROM attempts WHERE call_id=?',(x,))]
  for a in aids:c.execute('DELETE FROM usage WHERE attempt_id=?',(a,));c.execute('DELETE FROM payloads WHERE attempt_id=?',(a,))
  c.execute('DELETE FROM attempts WHERE call_id=?',(x,));c.execute('DELETE FROM calls WHERE id=?',(x,))
 c.commit();c.close();return len(ids)
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--database');p.add_argument('--listen');p.add_argument('--upstream');s=p.add_subparsers(dest='cmd',required=True)
 for n in ['init','start','list','purge']:
  q=s.add_parser(n);q.add_argument('--database',default=argparse.SUPPRESS);q.add_argument('--listen',default=argparse.SUPPRESS);q.add_argument('--upstream',default=argparse.SUPPRESS)
 sh=s.add_parser('show');sh.add_argument('call_id',type=int);sh.add_argument('--database',default=argparse.SUPPRESS);sh.add_argument('--listen',default=argparse.SUPPRESS);sh.add_argument('--upstream',default=argparse.SUPPRESS);a=p.parse_args(argv);path=a.database or os.getenv('PROMPT_HARBOR_DB') or DEFAULT_DB;listen=a.listen or os.getenv('PROMPT_HARBOR_LISTEN') or DEFAULT_LISTEN;up=a.upstream or os.getenv('PROMPT_HARBOR_UPSTREAM') or DEFAULT_UPSTREAM;init(path)
 if a.cmd=='init':print('initialized SQLite database:',path)
 elif a.cmd=='purge':print('purged',purge(path),'calls older than 2 days')
 elif a.cmd=='list':
  for r in db(path).execute('SELECT id,created_at,endpoint,model,status,status_code,duration_ms,input_bytes,output_bytes FROM calls ORDER BY id DESC LIMIT 50'):print(f"{r['id']} {r['created_at']} {r['model'] or '-'} {r['status']} {r['status_code'] or '-'} {r['duration_ms'] or '-'}ms {r['input_bytes']}/{r['output_bytes']} {r['endpoint']}")
 elif a.cmd=='show':
  r=db(path).execute('SELECT c.*,a.request_headers_json,a.response_headers_json,p.request_body,p.response_body,p.request_truncated,p.response_truncated FROM calls c JOIN attempts a ON a.call_id=c.id JOIN payloads p ON p.attempt_id=a.id WHERE c.id=?',(a.call_id,)).fetchone()
  if not r:raise SystemExit('call not found')
  print('[HEADERS]\n'+json.dumps({'request':json.loads(r['request_headers_json'] or '{}'),'response':json.loads(r['response_headers_json'] or '{}')},indent=2));print(json.dumps({k:r[k] for k in ('id','session_id','created_at','completed_at','endpoint','model','stream','status','status_code','first_byte_at','duration_ms','input_bytes','output_bytes','error_type','error_message')},indent=2));print(f"truncated: request={r['request_truncated'] or 0} response={r['response_truncated'] or 0}");print('\n[REQUEST]\n'+(r['request_body'] or b'').decode(errors='replace'));print('\n[RESPONSE]\n'+(r['response_body'] or b'').decode(errors='replace'))
 else:
  c=db(path);cur=c.execute('INSERT INTO sessions(agent,started_at,last_seen_at,cwd,metadata_json) VALUES(?,?,?,?,?)',('codex',iso(),iso(),os.getcwd(),'{}'));sid=cur.lastrowid;c.commit();c.close();Handler.db_path=path;Handler.upstream=up;Handler.session_id=sid;host,port=listen.rsplit(':',1);print(f'gateway listening on http://{listen}, upstream {up}',flush=True);purge(path);ThreadingHTTPServer((host,int(port)),Handler).serve_forever()
if __name__=='__main__':main()
