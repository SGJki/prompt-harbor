import json, sqlite3, subprocess, sys
import agent_gateway as g

def seeded(tmp_path):
 p=tmp_path/'s.db'; g.init(str(p)); c=sqlite3.connect(p); c.execute("insert into sessions(agent) values('codex')"); sid=c.execute('select last_insert_rowid()').fetchone()[0]; c.execute("insert into calls(session_id,endpoint,model,status,status_code,input_bytes,output_bytes) values(?,?,?,?,?,?,?)",(sid,'/v1/responses','m','succeeded',200,3,4)); cid=c.execute('select last_insert_rowid()').fetchone()[0]; c.execute("insert into attempts(call_id,status,request_headers_json,response_headers_json) values(?,?,?,?)",(cid,'succeeded',json.dumps({'X-Test':'ok'}),json.dumps({'Content-Type':'application/json'}))); aid=c.execute('select last_insert_rowid()').fetchone()[0]; c.execute("insert into payloads(attempt_id,request_body,response_body,response_complete) values(?,?,?,1)",(aid,b'abc',b'body')); c.commit(); c.close(); return p,cid
def test_sensitive_header_names_all_redacted():
 for key in ('Authorization','authorization','Cookie','Set-Cookie','Proxy-Authorization'): assert key.lower() not in {k.lower() for k in g.headers({key:'secret'})}
def test_prompt_content_is_preserved(tmp_path):
 p,cid=seeded(tmp_path); row=sqlite3.connect(p).execute('select request_body,response_body from payloads').fetchone(); assert row==(b'abc',b'body')
def test_list_does_not_print_body(tmp_path):
 p,_=seeded(tmp_path); r=subprocess.run([sys.executable,'agent_gateway.py','list','--database',str(p)],capture_output=True,text=True); assert 'body' not in r.stdout and 'abc' not in r.stdout
def test_show_prints_payload(tmp_path):
 p,cid=seeded(tmp_path); r=subprocess.run([sys.executable,'agent_gateway.py','show',str(cid),'--database',str(p)],capture_output=True,text=True); assert r.returncode==0 and 'abc' in r.stdout and 'body' in r.stdout
def test_show_headers_are_json(tmp_path):
 p,cid=seeded(tmp_path); r=subprocess.run([sys.executable,'agent_gateway.py','show',str(cid),'--database',str(p)],capture_output=True,text=True); assert '[HEADERS]' in r.stdout and 'X-Test' in r.stdout
