import sqlite3, agent_gateway as g
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
