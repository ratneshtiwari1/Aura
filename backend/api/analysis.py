from fastapi import APIRouter, HTTPException

from automation.ai.ai_service import AIServiceError
from backend.services.ai_audit_service import write_ai_audit_event
from backend.services.ai_analysis_service import AnalysisClarificationRequired, analyze_data_question
from backend.services.analysis_service import understand, kpi_suggestions
from backend.services.model_service import load_source_metadata, load_source_payload
from backend.policy import MIN_RELATIONSHIP_CONFIDENCE

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def _source(source_id: object):
    if not isinstance(source_id, str):
        raise HTTPException(status_code=400, detail="sourceId is required")
    try:
        return load_source_metadata(source_id), load_source_payload(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/understand")
async def understand_source(payload: dict) -> dict:
    metadata, source = _source(payload.get("sourceId"))
    result = understand(metadata, source.get("aiUnderstanding"))
    write_ai_audit_event(source_id=payload["sourceId"], operation="business_understanding", provider="rules", model="grounded", outcome="succeeded", validation_result="review_required", decisions=result)
    return result


@router.post("/kpis")
async def suggest_kpis(payload: dict) -> dict:
    metadata, _ = _source(payload.get("sourceId"))
    return {"suggestions": kpi_suggestions(metadata), "requiresConfirmation": True}


@router.post("/ask")
async def ask_data(payload: dict) -> dict:
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        raise HTTPException(status_code=400, detail="question is required")
    source_id = payload.get("sourceId")
    metadata, source = _source(source_id)
    selected_table = payload.get("table")
    if selected_table is not None and (
        not isinstance(selected_table, str) or selected_table not in metadata.get("tables", {})
    ):
        raise HTTPException(status_code=400, detail="Selected table was not found in this source")
    try:
        from backend.agents.graph_workflow import execute_agent_action
        result = await execute_agent_action("answer_data_question", source_id, question, {
            "selectedTable":selected_table,"clarificationAnswer":payload.get("clarificationAnswer"),
            "conversation":payload.get("conversation",[])})
    except AnalysisClarificationRequired as exc:
        result = exc.response(question)
    except AIServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    plan = result.get("analysisPlan", {})
    write_ai_audit_event(
        source_id=source_id,
        operation="guided_investigation",
        provider=plan.get("provider", "unknown"),
        model=plan.get("model", "unknown"),
        outcome="clarification" if plan.get("status") == "needs_clarification" else "succeeded",
        validation_result="passed",
        decisions={
            "question": question,
            "analysis_plan": plan,
            "source_fields": result["sourceFields"],
            "findings": result["findings"],
        },
    )
    return result


@router.post("/relationships")
async def inspect_relationships(payload: dict) -> dict:
    metadata, _ = _source(payload.get("sourceId"))
    relationships = [
        item
        for item in metadata.get("relationships", [])
        if item.get("confidence", 0) >= MIN_RELATIONSHIP_CONFIDENCE
    ]
    return {"relationships": relationships, "message": f"Found {len(relationships)} high-confidence inferred relationships. Review before changing the model."}


@router.post("/preparation")
async def preparation_suggestions(payload: dict) -> dict:
    metadata, source = _source(payload.get("sourceId"))
    review = metadata.get("parsingReview", {})
    warnings = source.get("workbookProfile", {}).get("warnings", [])
    return {"requiresConfirmation": True, "parsingReview": review, "warnings": warnings,
            "message": "These are proposals only; no workbook transformations have been applied."}
