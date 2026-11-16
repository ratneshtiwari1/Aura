from fastapi import APIRouter, HTTPException
import re

from automation.ai.ai_service import AIServiceError
from backend.agents.orchestrator import AgentOrchestrator
from backend.tools import public_tool_contracts
from backend.policy import MAX_CONVERSATION_MESSAGES, MAX_MESSAGE_CHARACTERS
from backend.services.model_service import load_source_metadata
from backend.services.run_service import (
    RunBusyError, approve_run, build_audit_record, create_run, execute_run,
    link_report, list_user_runs, load_run, record, record_trace_id, request_cancellation,
)
from backend.services.identity_service import current_user_id

router = APIRouter(prefix="/api/agent", tags=["agent"])
orchestrator = AgentOrchestrator()
TRACE_ID_PATTERN = re.compile(r"[a-fA-F0-9]{32}\Z")


def _trace_id(payload: dict) -> str | None:
    trace_id = payload.get("traceId")
    if trace_id is None:
        return None
    if not isinstance(trace_id, str) or not TRACE_ID_PATTERN.fullmatch(trace_id):
        raise HTTPException(status_code=400, detail="traceId must contain 32 hexadecimal characters")
    return trace_id.lower()


def _execution_context(payload: dict) -> dict:
    conversation = payload.get("conversation", [])
    if not isinstance(conversation, list) or len(conversation) > MAX_CONVERSATION_MESSAGES or any(
        not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}
        or not isinstance(item.get("text"), str) or len(item["text"]) > MAX_MESSAGE_CHARACTERS
        for item in conversation
    ):
        raise HTTPException(status_code=400, detail="conversation is invalid")
    context = {"conversation": conversation}
    if isinstance(payload.get("reportSpec"), dict):
        context["reportSpec"] = payload["reportSpec"]
    if isinstance(payload.get("plannerMessage"), str):
        context["plannerMessage"] = payload["plannerMessage"][:MAX_MESSAGE_CHARACTERS]
    if isinstance(payload.get("table"), str):
        context["selectedTable"] = payload["table"]
    if isinstance(payload.get("clarificationAnswer"), str) and payload["clarificationAnswer"].strip():
        context["clarificationAnswer"] = payload["clarificationAnswer"].strip()
    return context


def _contextual_goal(request: str, conversation: list[dict[str, str]], clarification: object) -> str:
    """Return a parser-safe single-turn goal for compatibility callers.

    Run execution deliberately stores `request` and `clarification` separately;
    this helper remains useful where a one-shot caller needs a display value.
    """
    parts = [request]
    if isinstance(clarification, str) and clarification.strip():
        parts.append(f"Clarification answer: {clarification.strip()}")
    return "\n".join(parts)


@router.post("/route")
async def route_agent(payload: dict) -> dict:
    source_id, request = payload.get("sourceId"), payload.get("request")
    if not isinstance(source_id, str) or not isinstance(request, str) or not request.strip():
        raise HTTPException(status_code=400, detail="sourceId and request are required")
    context = _execution_context(payload)
    try:
        metadata = load_source_metadata(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return await orchestrator.decide(
            source_id,
            request,
            bool(payload.get("hasReport")),
            payload.get("table"),
            metadata,
            context["conversation"],
            payload.get("clarificationAnswer"),
        )
    except AIServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"AI action plan was rejected: {exc}") from exc


@router.get("/tools")
async def list_agent_tools() -> dict:
    return {"tools": public_tool_contracts()}


@router.post("/runs")
async def start_agent_run(payload: dict) -> dict:
    """Persist a reviewed, allow-listed plan so it can resume after a restart."""
    source_id, request = payload.get("sourceId"), payload.get("request")
    if not isinstance(source_id, str) or not isinstance(request, str) or not request.strip():
        raise HTTPException(status_code=400, detail="sourceId and request are required")
    trace_id = _trace_id(payload)
    try:
        metadata = load_source_metadata(source_id)
        context = _execution_context(payload)
        selected_table = payload.get("table")
        from backend.services.conversation_memory import read as read_memory
        memory = read_memory(source_id)
        # Server history is authoritative; append only newer supplied turns.
        stored = memory["conversation"]
        supplied = context["conversation"]
        if stored:
            known = {(m["role"],m["text"]) for m in stored}
            context["conversation"] = (stored + [m for m in supplied if (m["role"],m["text"]) not in known])[-MAX_CONVERSATION_MESSAGES:]
        if memory.get("verifiedContext"):
            import json
            context["conversation"] = (context["conversation"] + [{"role":"assistant", "text":"Verified prior analysis context: " + json.dumps(memory["verifiedContext"],default=str)[:3500]}])[-MAX_CONVERSATION_MESSAGES:]
        if not context.get("reportSpec") and memory.get("reportSpec"):
            context["reportSpec"] = memory["reportSpec"]
        if payload.get("previousRunId"):
            previous = load_run(payload["previousRunId"])
            if previous["sourceId"] == source_id and not context["conversation"]:
                prior_messages = [{"role": "user", "text": previous["goal"]}]
                prior_answers = [
                    step.get("result", {}).get("answer")
                    for step in previous.get("steps", [])
                    if step.get("action") == "answer_data_question"
                    and isinstance(step.get("result"), dict)
                    and isinstance(step["result"].get("answer"), str)
                ]
                if prior_answers:
                    prior_messages.append({"role": "assistant", "text": prior_answers[-1][:MAX_MESSAGE_CHARACTERS]})
                context["conversation"] = prior_messages[-(MAX_CONVERSATION_MESSAGES - 1):]
        try:
            decision = await orchestrator.decide(
                source_id, request, bool(context.get("reportSpec") or payload.get("hasReport")), selected_table, metadata,
                context["conversation"], payload.get("clarificationAnswer"),
            )
        except AIServiceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=f"AI action plan was rejected: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if decision.get("message"):
        context["plannerMessage"] = decision["message"]
    # Keep the request immutable for the lifetime of a run. Clarification is
    # structured execution context, not an instruction to replace or extend
    # the report request passed to the deterministic planner.
    run = create_run(
        source_id, request.strip(),
        decision["plannedSteps"], retry_limit=payload.get("retryLimit", 2),
        requires_approval=bool(decision["requiresApproval"]), planner=str(decision.get("planner", "rules")),
        fallback_used=bool(decision.get("fallbackUsed")), context=context,
        source_type=payload.get("sourceType"),
        selected_table=selected_table,
        report_name=(payload.get("reportSpec") or {}).get("page") if isinstance(payload.get("reportSpec"), dict) else None,
    )
    run["useConversationMemory"] = True
    record_trace_id(run, trace_id)
    record(run, "planned", planner=decision.get("planner"), stepCount=len(run["steps"]))
    return await execute_run(run, context) if run["status"] != "awaiting_approval" else run


@router.get("/runs")
async def list_agent_runs(limit: int = 50, offset: int = 0) -> dict:
    bounded_limit = max(1, min(limit, 100))
    bounded_offset = max(0, offset)
    runs = list_user_runs(bounded_limit + 1, bounded_offset)
    return {"runs": runs[:bounded_limit], "hasMore": len(runs) > bounded_limit}


@router.post("/runs/{run_id}/approve")
async def approve_agent_run(run_id: str, payload: dict) -> dict:
    plan_version = payload.get("planVersion")
    if not isinstance(plan_version, str):
        raise HTTPException(status_code=400, detail="planVersion is required")
    trace_id = _trace_id(payload)
    try:
        run = load_run(run_id)
        record_trace_id(run, trace_id)
        return approve_run(run, plan_version, current_user_id())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/resume")
async def resume_agent_run(run_id: str, payload: dict) -> dict:
    """Advance every non-mutating step; explicitly stop before approval-required work."""
    context = _execution_context(payload)
    trace_id = _trace_id(payload)
    try:
        run = load_run(run_id)
        record_trace_id(run, trace_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return await execute_run(run, context)
    except RunBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/cancel")
async def cancel_agent_run(run_id: str) -> dict:
    try:
        return request_cancellation(load_run(run_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs/{run_id}")
async def get_agent_run(run_id: str) -> dict:
    try:
        return load_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs/{run_id}/audit")
async def get_agent_run_audit(run_id: str) -> dict:
    try:
        return build_audit_record(load_run(run_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/runs/{run_id}/report")
async def link_agent_run_report(run_id: str, payload: dict) -> dict:
    report_id = payload.get("reportId")
    if not isinstance(report_id, str) or not report_id.strip():
        raise HTTPException(status_code=400, detail="reportId is required")
    trace_id = _trace_id(payload)
    try:
        run = load_run(run_id)
        record_trace_id(run, trace_id)
        return link_report(run, report_id.strip(), payload.get("reportName"))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/memory/{source_id}")
async def conversation_history(source_id: str) -> dict:
    try:
        metadata = load_source_metadata(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404,detail="Source not found") from exc
    from backend.services.conversation_memory import read
    result = read(source_id)
    result["tables"] = [{"name":n,"columns":[{"name":c if isinstance(c,str) else c["name"]} for c in t.get("columns",[])]}
                        for n,t in metadata.get("tables",{}).items()]
    result["formulaCellCount"] = metadata.get("formulaCellCount")
    return result


@router.delete("/memory/{source_id}")
async def clear_conversation_history(source_id: str) -> dict:
    try:
        load_source_metadata(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404,detail="Source not found") from exc
    from backend.services.conversation_memory import clear
    clear(source_id)
    return {"cleared":True}


@router.get("/capabilities")
async def capabilities() -> dict:
    from automation.visual_registry import visual_capabilities
    return {"product": "AURA", "name": "AI-driven Understanding & Reporting Accelerator",
            "workflow": {"broadOverview": ["understand_workbook", "suggest_kpis", "plan_dashboard"],
                         "clarificationRoundLimit": 3, "tokenUsage": "/api/usage/tokens"},
            "analysis":["availability","aggregate","ranking","trend","count_distinct","distinct_values","schema","quality","analytical_query"],
            "analyticalQuery":{"engine":"DuckDB","readOnly":True,"resultRowLimit":200,"sourceRowLimit":500000,
                               "features":["explicit joins","window functions","period comparisons","descriptive statistics","correlation","percentiles"]},
            "memory":{"scope":"owner and source","retainedMessages":200,"contextMessages":30},
            "reports":visual_capabilities()}
