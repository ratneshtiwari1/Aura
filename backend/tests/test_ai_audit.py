import json

from backend.logging_config import agent_run_id_context, request_id_context, trace_id_context
from backend.services import ai_audit_service
from backend.services.ai_audit_service import (
    dashboard_decisions,
    failure_details,
    workbook_decisions,
    write_ai_audit_event,
)


def test_audit_file_appends_structured_ai_decisions_without_prompt_or_rows(monkeypatch, tmp_path) -> None:
    audit_path = tmp_path / "audit" / "ai-audit.jsonl"
    monkeypatch.setattr(ai_audit_service, "AUDIT_FILE_PATH", audit_path)
    context_token = request_id_context.set("request-123")
    trace_token = trace_id_context.set("trace-123")
    run_token = agent_run_id_context.set("run-123")
    try:
        write_ai_audit_event(
            source_id="src-test",
            operation="dashboard_planning",
            provider="gemini",
            model="gemini-2.5-flash",
            outcome="succeeded",
            validation_result="passed",
            decisions=dashboard_decisions({
                "page": "Sales",
                "rationale": "Revenue is numeric and region is a useful category.",
                "visuals": [{
                    "type": "lineChart",
                    "category": {"table": "Sales", "field": "Month", "fieldType": "column"},
                    "value": {"table": "Sales", "field": "Total Revenue", "fieldType": "measure"},
                }],
            }),
        )
    finally:
        agent_run_id_context.reset(run_token)
        trace_id_context.reset(trace_token)
        request_id_context.reset(context_token)

    event = json.loads(audit_path.read_text(encoding="utf-8"))
    assert event["request_id"] == "request-123"
    assert event["trace_id"] == "trace-123"
    assert event["agent_run_id"] == "run-123"
    assert event["decisions"] == {
        "status": "ready",
        "page": "Sales",
        "visuals": [{
            "type": "lineChart",
            "bindings": [
                {"role": "category", "table": "Sales", "field": "Month"},
                {"role": "value", "table": "Sales", "field": "Total Revenue"},
            ],
        }],
        "rationale": "Revenue is numeric and region is a useful category.",
    }
    assert "prompt" not in event
    assert "rows" not in event


def test_workbook_audit_preserves_column_role_and_explanation() -> None:
    assert workbook_decisions({
        "tables": [{
            "name": "Sales",
            "columns": [{
                "name": "Revenue",
                "semantic_role": "measure",
                "description": "A numeric value that can be aggregated.",
            }],
        }],
    }) == {
        "tables": [{
            "name": "Sales",
            "columns": [{
                "name": "Revenue",
                "role": "measure",
                "explanation": "A numeric value that can be aggregated.",
            }],
        }],
    }


def test_failure_audit_detail_includes_safe_upstream_status_code() -> None:
    class Response:
        status_code = 404

    class UpstreamError(Exception):
        response = Response()

    error = RuntimeError("AI operation failed")
    error.__cause__ = UpstreamError("Not found")

    assert failure_details(error) == {
        "type": "RuntimeError",
        "upstream_status_code": 404,
    }


def test_audit_correlates_provider_metrics_only_with_matching_operation(monkeypatch, tmp_path) -> None:
    audit_path = tmp_path / "events.jsonl"
    monkeypatch.setattr(ai_audit_service, "AUDIT_FILE_PATH", audit_path)
    token = ai_audit_service.ai_metrics_context.set({
        "operation": "dashboard_planning", "latency_ms": 25, "input_tokens": 100,
    })
    try:
        for operation in ("dashboard_planning", "report_generation"):
            write_ai_audit_event(
                source_id="src-test", operation=operation, provider="mock", model="mock",
                outcome="succeeded", validation_result="passed",
                context={"report_id": "report-test"},
            )
    finally:
        ai_audit_service.ai_metrics_context.reset(token)
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert events[0]["schema_version"] == 2
    assert events[0]["event_id"] != events[1]["event_id"]
    assert events[0]["metrics"]["input_tokens"] == 100
    assert "metrics" not in events[1]
    assert events[1]["context"]["report_id"] == "report-test"
