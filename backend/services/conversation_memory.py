"""Owner/source-scoped durable conversation memory. No cross-user retrieval."""
import hashlib
import json
import sqlite3
from backend.config import get_settings
from backend.services.identity_service import current_user_id


def _connect():
    root=get_settings().app_data_dir
    root.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(root/'conversation_memory.sqlite',timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS turns (seq INTEGER PRIMARY KEY, scope TEXT, event TEXT, role TEXT, text TEXT, UNIQUE(scope,event))')
    db.execute('CREATE TABLE IF NOT EXISTS reports (scope TEXT PRIMARY KEY, spec TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS facts (scope TEXT PRIMARY KEY, payload TEXT)')
    return db


def scope(source_id):
    return hashlib.sha256(json.dumps([current_user_id(),source_id]).encode()).hexdigest()


def read(source_id,limit=30):
    with _connect() as db:
        rows=db.execute('SELECT role,text FROM turns WHERE scope=? ORDER BY seq DESC LIMIT ?', (scope(source_id),min(max(limit,1),50))).fetchall()
        spec=db.execute('SELECT spec FROM reports WHERE scope=?',(scope(source_id),)).fetchone()
        facts=db.execute('SELECT payload FROM facts WHERE scope=?',(scope(source_id),)).fetchone()
    return {'conversation':[{'role':r,'text':t} for r,t in reversed(rows)],'reportSpec':json.loads(spec[0]) if spec else None,'verifiedContext':json.loads(facts[0]) if facts else {}}


def remember(run, context):
    if not run.get('useConversationMemory'): return
    key=scope(run['sourceId'])
    answer=context.get('clarificationAnswer')
    user=answer if answer else run['goal']
    event=run['runId']+':'+hashlib.sha256(str(answer or '').encode()).hexdigest()[:16]
    messages=[];spec=None;facts=None
    for step in run.get('steps',[]):
        result=step.get('result')
        if step.get('status')!='completed' or not isinstance(result,dict):continue
        text=result.get('assistantMessage') or result.get('answer') or result.get('message')
        if text: messages.append(str(text))
        if step['action']=='answer_data_question':
            facts={k:result[k] for k in ('analysisPlan','sourceFields','filters','method') if k in result}
        if step['action']=='plan_dashboard':spec={k:v for k,v in result.items() if k in {'page','visuals','additionalPages','design'}}
        if step['action']=='edit_report':spec=result.get('reportSpec')
    if run.get('status')=='needs_user':messages.append(run.get('help',{}).get('message','Please clarify.'))
    with _connect() as db:
        db.execute('INSERT OR IGNORE INTO turns(scope,event,role,text) VALUES(?,?,?,?)',(key,event+':user','user',str(user)[:4000]))
        if messages:db.execute('INSERT OR REPLACE INTO turns(scope,event,role,text) VALUES(?,?,?,?)',(key,event+':assistant','assistant','\n'.join(messages)[:4000]))
        if facts:db.execute('INSERT OR REPLACE INTO facts VALUES(?,?)',(key,json.dumps(facts,default=str)))
        if spec:db.execute('INSERT OR REPLACE INTO reports VALUES(?,?)',(key,json.dumps(spec,default=str)))
        # Bound retained conversation; report remains until explicitly cleared.
        db.execute('DELETE FROM turns WHERE scope=? AND seq NOT IN (SELECT seq FROM turns WHERE scope=? ORDER BY seq DESC LIMIT 200)',(key,key))


def clear(source_id):
    with _connect() as db:
        db.execute('DELETE FROM turns WHERE scope=?',(scope(source_id),))
        db.execute('DELETE FROM reports WHERE scope=?',(scope(source_id),))
        db.execute('DELETE FROM facts WHERE scope=?',(scope(source_id),))
