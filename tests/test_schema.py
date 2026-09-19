import argparse, json, os, sqlite3, subprocess, sys, socket, threading, time, http.client
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import prompt_harbor as g

def db_with_schema(tmp_path):
    p=tmp_path/'x.db'; g.init(str(p)); return p

def test_init_creates_all_tables(tmp_path):
    p=db_with_schema(tmp_path); names={r[0] for r in sqlite3.connect(p).execute("select name from sqlite_master where type='table'")}; assert {'sessions','runtime_sessions','client_sessions','calls','attempts','payloads','usage'}<=names
def test_primary_keys_autoincrement(tmp_path):
    p=db_with_schema(tmp_path); c=sqlite3.connect(p)
    for t in ('sessions','calls','attempts','payloads','usage'):
        sql=c.execute('select sql from sqlite_master where name=?',(t,)).fetchone()[0].upper(); assert 'ID INTEGER PRIMARY KEY AUTOINCREMENT' in sql
def test_no_foreign_keys(tmp_path):
    p=db_with_schema(tmp_path); assert not any('FOREIGN KEY' in (r[0] or '').upper() for r in sqlite3.connect(p).execute("select sql from sqlite_master where sql is not null"))
def test_required_index(tmp_path):
    p=db_with_schema(tmp_path); assert sqlite3.connect(p).execute("select 1 from sqlite_master where type='index' and name='calls_created_idx'").fetchone()


def test_explicit_session_indexes_and_capture_columns(tmp_path):
    p=db_with_schema(tmp_path)
    with sqlite3.connect(p) as connection:
        indexes = {row[1] for row in connection.execute("select type,name from sqlite_master where type='index'")}
        call_columns = {row[1] for row in connection.execute("pragma table_info(calls)")}
        payload_columns = {row[1] for row in connection.execute("pragma table_info(payloads)")}
    assert {"calls_runtime_session_idx", "calls_client_session_idx", "calls_thread_idx", "calls_request_correlation_idx"} <= indexes
    assert {"runtime_session_id", "client_session_row_id", "thread_id", "request_correlation_id", "provider_session_context"} <= call_columns
    assert "capture_degraded" in payload_columns
def test_headers_redacted():
    h=g.headers({'Authorization':'x','Cookie':'y','Set-Cookie':'z','Proxy-Authorization':'p','X-Test':'ok'}); assert h=={'X-Test':'ok'}
def test_usage_json(): assert g.extract_usage(b'{"usage":{"input_tokens":1}}')['input_tokens']==1
def test_usage_sse(): assert g.extract_usage(b'data: {"usage":{"total_tokens":4}}\n\n')['total_tokens']==4
def test_usage_missing(): assert g.extract_usage(b'data: {}\n\n') is None
def test_default_constants(): assert g.DEFAULT_LISTEN=='127.0.0.1:8787' and g.DEFAULT_DB=='gateway.db'
def test_iso_format(): assert 'T' in g.iso()
def test_purge_empty(tmp_path): assert g.purge(str(db_with_schema(tmp_path)))==0
def test_schema_idempotent(tmp_path): p=db_with_schema(tmp_path); g.init(str(p)); assert sqlite3.connect(p).execute('select count(*) from sessions').fetchone()==(0,)


def test_legacy_database_is_discarded_before_new_indexes(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("create table sessions(id integer primary key); create table calls(id integer primary key, session_id integer); insert into sessions values(7);")
    g.init(str(path))
    with sqlite3.connect(path) as connection:
        assert connection.execute("select count(*) from sessions").fetchone() == (0,)
        assert connection.execute("pragma table_info(calls)").fetchall()[1][1] == "session_id"
        assert connection.execute("select name from sqlite_master where type='index' and name='calls_runtime_session_idx'").fetchone()


def test_partial_calls_schema_missing_provider_context_is_discarded(tmp_path):
    path = tmp_path / "partial.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE runtime_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, started_at TEXT, last_seen_at TEXT, cwd TEXT, project_name TEXT, metadata_json TEXT);
            CREATE TABLE client_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT, client_session_id TEXT, identity_status TEXT, identity_source TEXT, first_seen_at TEXT, last_seen_at TEXT, metadata_json TEXT);
            CREATE TABLE calls(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, runtime_session_id INTEGER, client_session_row_id INTEGER, thread_id TEXT, request_correlation_id TEXT, created_at TEXT, completed_at TEXT, provider TEXT, api_family TEXT, endpoint TEXT, model TEXT, stream INTEGER, status TEXT, status_code INTEGER, first_byte_at TEXT, duration_ms INTEGER, input_bytes INTEGER, output_bytes INTEGER, error_type TEXT, error_message TEXT);
            CREATE TABLE payloads(id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id INTEGER UNIQUE, capture_degraded INTEGER);
            """
        )
    g.init(str(path))
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(calls)")}
        assert "provider_session_context" in columns
class ExtractionUpstream(BaseHTTPRequestHandler):
    def do_POST(self):
        n=int(self.headers.get('Content-Length','0')); self.rfile.read(n)
        body=b'{"ok":true}'; self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,*a): pass

def _free_port():
    with socket.socket() as s: s.bind(('127.0.0.1',0)); return s.getsockname()[1]

def _start_extraction_gateway(tmp_path):
    up=ThreadingHTTPServer(('127.0.0.1',0),ExtractionUpstream); threading.Thread(target=up.serve_forever,daemon=True).start(); port=_free_port(); db=tmp_path/'extract.db'
    proc=subprocess.Popen([sys.executable,str(Path(__file__).parents[1]/'prompt_harbor.py'),'start','--database',str(db),'--listen',f'127.0.0.1:{port}','--upstream',f'http://127.0.0.1:{up.server_port}'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    deadline=time.time()+5
    while time.time()<deadline:
        try:
            c=http.client.HTTPConnection('127.0.0.1',port,timeout=1); c.connect(); c.close(); return proc,up,port,db
        except OSError: time.sleep(.03)
    proc.kill(); up.shutdown(); raise AssertionError('gateway did not start')

def test_model_extraction_shape(tmp_path):
    proc,up,port,db=_start_extraction_gateway(tmp_path)
    try:
        c=http.client.HTTPConnection('127.0.0.1',port); c.request('POST','/v1/responses',b'{"model":"real-model"}'); r=c.getresponse(); assert r.status==200; r.read(); c.close()
        row=None; deadline=time.time()+3
        while time.time()<deadline:
            row=sqlite3.connect(db).execute('select model,stream from calls').fetchone()
            if row: break
            time.sleep(.03)
        assert row==('real-model',0)
    finally: proc.terminate(); proc.wait(timeout=3); up.shutdown()

def test_stream_boolean(tmp_path):
    proc,up,port,db=_start_extraction_gateway(tmp_path)
    try:
        for payload in (b'{"model":"true","stream":true}',b'{"model":"false","stream":false}',b'{"model":"missing"}'):
            c=http.client.HTTPConnection('127.0.0.1',port); c.request('POST','/v1/responses',payload); r=c.getresponse(); assert r.status==200; r.read(); c.close()
        rows=[]; deadline=time.time()+3
        while time.time()<deadline:
            rows=sqlite3.connect(db).execute('select model,stream from calls order by id').fetchall()
            if len(rows)==3: break
            time.sleep(.03)
        assert rows==[('true',1),('false',0),('missing',0)]
    finally: proc.terminate(); proc.wait(timeout=3); up.shutdown()
def test_headers_case_insensitive(): assert 'authorization' not in {k.lower() for k in g.headers({'authorization':'x'})}
def test_init_cli(tmp_path):
    p=tmp_path/'c.db'; r=subprocess.run([sys.executable,'prompt_harbor.py','init','--database',str(p)],capture_output=True,text=True); assert r.returncode==0 and p.exists()
def test_help_mentions_commands():
    r=subprocess.run([sys.executable,'prompt_harbor.py','--help'],capture_output=True,text=True); assert r.returncode==0; assert all(x in r.stdout for x in ('start','init','list','show','purge','--database','--listen','--upstream'))
def test_list_empty(tmp_path):
    p=db_with_schema(tmp_path); r=subprocess.run([sys.executable,'prompt_harbor.py','list','--database',str(p)],capture_output=True,text=True); assert r.returncode==0 and r.stdout==''
def test_show_missing_nonzero(tmp_path):
    p=db_with_schema(tmp_path); r=subprocess.run([sys.executable,'prompt_harbor.py','show','999','--database',str(p)],capture_output=True,text=True); assert r.returncode!=0 and 'not found' in r.stderr
def test_env_database(tmp_path):
    p=tmp_path/'e.db'; e=dict(os.environ,PROMPT_HARBOR_DB=str(p)); r=subprocess.run([sys.executable,'prompt_harbor.py','init'],env=e,capture_output=True,text=True); assert r.returncode==0 and p.exists()
def test_calls_schema_columns(tmp_path):
    p=db_with_schema(tmp_path); cols={r[1] for r in sqlite3.connect(p).execute('pragma table_info(calls)')}; assert {'session_id','model','stream','status_code','duration_ms'}<=cols
def test_payload_schema_columns(tmp_path):
    p=db_with_schema(tmp_path); cols={r[1] for r in sqlite3.connect(p).execute('pragma table_info(payloads)')}; assert {'request_body','response_body','response_complete','request_truncated'}<=cols
def test_usage_schema_columns(tmp_path):
    p=db_with_schema(tmp_path); cols={r[1] for r in sqlite3.connect(p).execute('pragma table_info(usage)')}; assert {'input_tokens','output_tokens','total_tokens','raw_usage_json'}<=cols
