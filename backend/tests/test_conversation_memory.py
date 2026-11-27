from types import SimpleNamespace
from backend.services import conversation_memory as memory


def test_memory_persists_evidence_and_report_but_isolates_owner_and_source(monkeypatch,tmp_path):
    monkeypatch.setattr(memory,'get_settings',lambda:SimpleNamespace(app_data_dir=tmp_path))
    owner=['alice'];monkeypatch.setattr(memory,'current_user_id',lambda:owner[0])
    run={'runId':'r1','sourceId':'s1','goal':'Total revenue','useConversationMemory':True,'status':'completed','steps':[
        {'action':'answer_data_question','status':'completed','result':{'answer':'Revenue is 25','sourceFields':['Sales.Revenue'],'analysisPlan':{'table':'Sales','measure':'Revenue'}}},
        {'action':'plan_dashboard','status':'completed','result':{'page':'Overview','visuals':[{'type':'card'}],'assistantMessage':'Draft ready'}}]}
    memory.remember(run,{})
    memory.remember(run,{})
    restored=memory.read('s1')
    assert len(restored['conversation'])==2
    assert restored['verifiedContext']['sourceFields']==['Sales.Revenue']
    assert restored['reportSpec']['page']=='Overview'
    assert memory.read('s2')['conversation']==[]
    owner[0]='bob';assert memory.read('s1')['conversation']==[]
    owner[0]='alice';memory.clear('s1')
    assert memory.read('s1')['conversation']==[]
    assert memory.read('s1')['verifiedContext']=={}
    assert memory.read('s1')['reportSpec'] is None


def test_clarification_answer_becomes_followup_memory(monkeypatch,tmp_path):
    monkeypatch.setattr(memory,'get_settings',lambda:SimpleNamespace(app_data_dir=tmp_path))
    monkeypatch.setattr(memory,'current_user_id',lambda:'alice')
    run={'runId':'r2','sourceId':'s','goal':'Revenue','useConversationMemory':True,'status':'needs_user','steps':[], 'help':{'message':'Which revenue?'}}
    memory.remember(run,{})
    run['status']='completed';run['steps']=[{'action':'respond','status':'completed','result':{'message':'Using net revenue'}}]
    memory.remember(run,{'clarificationAnswer':'Net revenue'})
    assert [m['text'] for m in memory.read('s')['conversation']]==['Revenue','Which revenue?','Net revenue','Using net revenue']


def test_memory_api_restores_validated_source_and_can_clear(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.api import agent
    monkeypatch.setattr(memory,'get_settings',lambda:SimpleNamespace(app_data_dir=tmp_path))
    monkeypatch.setattr(memory,'current_user_id',lambda:'test-owner')
    monkeypatch.setattr(agent,'load_source_metadata',lambda _: {'tables':{'Sales':{'columns':['Region','Revenue']}}})
    memory.remember({'runId':'api','sourceId':'s','goal':'Revenue?','status':'completed','useConversationMemory':True,
                     'steps':[{'action':'answer_data_question','status':'completed','result':{'answer':'Revenue is 25'}}]}, {})
    with TestClient(app) as client:
        restored=client.get('/api/agent/memory/s')
        assert restored.status_code==200
        assert restored.json()['conversation'][1]['text']=='Revenue is 25'
        assert restored.json()['tables'][0]['name']=='Sales'
        assert client.delete('/api/agent/memory/s').json()['cleared'] is True
        assert client.get('/api/agent/memory/s').json()['conversation']==[]
        def missing(_):raise ValueError('source not found')
        monkeypatch.setattr(agent,'load_source_metadata',missing)
        assert client.get('/api/agent/memory/unknown').status_code==404
