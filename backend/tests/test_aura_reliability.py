import asyncio
import csv
import io
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from automation.ai.azure_openai_client import AIServiceError, AzureOpenAIClient
from backend.agents import graph_workflow, langgraph_tools
from backend.services import run_service, token_usage_service as usage
from backend.services.ai_analysis_service import AnalysisClarificationRequired
from backend.services.identity_service import identity_context


def test_validation_errors_propagate_for_repair(monkeypatch):
    class Tool:
        async def ainvoke(self, payload):
            raise ValueError('Missing required measure binding')
    monkeypatch.setitem(langgraph_tools.TOOLS, 'plan_dashboard', Tool())
    with pytest.raises(ValueError, match='measure binding'):
        asyncio.run(graph_workflow.execute_agent_action('plan_dashboard', 'source', 'Build dashboard', {}))


def test_valid_report_survives_narration_outage(monkeypatch):
    async def unavailable(*args):
        raise AIServiceError('Unavailable')
    monkeypatch.setattr(graph_workflow, '_json', unavailable)
    spec = {'page': 'Overview', 'visuals': [{'type': 'card'}],
            'additionalPages': [{'page': 'Detail', 'visuals': [{'type': 'table'}]}]}
    result = asyncio.run(graph_workflow._response({'action': 'plan_dashboard', 'goal': 'Build report', 'result': spec}))['result']
    assert result['visuals'] == spec['visuals']
    assert '2 page(s), 2 visual(s)' in result['assistantMessage']


def test_repair_context_reaches_next_attempt(monkeypatch, tmp_path):
    monkeypatch.setattr(run_service, 'get_settings', lambda: SimpleNamespace(app_data_dir=tmp_path))
    contexts = []
    async def tool(action, source, goal, context):
        contexts.append(context)
        if len(contexts) == 1:
            raise ValueError('Missing binding')
        return {'page': 'Overview', 'visuals': []}
    monkeypatch.setattr(run_service, 'execute_tool', tool)
    run = run_service.create_run('source', 'Build dashboard', [{'action': 'plan_dashboard', 'reason': 'Plan'}])
    result = asyncio.run(run_service.execute_run(run, {}))
    assert result['status'] == 'completed'
    assert contexts[1]['repair']['validationError'] == 'Missing binding'


def test_repeated_answered_question_stops_and_poll_does_not_replay(monkeypatch, tmp_path):
    monkeypatch.setattr(run_service, 'get_settings', lambda: SimpleNamespace(app_data_dir=tmp_path))
    calls = []
    async def tool(action, source, goal, context):
        calls.append(context)
        raise AnalysisClarificationRequired('Which metric?', ['Revenue', 'Cost'], provider='azure', model='test')
    monkeypatch.setattr(run_service, 'execute_tool', tool)
    run = run_service.create_run('source', 'Build dashboard', [{'action': 'plan_dashboard', 'reason': 'Plan'}])
    assert asyncio.run(run_service.execute_run(run, {}))['status'] == 'needs_user'
    assert asyncio.run(run_service.execute_run(run, {}))['status'] == 'needs_user'
    assert len(calls) == 1
    result = asyncio.run(run_service.execute_run(run, {'clarificationAnswer': 'Revenue'}))
    assert result['status'] == 'failed'
    assert result['clarificationHistory'][0]['answer'] == 'Revenue'
    assert len(calls) == 2


def response(content='{}', known=True):
    return SimpleNamespace(id='response-1', choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15,
            prompt_tokens_details=SimpleNamespace(cached_tokens=3),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=2)) if known else None)


def client(result):
    transport = AzureOpenAIClient.__new__(AzureOpenAIClient)
    transport.model = 'test-model'
    async def create(**kwargs):
        return result
    transport._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return transport


def test_invalid_json_and_connection_checks_are_counted(monkeypatch, tmp_path):
    monkeypatch.setenv('TOKEN_USAGE_DB', str(tmp_path / 'tokens.sqlite3'))
    with pytest.raises(AIServiceError):
        asyncio.run(client(response('broken JSON')).generate_json('dashboard_planning', 'prompt', {}))
    asyncio.run(client(response()).test_connection())
    asyncio.run(client(response(known=False)).generate_json('agent_routing', 'prompt', {}))
    total = usage.usage_summary()
    assert total['calls'] == 3
    assert total['total_tokens'] == 30
    assert total['input_tokens'] == 20
    assert total['output_tokens'] == 10
    assert total['calls_without_usage'] == 1
    rows = list(csv.DictReader(io.StringIO(usage.usage_csv())))
    assert rows[0]['outcome'] == 'failed'
    assert rows[0]['cached_tokens'] == '3'
    assert rows[0]['reasoning_tokens'] == '2'


def test_token_ledger_concurrent_calls_and_user_isolation(monkeypatch, tmp_path):
    monkeypatch.setenv('TOKEN_USAGE_DB', str(tmp_path / 'tokens.sqlite3'))
    def write(_):
        usage.record_token_usage(operation='dashboard_planning', model='test', response=response(), outcome='succeeded', latency_ms=1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(20)))
    assert usage.usage_summary()['total_tokens'] == 300
    with (tmp_path / 'tokens.csv').open(encoding='utf-8-sig', newline='') as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 20
    assert sum(int(row['total_tokens']) for row in rows) == 300
    with (tmp_path / 'daily-usage' / 'daily-usage.csv').open(encoding='utf-8-sig', newline='') as file:
        daily = list(csv.DictReader(file))
    assert len(daily) == 1
    assert daily[0]['calls'] == '20'
    assert daily[0]['total_tokens'] == '300'
    token = identity_context.set('another-user')
    try:
        assert usage.usage_summary()['calls'] == 0
        assert len(list(csv.DictReader(io.StringIO(usage.usage_csv())))) == 0
    finally:
        identity_context.reset(token)


def test_usage_api_exports_only_authenticated_owner(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import app
    monkeypatch.setenv('TOKEN_USAGE_DB', str(tmp_path / 'tokens.sqlite3'))
    monkeypatch.setenv('AUTH_REQUIRED', 'false')
    token = identity_context.set('alice')
    try:
        usage.record_token_usage(operation='test', model='test', response=response(), outcome='succeeded', latency_ms=1)
    finally:
        identity_context.reset(token)
    api = TestClient(app)
    assert api.get('/api/usage/tokens', headers={'X-AURA-User': 'alice'}).json()['total_tokens'] == 15
    assert api.get('/api/usage/tokens', headers={'X-AURA-User': 'bob'}).json()['total_tokens'] == 0
    exported = api.get('/api/usage/tokens/export', headers={'X-AURA-User': 'alice'})
    assert exported.status_code == 200
    assert 'text/csv' in exported.headers['content-type']
    assert len(list(csv.DictReader(io.StringIO(exported.text)))) == 1


def test_usage_scopes_isolate_chats_backend_sessions_and_owners(monkeypatch, tmp_path):
    from backend.logging_config import chat_session_id_context
    from fastapi.testclient import TestClient
    from backend.main import app

    monkeypatch.setenv('TOKEN_USAGE_DB', str(tmp_path / 'tokens.sqlite3'))
    monkeypatch.setenv('AUTH_REQUIRED', 'false')
    chat_a = '11111111-1111-4111-8111-111111111111'
    chat_b = '22222222-2222-4222-8222-222222222222'
    owner_token = identity_context.set('alice')
    try:
        for chat, backend in [(chat_a, 'old-backend'), (chat_a, usage.SESSION_ID), (chat_b, usage.SESSION_ID), (None, usage.SESSION_ID)]:
            chat_token = chat_session_id_context.set(chat)
            with monkeypatch.context() as context:
                context.setattr(usage, 'SESSION_ID', backend)
                try:
                    usage.record_token_usage(operation='test', model='test', response=response(), outcome='succeeded', latency_ms=1)
                finally:
                    chat_session_id_context.reset(chat_token)
    finally:
        identity_context.reset(owner_token)

    with TestClient(app) as api:
        headers = {'X-AURA-User': 'alice', 'X-AURA-Chat-ID': chat_a}
        assert api.get('/api/usage/tokens?scope=chat', headers=headers).json()['total_tokens'] == 30
        assert api.get('/api/usage/tokens?scope=backend', headers=headers).json()['total_tokens'] == 45
        assert api.get('/api/usage/tokens?scope=all', headers=headers).json()['total_tokens'] == 60
        exported = api.get('/api/usage/tokens/export?scope=chat', headers=headers)
        assert len(list(csv.DictReader(io.StringIO(exported.text)))) == 2
        headers['X-AURA-Chat-ID'] = chat_b
        assert api.get('/api/usage/tokens?scope=chat', headers=headers).json()['total_tokens'] == 15
        headers['X-AURA-User'] = 'bob'
        assert api.get('/api/usage/tokens?scope=chat', headers=headers).json()['calls'] == 0
        assert api.get('/api/usage/tokens?scope=backend', headers=headers).json()['calls'] == 0
        assert api.get('/api/usage/tokens?scope=chat').status_code == 400
        assert api.get('/api/usage/tokens/export?scope=chat').status_code == 400
        assert api.get('/api/usage/tokens?scope=chat', headers={'X-AURA-Chat-ID': 'bad'}).status_code == 400
        assert api.get('/api/usage/tokens?scope=invalid').status_code == 422


def test_token_ledger_migrates_existing_records_without_attributing_them(monkeypatch, tmp_path):
    import sqlite3
    path = tmp_path / 'tokens.sqlite3'
    monkeypatch.setenv('TOKEN_USAGE_DB', str(path))
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE token_usage (
            call_id TEXT PRIMARY KEY, recorded_at TEXT, owner_id TEXT, operation TEXT,
            model TEXT, outcome TEXT, usage_known INTEGER, input_tokens INTEGER,
            output_tokens INTEGER, total_tokens INTEGER, cached_tokens INTEGER,
            reasoning_tokens INTEGER, latency_ms REAL, request_id TEXT, trace_id TEXT,
            agent_run_id TEXT, response_id TEXT)''')
        db.execute('INSERT INTO token_usage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   ('legacy', '2026', 'demo-user', 'test', 'test', 'succeeded', 1, 10, 5, 15, None, None, 1, None, None, None, None))
    usage.record_token_usage(operation='test', model='test', response=response(), outcome='succeeded', latency_ms=1)
    assert usage.usage_summary()['total_tokens'] == 30
    assert usage.usage_summary('backend')['total_tokens'] == 15
    rows = list(csv.DictReader(io.StringIO(usage.usage_csv())))
    assert len(rows) == 2
    assert rows[0]['backend_session_id'] == ''
    with (tmp_path / 'daily-usage' / 'daily-usage.csv').open(encoding='utf-8-sig', newline='') as file:
        daily = next(csv.DictReader(file))
    assert daily['calls'] == '1'
    assert daily['total_tokens'] == '15'


def test_usage_is_stored_per_backend_session_and_aggregated_across_sessions(monkeypatch, tmp_path):
    from backend.logging_config import chat_session_id_context

    monkeypatch.delenv('TOKEN_USAGE_DB', raising=False)
    monkeypatch.setattr(usage, 'get_settings', lambda: SimpleNamespace(app_data_dir=tmp_path))
    chat_id = '44444444-4444-4444-8444-444444444444'
    token = chat_session_id_context.set(chat_id)
    try:
        for session in ('session-one', 'session-two'):
            monkeypatch.setattr(usage, 'SESSION_ID', session)
            usage.record_token_usage(operation='test', model='test', response=response(), outcome='succeeded', latency_ms=1)
            assert (tmp_path / 'sessions' / session / 'usage' / 'token-usage.sqlite3').is_file()
            with (tmp_path / 'sessions' / session / 'usage' / 'token-usage.csv').open(encoding='utf-8-sig', newline='') as file:
                rows = list(csv.DictReader(file))
            assert len(rows) == 1
            assert rows[0]['chat_session_id'] == chat_id
            assert rows[0]['backend_session_id'] == session
            assert rows[0]['total_tokens'] == '15'
        assert usage.usage_summary('backend')['total_tokens'] == 15
        assert usage.usage_summary('chat')['total_tokens'] == 30
        assert usage.usage_summary()['total_tokens'] == 30
        assert len(list(csv.DictReader(io.StringIO(usage.usage_csv('chat'))))) == 2
        with (tmp_path / 'daily-usage' / 'daily-usage.csv').open(encoding='utf-8-sig', newline='') as file:
            daily = next(csv.DictReader(file))
        assert daily['calls'] == '2'
        assert daily['input_tokens'] == '20'
        assert daily['output_tokens'] == '10'
        assert daily['total_tokens'] == '30'
    finally:
        chat_session_id_context.reset(token)


def test_token_csv_handles_unknown_usage_and_spreadsheet_formulas(monkeypatch, tmp_path):
    monkeypatch.setenv('TOKEN_USAGE_DB', str(tmp_path / 'tokens.sqlite3'))
    usage.record_token_usage(operation='=unsafe', model='test', response=response(known=False), outcome='failed', latency_ms=1)
    with (tmp_path / 'tokens.csv').open(encoding='utf-8-sig', newline='') as file:
        row = next(csv.DictReader(file))
    assert row['operation'] == "'=unsafe"
    assert row['usage_known'] == '0'
    assert row['total_tokens'] == ''
    with (tmp_path / 'daily-usage' / 'daily-usage.csv').open(encoding='utf-8-sig', newline='') as file:
        daily = next(csv.DictReader(file))
    assert daily['calls'] == '1'
    assert daily['calls_without_usage'] == '1'
    assert daily['total_tokens'] == '0'


def test_daily_usage_splits_on_local_midnight(monkeypatch, tmp_path):
    monkeypatch.setenv('TOKEN_USAGE_DB', str(tmp_path / 'tokens.sqlite3'))
    today = datetime.now().astimezone().date()
    before_midnight = datetime.combine(today, datetime.min.time()).astimezone() - timedelta(seconds=1)
    after_midnight = datetime.combine(today, datetime.min.time()).astimezone() + timedelta(seconds=1)
    instants = iter([before_midnight.astimezone(timezone.utc), after_midnight.astimezone(timezone.utc)])

    class Clock:
        @staticmethod
        def now(tz):
            assert tz == timezone.utc
            return next(instants)

    monkeypatch.setattr(usage, 'datetime', Clock)
    for _ in range(2):
        usage.record_token_usage(operation='test', model='test', response=response(), outcome='succeeded', latency_ms=1)
    with (tmp_path / 'daily-usage' / 'daily-usage.csv').open(encoding='utf-8-sig', newline='') as file:
        rows = list(csv.DictReader(file))
    assert [row['local_date'] for row in rows] == [(today - timedelta(days=1)).isoformat(), today.isoformat()]
    assert [row['total_tokens'] for row in rows] == ['15', '15']


def test_daily_usage_concurrent_writers_across_backend_sessions(monkeypatch, tmp_path):
    monkeypatch.delenv('TOKEN_USAGE_DB', raising=False)
    monkeypatch.setattr(usage, 'get_settings', lambda: SimpleNamespace(app_data_dir=tmp_path))
    local = threading.local()
    monkeypatch.setattr(usage, '_current_path', lambda: tmp_path / 'sessions' / local.session / 'usage' / 'token-usage.sqlite3')

    def write(index):
        local.session = f'session-{index % 4}'
        usage.record_token_usage(operation='test', model='test', response=response(), outcome='succeeded', latency_ms=1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(40)))
    with (tmp_path / 'daily-usage' / 'daily-usage.csv').open(encoding='utf-8-sig', newline='') as file:
        daily = next(csv.DictReader(file))
    assert daily['calls'] == '40'
    assert daily['total_tokens'] == '600'


def test_daily_insert_failure_rolls_back_session_record(monkeypatch, tmp_path):
    path = tmp_path / 'tokens.sqlite3'
    monkeypatch.setenv('TOKEN_USAGE_DB', str(path))
    daily = tmp_path / 'daily-usage' / 'daily-usage.sqlite3'
    usage._initialize_daily(daily)
    with sqlite3.connect(daily) as db:
        db.execute("""CREATE TRIGGER reject_daily BEFORE INSERT ON daily_usage
                      BEGIN SELECT RAISE(ABORT, 'daily ledger unavailable'); END""")
    with pytest.raises(sqlite3.IntegrityError, match='daily ledger unavailable'):
        usage.record_token_usage(operation='test', model='test', response=response(), outcome='succeeded', latency_ms=1)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM token_usage').fetchone()[0] == 0


def test_token_csv_failure_is_reported_and_ledger_remains(monkeypatch, tmp_path):
    monkeypatch.setenv('TOKEN_USAGE_DB', str(tmp_path / 'tokens.sqlite3'))
    def fail(*args):
        raise OSError('CSV is locked')
    with monkeypatch.context() as context:
        context.setattr(usage, '_write_csv', fail)
        with pytest.raises(OSError, match='CSV is locked'):
            usage.record_token_usage(operation='test', model='test', response=response(), outcome='succeeded', latency_ms=1)
    assert usage.usage_summary()['total_tokens'] == 15
    usage.record_token_usage(operation='test', model='test', response=response(), outcome='succeeded', latency_ms=1)
    with (tmp_path / 'daily-usage' / 'daily-usage.csv').open(encoding='utf-8-sig', newline='') as file:
        daily = next(csv.DictReader(file))
    assert daily['calls'] == '2'
    assert daily['total_tokens'] == '30'


def test_default_all_time_usage_includes_legacy_ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(usage, 'get_settings', lambda: SimpleNamespace(app_data_dir=tmp_path))
    legacy = tmp_path / 'usage' / 'token-usage.sqlite3'
    monkeypatch.setenv('TOKEN_USAGE_DB', str(legacy))
    usage.record_token_usage(operation='test', model='test', response=response(), outcome='succeeded', latency_ms=1)
    monkeypatch.delenv('TOKEN_USAGE_DB')
    usage.record_token_usage(operation='test', model='test', response=response(), outcome='succeeded', latency_ms=1)
    assert usage.usage_summary()['total_tokens'] == 30
    assert usage.usage_summary('backend')['total_tokens'] == 15
    assert len(list(csv.DictReader(io.StringIO(usage.usage_csv())))) == 2


def test_request_context_attributes_provider_calls_to_chat(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import app

    monkeypatch.setenv('TOKEN_USAGE_DB', str(tmp_path / 'tokens.sqlite3'))
    monkeypatch.setenv('AUTH_REQUIRED', 'false')
    chat_id = '33333333-3333-4333-8333-333333333333'

    @app.get('/api/test-token-context')
    async def record():
        await asyncio.to_thread(
            usage.record_token_usage, operation='test', model='test',
            response=response(), outcome='succeeded', latency_ms=1,
        )
        return {'ok': True}

    try:
        with TestClient(app) as api:
            headers = {'X-AURA-User': 'alice', 'X-AURA-Chat-ID': chat_id}
            assert api.get('/api/test-token-context', headers=headers).status_code == 200
            assert api.get('/api/usage/tokens?scope=chat', headers=headers).json()['total_tokens'] == 15
            assert api.get('/api/usage/tokens?scope=backend', headers=headers).json()['total_tokens'] == 15
    finally:
        app.router.routes[:] = [route for route in app.router.routes if getattr(route, 'endpoint', None) is not record]


def test_response_conversation_does_not_overwrite_message(monkeypatch):
    async def reply(*args):
        return {'message': 'Ready', 'evidenceKeys': ['message']}
    monkeypatch.setattr(graph_workflow, '_json', reply)
    result = asyncio.run(graph_workflow._response({'action': 'respond', 'goal': 'Hello',
        'result': {'message': 'Hello'}, 'context': {'conversation': [{'role': 'user', 'text': 'Hello'}]}}))
    assert result['result']['assistantMessage'] == 'Ready'


def test_broad_request_chains_understanding_kpis_and_dashboard(monkeypatch):
    from pathlib import Path
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.agents import tools, orchestrator
    from automation.ai.ai_service import MockProvider
    monkeypatch.setenv('USE_AI', 'true')
    monkeypatch.setenv('AI_PROVIDER', 'mock')
    api = TestClient(app)
    sample = Path(__file__).resolve().parents[2] / 'docs' / 'DemoSales.xlsx'
    uploaded = api.post('/api/datasource/excel', files={'file': ('sales.xlsx', sample.read_bytes())})
    assert uploaded.status_code == 200
    captured = []
    class Provider(MockProvider):
        async def route_agent(self, request, context):
            return {'steps': [{'action': action, 'reason': action} for action in
                             ['understand_workbook', 'suggest_kpis', 'plan_dashboard']], 'requires_approval': False}
        async def plan_dashboard(self, request, profile, understanding, metadata, count, repair_context=None, **kwargs):
            captured.append(repair_context)
            name, table = next((n,t) for n,t in metadata['tables'].items() if t.get('measures'))
            return {'status': 'ready', 'page': 'Dataset overview', 'visuals': [
                {'type': 'card', 'value': {'table': name, 'field': table['measures'][0], 'fieldType': 'measure'}}]}
    monkeypatch.setattr(tools, 'get_ai_provider', lambda _: Provider())
    monkeypatch.setattr(orchestrator, 'get_ai_provider', lambda _: Provider())
    result = api.post('/api/agent/runs', json={'sourceId': uploaded.json()['sourceId'],
        'request': 'Understand this dataset, suggest relevant KPIs, and create an overview dashboard'}).json()
    assert result['status'] == 'completed', result.get('help')
    assert [s['action'] for s in result['steps']] == ['understand_workbook', 'suggest_kpis', 'plan_dashboard']
    assert captured[0]['completed_step_outputs']['0']['tables']
    assert captured[0]['completed_step_outputs']['1']['suggestions']
    spec = result['results']['2']
    payload = {'sourceId': uploaded.json()['sourceId'], 'reportSpec': spec}
    assert api.post('/api/report/approve', json=payload).status_code == 200
    generated = api.post('/api/report/generate', json=payload)
    assert generated.status_code == 200, generated.text
    assert generated.json()['validation']['passed']


def test_answered_dashboard_question_is_repaired_before_asking_user(monkeypatch):
    from backend.agents import tools
    calls = []
    class Provider:
        model = 'test'
        async def plan_dashboard(self, *args, repair_context=None, **kwargs):
            calls.append(dict(repair_context))
            if len(calls) == 1:
                return {'status': 'needs_clarification', 'question': 'Which metric?', 'options': ['Revenue']}
            return {'status': 'ready', 'page': 'Overview', 'visuals': [{'type': 'card'}]}
    monkeypatch.setattr(tools, 'load_source_metadata', lambda _: {'tables': {}})
    monkeypatch.setattr(tools, 'load_source_payload', lambda _: {'workbookProfile': {'ok': True}, 'aiUnderstanding': {'ok': True}})
    monkeypatch.setattr(tools, 'profile_for_ai', lambda p: p)
    monkeypatch.setattr(tools, 'get_settings', lambda: SimpleNamespace(ai_provider='azure'))
    monkeypatch.setattr(tools, 'get_ai_provider', lambda _: Provider())
    monkeypatch.setattr(tools, 'normalize_ai_measure_bindings', lambda *a: None)
    monkeypatch.setattr(tools, 'validate_ai_plan', lambda *a: None)
    monkeypatch.setattr(tools, 'check_visual_request', lambda *a: None)
    result = asyncio.run(tools._plan_one_page('source', 'Create overview', {
        'clarificationHistory': [{'question': 'Which metric?', 'answer': 'Revenue'}]}))
    assert result['page'] == 'Overview'
    assert len(calls) == 2
    assert calls[1]['clarification_answer'] == 'Revenue'
    assert 'answered question' in calls[1]['validation_error']
