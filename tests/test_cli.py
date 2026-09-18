import os, subprocess, sys, sqlite3
import prompt_harbor as g
def test_global_help_exit_code():
 r=subprocess.run([sys.executable,'prompt_harbor.py','--help'],capture_output=True,text=True); assert r.returncode==0
def test_unknown_command_nonzero():
 r=subprocess.run([sys.executable,'prompt_harbor.py','unknown'],capture_output=True,text=True); assert r.returncode!=0
def test_purge_cli_output(tmp_path):
 p=tmp_path/'p.db'; subprocess.run([sys.executable,'prompt_harbor.py','init','--database',str(p)],check=True,capture_output=True); r=subprocess.run([sys.executable,'prompt_harbor.py','purge','--database',str(p)],capture_output=True,text=True); assert r.returncode==0 and 'purged' in r.stdout
def test_list_has_metadata_columns(tmp_path):
 p=tmp_path/'p.db'; subprocess.run([sys.executable,'prompt_harbor.py','init','--database',str(p)],check=True,capture_output=True)
 with sqlite3.connect(p) as c:
  c.execute("insert into sessions(agent) values('cli')"); sid=c.execute('select last_insert_rowid()').fetchone()[0]
  c.execute("insert into calls(session_id,created_at,endpoint,model,status,status_code,duration_ms,input_bytes,output_bytes) values(?,?,?,?,?,?,?,?,?)",(sid,'2026-09-17T12:34:56+00:00','/v1/responses','cli-model','succeeded',201,42,12,34)); c.execute("insert into attempts(call_id,status) values(last_insert_rowid(),'succeeded')"); c.execute("insert into payloads(attempt_id,request_body,response_body) values(last_insert_rowid(),?,?)",(b'SECRET_BODY',b'response'))
 r=subprocess.run([sys.executable,'prompt_harbor.py','list','--database',str(p)],capture_output=True,text=True); assert r.returncode==0
 assert '2026-09-17T12:34:56+00:00 cli-model succeeded 201 42ms 12/34 /v1/responses' in r.stdout
 assert 'SECRET_BODY' not in r.stdout and 'response\n' not in r.stdout
def test_cli_argument_overrides_environment(tmp_path):
 env_path=tmp_path/'env.db'; arg_path=tmp_path/'arg.db'; env=dict(os.environ,PROMPT_HARBOR_DB=str(env_path)); r=subprocess.run([sys.executable,'prompt_harbor.py','init','--database',str(arg_path)],env=env,capture_output=True,text=True); assert r.returncode==0 and arg_path.exists() and not env_path.exists()
def test_environment_overrides_default(tmp_path):
 env_path=tmp_path/'env.db'; env=dict(os.environ,PROMPT_HARBOR_DB=str(env_path)); r=subprocess.run([sys.executable,'prompt_harbor.py','init'],env=env,capture_output=True,text=True); assert r.returncode==0 and env_path.exists()
