import argparse
import os, subprocess, sys, sqlite3
import prompt_harbor as g
from prompt_harbor.config import ConfigError, load_settings
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


def test_cli_explicit_scope_and_capture_fields_are_safe(tmp_path):
 p=tmp_path/'p.db'; subprocess.run([sys.executable,'prompt_harbor.py','init','--database',str(p)],check=True,capture_output=True)
 with sqlite3.connect(p) as c:
  c.execute("insert into runtime_sessions(agent,started_at,last_seen_at) values('cli','now','now')"); runtime_id=c.execute('select last_insert_rowid()').fetchone()[0]
  c.execute("insert into client_sessions(client_session_id,identity_status,identity_source,first_seen_at,last_seen_at) values('opaque','explicit','session-id','now','now')"); client_row=c.execute('select last_insert_rowid()').fetchone()[0]
  c.execute("insert into calls(runtime_session_id,client_session_row_id,thread_id,request_correlation_id,created_at,endpoint,model,status,status_code) values(?,?,?,?,?,?,?,?,?)",(runtime_id,client_row,'thread','request','now','/v1/responses','m','succeeded',200)); call_id=c.execute('select last_insert_rowid()').fetchone()[0]
  c.execute("insert into attempts(call_id,status,request_headers_json) values(?,?,?)",(call_id,'succeeded','{"X-Test":"ok","Authorization":"must-not-print"}')); attempt_id=c.execute('select last_insert_rowid()').fetchone()[0]
  c.execute("insert into payloads(attempt_id,request_body,response_body,response_truncated,capture_degraded) values(?,?,?,?,?)",(attempt_id,b'request',b'response',1,1))
 r=subprocess.run([sys.executable,'prompt_harbor.py','show',str(call_id),'--database',str(p)],capture_output=True,text=True)
 assert r.returncode==0 and 'runtime_session_id' in r.stdout and 'client_session_id' in r.stdout
 assert 'capture_degraded=1' in r.stdout and 'Authorization' not in r.stdout
def test_cli_argument_overrides_environment(tmp_path):
 env_path=tmp_path/'env.db'; arg_path=tmp_path/'arg.db'; env=dict(os.environ,PROMPT_HARBOR_DB=str(env_path)); r=subprocess.run([sys.executable,'prompt_harbor.py','init','--database',str(arg_path)],env=env,capture_output=True,text=True); assert r.returncode==0 and arg_path.exists() and not env_path.exists()
def test_environment_overrides_default(tmp_path):
 env_path=tmp_path/'env.db'; env=dict(os.environ,PROMPT_HARBOR_DB=str(env_path)); r=subprocess.run([sys.executable,'prompt_harbor.py','init'],env=env,capture_output=True,text=True); assert r.returncode==0 and env_path.exists()

def test_config_file_values_are_loaded(tmp_path):
 cfg=tmp_path/'gateway.ini'; cfg.write_text('[gateway]\ndatabase = config.db\nretention_days = 7\nmax_body = 123\n[sidecar]\nurl = http://127.0.0.1:8790\n', encoding='utf-8')
 settings=load_settings(argparse.Namespace(config=str(cfg)))
 assert settings.database == 'config.db' and settings.retention_days == 7 and settings.max_body == 123
 assert settings.sidecar_url == 'http://127.0.0.1:8790'

def test_config_file_is_used_by_cli(tmp_path):
 cfg=tmp_path/'gateway.ini'; database=tmp_path/'configured.db'; cfg.write_text(f'[gateway]\ndatabase = {database}\n', encoding='utf-8')
 result=subprocess.run([sys.executable, 'prompt_harbor.py', 'init', '--config', str(cfg)], capture_output=True, text=True)
 assert result.returncode == 0 and database.exists()

def test_cli_and_environment_override_config_file(tmp_path, monkeypatch):
 cfg=tmp_path/'gateway.ini'; cfg.write_text('[gateway]\ndatabase = config.db\nmax_body = 123\n', encoding='utf-8')
 monkeypatch.setenv('PROMPT_HARBOR_DB', 'env.db')
 settings=load_settings(argparse.Namespace(config=str(cfg), database='cli.db'))
 assert settings.database == 'cli.db'
 settings=load_settings(argparse.Namespace(config=str(cfg)))
 assert settings.database == 'env.db'

def test_invalid_config_value_is_reported(tmp_path):
 cfg=tmp_path/'gateway.ini'; cfg.write_text('[gateway]\nretention_days = no\n', encoding='utf-8')
 try:
  load_settings(argparse.Namespace(config=str(cfg)))
 except ConfigError as exc:
  assert 'retention_days' in str(exc)
 else:
  raise AssertionError('invalid configuration was accepted')
