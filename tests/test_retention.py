import sqlite3, prompt_harbor as g
from datetime import datetime, timezone, timedelta
import http.client, subprocess, sys, threading, time
from http.server import ThreadingHTTPServer
from pathlib import Path
from test_gateway import JsonUpstream, free
def seed(p, age):
 c=sqlite3.connect(p); old=f"datetime('now','-{age} days')"; c.execute(f"insert into sessions(agent,started_at,last_seen_at) values('x',{old},{old})"); sid=c.execute('select last_insert_rowid()').fetchone()[0]; c.execute(f"insert into calls(session_id,created_at,status) values({sid},{old},'succeeded')"); c.commit(); c.close()
def test_purge_old_call(tmp_path):
 p=tmp_path/'r.db'; g.init(str(p)); seed(p,3); assert g.purge(str(p))==1; assert sqlite3.connect(p).execute('select count(*) from calls').fetchone()==(0,)
def test_purge_keeps_recent(tmp_path):
 p=tmp_path/'r.db'; g.init(str(p)); seed(p,1); assert g.purge(str(p))==0; assert sqlite3.connect(p).execute('select count(*) from calls').fetchone()==(1,)
def test_purge_repeat_idempotent(tmp_path):
 p=tmp_path/'r.db'; g.init(str(p)); seed(p,4); g.purge(str(p)); assert g.purge(str(p))==0
def test_business_cascade_without_fk(tmp_path):
 p=tmp_path/'r.db'; g.init(str(p)); c=sqlite3.connect(p); assert c.execute('pragma foreign_keys').fetchone()==(0,)
def test_sessions_survive_purge(tmp_path):
 p=tmp_path/'r.db'; g.init(str(p)); seed(p,3); g.purge(str(p)); assert sqlite3.connect(p).execute('select count(*) from sessions').fetchone()==(1,)

def seed_chain(path, age_days, attempts=1, model='m'):
    stamp=(datetime.now(timezone.utc)-timedelta(days=age_days)).isoformat()
    with sqlite3.connect(path) as c:
        c.execute('insert into sessions(agent,started_at,last_seen_at) values(?,?,?)',('seed',stamp,stamp)); sid=c.execute('select last_insert_rowid()').fetchone()[0]
        c.execute('insert into calls(session_id,created_at,model,status) values(?,?,?,?)',(sid,stamp,model,'succeeded')); cid=c.execute('select last_insert_rowid()').fetchone()[0]
        for n in range(attempts):
            c.execute('insert into attempts(call_id,attempt_no,started_at,completed_at,status) values(?,?,?,?,?)',(cid,n+1,stamp,stamp,'succeeded')); aid=c.execute('select last_insert_rowid()').fetchone()[0]
            c.execute('insert into payloads(attempt_id,request_body,response_body,response_complete) values(?,?,?,1)',(aid,b'request',b'response'))
            c.execute('insert into usage(attempt_id,input_tokens,output_tokens,total_tokens) values(?,?,?,?)',(aid,1,2,3))
    return sid,cid

def test_purge_removes_full_expired_chain_and_keeps_recent_chain(tmp_path):
    p=tmp_path/'chain.db'; g.init(str(p)); old_sid,old_cid=seed_chain(p,3,attempts=2,model='old'); recent_sid,recent_cid=seed_chain(p,1,attempts=1,model='recent')
    assert g.purge(str(p))==1
    with sqlite3.connect(p) as c:
        assert c.execute('select count(*) from calls where id=?',(old_cid,)).fetchone()==(0,)
        assert c.execute('select count(*) from attempts where call_id=?',(old_cid,)).fetchone()==(0,)
        assert c.execute('select count(*) from payloads').fetchone()==(1,)
        assert c.execute('select count(*) from usage').fetchone()==(1,)
        assert c.execute('select id,model,status from calls').fetchone()==(recent_cid,'recent','succeeded')
        assert c.execute('select count(*) from sessions where id in (?,?)',(old_sid,recent_sid)).fetchone()==(2,)
    assert g.purge(str(p))==0

def test_startup_purge_cleans_expired_chain_on_restart(tmp_path):
    p=tmp_path/'restart.db'; g.init(str(p)); old_sid,old_cid=seed_chain(p,3,attempts=2,model='old'); recent_sid,recent_cid=seed_chain(p,1,attempts=1,model='recent')
    up=ThreadingHTTPServer(('127.0.0.1',0),JsonUpstream); threading.Thread(target=up.serve_forever,daemon=True).start()
    def start():
        port=free(); cmd=[sys.executable,str(Path(__file__).parents[1]/'prompt_harbor.py'),'start','--database',str(p),'--listen',f'127.0.0.1:{port}','--upstream',f'http://127.0.0.1:{up.server_port}']; proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        deadline=time.time()+5
        while time.time()<deadline:
            try:
                c=http.client.HTTPConnection('127.0.0.1',port,timeout=1); c.connect(); c.close(); return proc,port
            except OSError: time.sleep(.03)
        proc.kill(); raise AssertionError('gateway did not start')
    proc=None
    try:
        proc,port=start(); proc.terminate(); proc.wait(timeout=3)
        restarted_sid,restarted_cid=seed_chain(p,3,attempts=2,model='restart-old'); restart_recent_sid,restart_recent_cid=seed_chain(p,1,attempts=1,model='restart-recent')
        proc,port=start()
        with sqlite3.connect(p) as c:
            assert c.execute('select count(*) from calls where id=?',(old_cid,)).fetchone()==(0,)
            assert c.execute('select count(*) from attempts where call_id=?',(old_cid,)).fetchone()==(0,)
            assert c.execute('select count(*) from calls where id=?',(restarted_cid,)).fetchone()==(0,)
            assert c.execute('select count(*) from attempts where call_id=?',(restarted_cid,)).fetchone()==(0,)
            assert c.execute('select count(*) from calls where id in (?,?)',(recent_cid,restart_recent_cid)).fetchone()==(2,)
            assert c.execute('select count(*) from payloads').fetchone()==(2,)
            assert c.execute('select count(*) from usage').fetchone()==(2,)
    finally:
        if proc is not None and proc.poll() is None: proc.terminate(); proc.wait(timeout=3)
        up.shutdown()
