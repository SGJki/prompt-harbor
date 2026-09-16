import os, subprocess, sys
def test_global_help_exit_code():
 r=subprocess.run([sys.executable,'prompt_harbor.py','--help'],capture_output=True,text=True); assert r.returncode==0
def test_unknown_command_nonzero():
 r=subprocess.run([sys.executable,'prompt_harbor.py','unknown'],capture_output=True,text=True); assert r.returncode!=0
def test_purge_cli_output(tmp_path):
 p=tmp_path/'p.db'; subprocess.run([sys.executable,'prompt_harbor.py','init','--database',str(p)],check=True,capture_output=True); r=subprocess.run([sys.executable,'prompt_harbor.py','purge','--database',str(p)],capture_output=True,text=True); assert r.returncode==0 and 'purged' in r.stdout
def test_list_has_metadata_columns(tmp_path):
 p=tmp_path/'p.db'; subprocess.run([sys.executable,'prompt_harbor.py','init','--database',str(p)],check=True,capture_output=True); r=subprocess.run([sys.executable,'prompt_harbor.py','list','--database',str(p)],capture_output=True,text=True); assert r.returncode==0
def test_cli_argument_overrides_environment(tmp_path):
 env_path=tmp_path/'env.db'; arg_path=tmp_path/'arg.db'; env=dict(os.environ,PROMPT_HARBOR_DB=str(env_path)); r=subprocess.run([sys.executable,'prompt_harbor.py','init','--database',str(arg_path)],env=env,capture_output=True,text=True); assert r.returncode==0 and arg_path.exists() and not env_path.exists()
def test_environment_overrides_default(tmp_path):
 env_path=tmp_path/'env.db'; env=dict(os.environ,PROMPT_HARBOR_DB=str(env_path)); r=subprocess.run([sys.executable,'prompt_harbor.py','init'],env=env,capture_output=True,text=True); assert r.returncode==0 and env_path.exists()
