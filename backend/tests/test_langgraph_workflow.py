import asyncio
from types import SimpleNamespace
import pytest
from automation.ai.ai_service import AIServiceError
from backend.agents import graph_workflow as workflow
from backend.agents import langgraph_tools
from backend.services.ai_analysis_service import AnalysisClarificationRequired


def run(coro):
    return asyncio.run(coro)


def setup(monkeypatch, tool_result, response):
    class Tool:
        async def ainvoke(self, payload):
            if isinstance(tool_result,Exception): raise tool_result
            return tool_result
    monkeypatch.setitem(langgraph_tools.TOOLS,'answer_data_question',Tool())
    async def fake_json(operation,prompt,payload): return response
    monkeypatch.setattr(workflow,'_json',fake_json)


def test_analysis_response_uses_natural_response_agent_wording(monkeypatch):
    setup(monkeypatch,{'answer':'Revenue is 25,000.00','findings':[{'value':25000}]},
          {'message':'For the North region, revenue is 25,000.00.','evidenceKeys':['answer','findings']})
    result=run(workflow.execute_agent_action('answer_data_question','source','Total revenue?',{}))
    assert result['answer']=='For the North region, revenue is 25,000.00.'
    assert result['assistantMessage']=='For the North region, revenue is 25,000.00.'
    assert result['responseEvidenceKeys']==['answer','findings']
    assert result['agentWorkflow']=='langgraph'
    assert result['findings']==[{'value':25000}]


def test_non_analysis_response_cannot_invent_numbers(monkeypatch):
    setup(monkeypatch,{'answer':'Revenue is 25','findings':[]},
          {'message':'Revenue is 999','evidenceKeys':['message']})
    with pytest.raises(AIServiceError,match='unsupported numbers'):
        run(workflow._response({
            'action':'respond',
            'goal':'revenue',
            'result':{'message':'Revenue is 25','findings':[]},
        }))


def test_analysis_response_rejects_unsupported_numbers(monkeypatch):
    setup(monkeypatch,{'answer':'Revenue: 25.00','findings':[{'value':25}]},
          {'message':'Revenue for North is 999.00.','evidenceKeys':['answer']})
    with pytest.raises(AIServiceError,match='unsupported numbers'):
        run(workflow.execute_agent_action('answer_data_question','s','revenue',{}))


def test_response_adds_a_safe_percentage_hint_for_fractional_growth():
    assert workflow._presentation_hints({
        'findings': [{'Region': 'North', 'Max QoQ Growth': 0.1334080629610178}],
    }) == ['Max QoQ Growth: 13.34%']


def test_workbook_table_measure_inventory_uses_verified_schema_without_narration(monkeypatch):
    async def unexpected(*args):
        raise AssertionError('Inventory should not be rewritten by the response agent')
    monkeypatch.setattr(workflow, '_json', unexpected)
    result = run(workflow._response({
        'action': 'understand_workbook',
        'goal': 'What tables and measures are available in this workbook',
        'result': {'tables': [
            {'table': 'Sales', 'fields': [], 'measures': ['Total Net Revenue', 'Average Discount %']},
            {'table': 'Calendar', 'fields': [], 'measures': []},
        ]},
    }))['result']
    assert result['assistantMessage'] == (
        'Available tables and measures:\nSales: Total Net Revenue, Average Discount %\nCalendar: No measures'
    )
    assert result['responseEvidenceKeys'] == ['tables']


def test_ambiguity_asks_clarification_without_an_answer(monkeypatch):
    from backend.services import model_service
    setup(monkeypatch,AnalysisClarificationRequired('Which revenue measure?', ['Net Revenue'], provider='azure', model='test'), {'question':'Invented question','options':['invented']})
    monkeypatch.setattr(model_service,'load_source_metadata',lambda _: {'tables':{}})
    monkeypatch.setattr(workflow,'get_settings',lambda:SimpleNamespace(ai_provider='azure_openai'))
    monkeypatch.setattr(workflow,'get_ai_provider',lambda _:SimpleNamespace(model='test'))
    with pytest.raises(AnalysisClarificationRequired) as error:
        run(workflow.execute_agent_action('answer_data_question','s','revenue',{}))
    assert error.value.options==['Net Revenue']
    assert error.value.question=='Which revenue measure?'


def test_outage_never_runs_rules_fallback(monkeypatch):
    setup(monkeypatch,AIServiceError('Azure unavailable'),{})
    with pytest.raises(AIServiceError,match='Azure unavailable'):
        run(workflow.execute_agent_action('answer_data_question','s','revenue',{}))


def test_response_uses_available_evidence_when_agent_keys_are_invalid(monkeypatch):
    setup(monkeypatch,{'answer':'No rows','findings':[]},{'message':'Done','evidenceKeys':['imaginary']})
    result = run(workflow._response({
        'action':'respond',
        'goal':'revenue',
        'result':{'message':'No rows','findings':[]},
    }))
    assert result['result']['assistantMessage'] == 'Done'
    assert result['result']['responseEvidenceKeys'] == ['message', 'findings']


def test_saved_run_resumes_only_waiting_analysis(monkeypatch,tmp_path):
    from backend.services import run_service,model_service
    settings=SimpleNamespace(app_data_dir=tmp_path,auth_required=False,api_tokens={},ai_provider='azure_openai')
    monkeypatch.setattr(run_service,'get_settings',lambda:settings)
    monkeypatch.setattr(workflow,'get_settings',lambda:settings)
    monkeypatch.setattr(workflow,'get_ai_provider',lambda _:SimpleNamespace(model='test'))
    monkeypatch.setattr(model_service,'load_source_metadata',lambda _: {'tables':{}})
    calls=[]
    class Tool:
        async def ainvoke(self,payload):
            calls.append(payload['context'].get('clarificationAnswer'))
            if not calls[-1]: raise AnalysisClarificationRequired('Which revenue?', provider='azure', model='test')
            return {'answer':'Revenue is 25','findings':[{'value':25}]}
    monkeypatch.setitem(langgraph_tools.TOOLS,'answer_data_question',Tool())
    async def fake_json(op,prompt,payload):
        if op=='clarification_agent':return {'question':'Which revenue?','options':[]}
        return {'message':'Revenue is 25','evidenceKeys':['answer']}
    monkeypatch.setattr(workflow,'_json',fake_json)
    saved=run_service.create_run('source','What is revenue?',[{'action':'answer_data_question','reason':'Analyze'}])
    paused=run(run_service.execute_run(saved,{}))
    assert paused['status']=='needs_user'
    restored=run_service.load_run(saved['runId'])
    done=run(run_service.execute_run(restored,{'clarificationAnswer':'Net Revenue'}))
    assert done['status']=='completed'
    assert calls==[None,'Net Revenue']
    assert done['steps'][0]['result']['answer']=='Revenue is 25'


def test_ai_edit_proposal_validates_before_response(monkeypatch):
    from backend.agents import tools
    spec={'page':'Sales','visuals':[{'type':'card','value':{'table':'Sales','field':'Revenue','fieldType':'measure'}}]}
    revised={'page':'Sales renamed','visuals':spec['visuals']}
    seen=[]
    class Client:
        async def generate_json(self,*args):
            return {'status':'ready','reportSpec':revised,'changes':['Renamed page']}
    monkeypatch.setattr(tools,'load_source_metadata',lambda _: {'tables':{}})
    monkeypatch.setattr(tools,'get_ai_provider',lambda _:SimpleNamespace(client=Client()))
    monkeypatch.setattr(tools,'validate_report_spec',lambda candidate,metadata:seen.append(candidate))
    result=run(tools._ai_edit_report('s','Rename page',{'reportSpec':spec}))
    assert seen==[revised]
    assert result['requiresConfirmation'] is True
    assert spec['page']=='Sales'


def test_ai_edit_uses_json_mode_and_preserves_requested_page(monkeypatch):
    from backend.agents import tools
    metadata = {'tables': {'Sales': {
        'columns': ['Customer Segment', 'Region'], 'measures': ['Total Net Revenue'],
    }}, 'relationships': []}
    spec = {'page': 'Sales', 'visuals': [{
        'type': 'card', 'value': {'table': 'Sales', 'field': 'Total Net Revenue', 'fieldType': 'measure'},
    }]}
    slicer = {'type': 'slicer', 'field': {'table': 'Sales', 'field': 'Customer Segment', 'fieldType': 'column'}}
    proposal = {'page': 'Sales', 'visuals': spec['visuals'], 'additionalPages': [
        {'page': 'Page 2', 'visuals': [slicer]},
    ]}
    class Client:
        async def generate_json(self, operation, prompt, payload):
            assert operation == 'report_edit_agent'
            assert 'json' in prompt.casefold()
            return {'status': 'ready', 'reportSpec': proposal, 'changes': ['Added page 2']}
    monkeypatch.setattr(tools, 'load_source_metadata', lambda _: metadata)
    monkeypatch.setattr(tools, 'get_ai_provider', lambda _: SimpleNamespace(client=Client()))
    context = {'reportSpec': spec}
    result = run(tools._ai_edit_report('s', 'Add a slicer for Customer Segment in second page', context))
    assert result['reportSpec'] == proposal
    assert spec == {'page': 'Sales', 'visuals': [spec['visuals'][0]]}

    proposal['additionalPages'][0]['visuals'] = [
        {'type': 'slicer', 'field': {'table': 'Sales', 'field': 'Region', 'fieldType': 'column'}},
    ]
    with pytest.raises(ValueError, match='requested slicer'):
        run(tools._ai_edit_report('s', 'Add a slicer for Customer Segment in second page', context))
    proposal['additionalPages'][0]['visuals'] = [slicer]
    proposal['visuals'] = []
    with pytest.raises(ValueError):
        run(tools._ai_edit_report('s', 'Add a slicer for Customer Segment in second page', context))


def test_mock_report_edit_creates_numbered_page_and_validates(monkeypatch):
    from backend.agents import tools
    metadata = {'tables': {'Sales': {
        'columns': ['Customer Segment'], 'measures': ['Total Net Revenue'],
    }}, 'relationships': []}
    spec = {'page': 'Sales', 'visuals': [{
        'type': 'card', 'value': {'table': 'Sales', 'field': 'Total Net Revenue', 'fieldType': 'measure'},
    }]}
    monkeypatch.setattr(tools, 'load_source_metadata', lambda _: metadata)
    monkeypatch.setattr(tools, 'get_ai_provider', lambda _: SimpleNamespace(model='mock'))
    result = run(tools._ai_edit_report('s', 'Add a slicer for Customer Segment in second page', {'reportSpec': spec}))
    assert result['reportSpec']['visuals'] == spec['visuals']
    assert result['reportSpec']['additionalPages'][0]['visuals'] == [{
        'type': 'slicer', 'field': {'table': 'Sales', 'field': 'Customer Segment', 'fieldType': 'column'},
    }]


def test_supervisor_clarification_replans_same_run(monkeypatch,tmp_path):
    from backend.services import run_service,model_service
    from backend.agents.orchestrator import AgentOrchestrator
    settings=SimpleNamespace(app_data_dir=tmp_path,auth_required=False,api_tokens={})
    monkeypatch.setattr(run_service,'get_settings',lambda:settings)
    monkeypatch.setattr(model_service,'load_source_metadata',lambda _: {'tables':{}})
    async def execute(action,source,goal,context):
        if action=='clarify':raise AnalysisClarificationRequired('Analyze or create?',[],provider='azure',model='test')
        return {'answer':'Revenue is 25','findings':[]}
    async def decide(*args,**kwargs):
        return {'plannedSteps':[{'action':'answer_data_question','reason':'Analyze clarified goal'}], 'requiresApproval':False}
    monkeypatch.setattr(run_service,'execute_tool',execute)
    monkeypatch.setattr(AgentOrchestrator,'decide',decide)
    saved=run_service.create_run('s','Help me',[{'action':'clarify','reason':'Missing intent'}])
    assert run(run_service.execute_run(saved,{}))['status']=='needs_user'
    restored=run_service.load_run(saved['runId'])
    done=run(run_service.execute_run(restored,{'clarificationAnswer':'Analyze revenue'}))
    assert done['status']=='completed'
    assert done['runId']==saved['runId']
    assert done['steps'][0]['action']=='answer_data_question'
