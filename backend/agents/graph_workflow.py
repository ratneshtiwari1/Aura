from __future__ import annotations

import json
import asyncio
from decimal import Decimal
import logging
import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from automation.ai.ai_service import AIServiceError, get_ai_provider
from backend.config import get_settings
from backend.agents.prompts import CLARIFICATION_PROMPT, RESPONSE_PROMPT
from backend.services.ai_analysis_service import AnalysisClarificationRequired

logger = logging.getLogger('autobi.graph')


class AgentState(TypedDict, total=False):
    action: str
    source_id: str
    goal: str
    context: dict[str, Any]
    result: dict[str, Any]
    question: str
    options: list[str]
    agent: str


async def _json(operation: str, prompt: str, payload: dict) -> dict:
    provider = get_ai_provider(get_settings())
    if not hasattr(provider, "client"):
        # Mock mode is a deterministic local integration mode, so graph
        # narration must not require the Azure transport client.
        if operation == "clarification_agent":
            return {"question": str(payload.get("uncertainty", "What should I do next?")),
                    "options": payload.get("options", [])}
        evidence = payload.get("verifiedResult", {})
        if isinstance(evidence, dict):
            message = evidence.get("answer") or evidence.get("message") or "The report draft is ready for review."
            return {"message": str(message), "evidenceKeys": list(evidence)[:1]}
        return {"message": "The report draft is ready for review.", "evidenceKeys": []}
    try:
        return await asyncio.wait_for(provider.client.generate_json(operation, prompt, payload), timeout=get_settings().ai_timeout_seconds)
    except asyncio.TimeoutError as exc:
        raise AIServiceError("Azure agent timed out. Please retry.") from exc


async def _specialist(state: AgentState) -> dict:
    from backend.agents.tools import evaluate_tool_result
    from backend.agents.langgraph_tools import TOOLS
    try:
        result = await TOOLS[state['action']].ainvoke({'source_id':state['source_id'], 'goal':state['goal'], 'context':state['context']})
        passed, error = evaluate_tool_result(state['action'], result)
        if not passed:
            raise ValueError(error)
        if state['action'] == 'clarify':
            return {'question': result['question'], 'options': [], 'agent': 'clarification'}
        return {'result': result}
    except AnalysisClarificationRequired as exc:
        return {'question': exc.question, 'options': exc.options}


async def _clarification(state: AgentState) -> dict:
    question = state['question']
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        raise AIServiceError('Specialist returned an invalid clarification question.')
    return {'question': question.strip(), 'options': state.get('options', []), 'agent': 'clarification'}


def _numbers(text: str) -> set[str]:
    return {str(Decimal(n.replace(',', '').rstrip('%')).normalize()) for n in re.findall(r'-?\d[\d,]*(?:\.\d+)?(?:[eE][+-]?\d+)?%?', text)}


def _presentation_hints(result: dict[str, Any]) -> list[str]:
    """Provide safe display forms for fractional rates returned by analytical SQL."""
    hints: list[str] = []
    findings = result.get('findings', [])
    if not isinstance(findings, list):
        return hints
    for row in findings:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            if not re.search(r'(?:growth|rate|margin|percent|percentage|%)', str(key), re.I):
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool) and -1 <= value <= 1:
                hints.append(f"{key}: {value * 100:.2f}%")
    return hints


async def _response(state: AgentState) -> dict:
    result = state['result']
    evidence = {k:v for k,v in result.items() if k not in {'rawRows', 'sampleRows'}}
    presentation_hints = _presentation_hints(result)
    if presentation_hints:
        evidence['presentationHints'] = presentation_hints
    spec = result.get('reportSpec', result)
    if isinstance(spec, dict) and isinstance(spec.get('visuals'), list):
        pages = [spec, *spec.get('additionalPages', [])]
        evidence['reportSummary'] = {'pageCount':len(pages),
            'visualCount':sum(len(p.get('visuals', [])) for p in pages)}
    if state['action'] in {'plan_dashboard', 'edit_report'}:
        summary = evidence.get('reportSummary', {})
        message = (f"Report draft ready for review: {summary['pageCount']} page(s), "
                   f"{summary['visualCount']} visual(s).") if summary else "Report changes are ready for review."
        return {'result': {**result, 'assistantMessage': message,
                           'responseEvidenceKeys': list(evidence), 'agentWorkflow': 'langgraph',
                           'specialist': state.get('agent', '')}}
    if state['action'] == 'understand_workbook' and re.search(
        r'\btables?\b', state['goal'], re.I
    ) and re.search(r'\bmeasures?\b', state['goal'], re.I):
        tables = result.get('tables', [])
        if not isinstance(tables, list):
            raise AIServiceError('Workbook understanding returned an invalid table inventory.')
        lines = []
        for table in tables:
            if not isinstance(table, dict) or not isinstance(table.get('table'), str) or not isinstance(table.get('measures'), list):
                raise AIServiceError('Workbook understanding returned an invalid table inventory.')
            measures = table['measures']
            if any(not isinstance(measure, str) for measure in measures):
                raise AIServiceError('Workbook understanding returned an invalid measure inventory.')
            lines.append(f"{table['table']}: {', '.join(measures) if measures else 'No measures'}")
        message = 'Available tables and measures:\n' + '\n'.join(lines) if lines else 'No tables are available in this workbook.'
        return {'result': {**result, 'assistantMessage': message, 'responseEvidenceKeys': ['tables'],
                           'agentWorkflow': 'langgraph', 'specialist': state.get('agent', '')}}
    is_data_answer = state['action'] in {'answer_data_question', 'answer_schema', 'investigate_data'}
    # Analysis execution remains deterministic, but the response agent turns
    # the validated plan and findings into the user-facing explanation.
    # Numeric values are checked against the evidence below before returning.
    raw = await _json('response_agent', RESPONSE_PROMPT,
        {'request': state['goal'], 'action': state['action'], 'verifiedResult': evidence,
         'conversation': state.get('context', {}).get('conversation', [])[-10:]})
    message, keys = raw.get('message'), raw.get('evidenceKeys')
    if not isinstance(message, str) or not message.strip() or len(message) > 6000:
        raise AIServiceError('Response agent returned an invalid answer. Please retry.')
    if is_data_answer:
        if not isinstance(keys, list):
            keys = []
        keys = [key for key in keys if isinstance(key, str) and key in evidence]
        if not keys:
            keys = list(evidence)
    else:
        if not isinstance(keys, list):
            keys = []
        keys = [key for key in keys if isinstance(key, str) and key in evidence]
        if not keys:
            keys = list(evidence)
    allowed = _numbers(json.dumps(evidence, default=str))
    if not is_data_answer:
        allowed |= _numbers(state['goal'])
        context = state.get('context') or {}
        allowed |= _numbers(str(context.get('clarificationAnswer', '')))
        history = context.get('clarificationHistory', [])
        for item in history if isinstance(history, list) else []:
            if isinstance(item, dict):
                allowed |= _numbers(str(item.get('answer', '')))
        conversation = context.get('conversation', [])
        for turn in conversation if isinstance(conversation, list) else []:
            if isinstance(turn, dict) and turn.get('role') == 'user':
                allowed |= _numbers(str(turn.get('text', '')))
    if not _numbers(message) <= allowed:
        raise AIServiceError('Response agent introduced unsupported numbers. Please retry.')
    coverage=result.get('coverage',{})
    if coverage.get('truncated'):
        message += f" Showing only the first {coverage['rowLimit']} result rows; additional result rows are not displayed."
    updated = {**result, 'responseEvidence' :evidence, 'assistantMessage':message.strip(), 'responseEvidenceKeys':keys,
               'agentWorkflow': 'langgraph', 'specialist':state.get('agent','')}
    if state['action'] in {'answer_data_question','answer_schema','investigate_data'}:
        updated['answer'] = message.strip()
    if state['action'] == 'respond':
        updated['message'] = message.strip()
    return {'result':updated}


def _dispatch(state: AgentState) -> str:
    action=state['action']
    if action in {'plan_dashboard','edit_report'}: return 'report_visual_agent'
    if action == 'clarify': return 'clarification_request'
    return 'data_analysis_agent' if action != 'respond' else 'conversation'


def build_graph():
    graph=StateGraph(AgentState)
    for name in ['data_analysis_agent','report_visual_agent','conversation','clarification_request']:
        async def node(state, name=name):
            logger.info('Specialist started',extra={'event':'graph.node.started','operation':name})
            return {**await _specialist(state), 'agent':name}
        graph.add_node(name,node)
        graph.add_conditional_edges(name,lambda s:'clarification_agent' if s.get('question') else 'response_agent', ['clarification_agent','response_agent'])
    graph.add_node('clarification_agent',_clarification)
    graph.add_node('response_agent',_response)
    graph.add_conditional_edges(START,_dispatch, ['data_analysis_agent','report_visual_agent','conversation','clarification_request'])
    graph.add_edge('clarification_agent',END)
    graph.add_edge('response_agent',END)
    return graph.compile()


GRAPH = build_graph()


async def execute_agent_action(action: str, source_id: str, goal: str, context: dict) -> dict:
    state=await GRAPH.ainvoke({'action':action,'source_id':source_id,'goal':goal,'context':context},
                             {'recursion_limit':12})
    if state.get('question'):
        provider=get_ai_provider(get_settings())
        raise AnalysisClarificationRequired(state['question'],state.get('options',[]),
                                           provider=get_settings().ai_provider,model=provider.model)
    return state['result']
