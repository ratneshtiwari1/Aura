from __future__ import annotations

import json
import threading
import logging
import os
from contextvars import ContextVar
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from backend.config import get_session_data_dir
from backend.logging_config import agent_run_id_context, request_id_context, trace_id_context

AUDIT_FILE_PATH = Path(os.getenv("AUDIT_FILE_PATH") or (
    get_session_data_dir() / "audit" / "ai-audit.jsonl"
))
_WRITE_LOCK = threading.Lock()
ai_metrics_context: ContextVar[dict[str, Any] | None] = ContextVar("ai_metrics", default=None)
logger = logging.getLogger("autobi.audit")


def write_report_audit_document(
    *,
    report_id: str,
    source_id: str,
    report_spec: dict[str, Any],
    validation: dict[str, Any],
    duration_ms: float,
    user_request: str | None = None,
    conversation: list[dict[str, str]] | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> Path:
    """Write one self-contained, human-readable audit record per report."""
    audit_directory = AUDIT_FILE_PATH.parent / "reports"
    audit_directory.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": 2,
        "report_id": report_id,
        "source_id": source_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "decision_origin": "AI dashboard plan reviewed and submitted for report generation",
        "user_request": user_request,
        "conversation": conversation or [],
        "decision_debug": {
            "final_report_spec": report_spec,
            "source_schema": {
                "tables": {
                    name: {"columns": table.get("columns", []), "measures": table.get("measures", []), "column_types": table.get("columnTypes", {})}
                    for name, table in (source_metadata or {}).get("tables", {}).items()
                },
                "relationships": (source_metadata or {}).get("relationships", []),
            },
            "limitations": "Contains validated fields and decisions only; excludes raw workbook rows, credentials, prompts, and hidden model reasoning.",
        },
        "dashboard_decisions": dashboard_decisions(report_spec),
        "generation": {
            "provider": "generator",
            "model": "pbir",
            "duration_ms": duration_ms,
            "validation": validation,
        },
        "event_log": "../ai-audit.jsonl",
    }
    target = audit_directory / f"{report_id}.json"
    with NamedTemporaryFile("w", encoding="utf-8", dir=audit_directory, delete=False) as file:
        json.dump(document, file, ensure_ascii=True, indent=2)
        file.write("\n")
        temporary_path = Path(file.name)
    os.replace(temporary_path, target)
    return target


def write_rejected_request_audit_document(
    *,
    source_id: str,
    user_request: str,
    reason: str,
    source_metadata: dict[str, Any],
    operation: str = "dashboard_planning",
) -> Path:
    """Persist rejected requests where no report ID exists yet."""
    audit_directory = AUDIT_FILE_PATH.parent / "reports"
    audit_directory.mkdir(parents=True, exist_ok=True)
    audit_id = f"rejected-{uuid4().hex[:8]}"
    document = {
        "schema_version": 2,
        "audit_id": audit_id,
        "status": "rejected",
        "source_id": source_id,
        "operation": operation,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "user_request": user_request,
        "rejection": {"reason": reason},
        "decision_debug": {
            "source_schema": {
                "tables": {
                    name: {"columns": table.get("columns", []), "measures": table.get("measures", []), "column_types": table.get("columnTypes", {})}
                    for name, table in source_metadata.get("tables", {}).items()
                },
                "relationships": source_metadata.get("relationships", []),
            },
            "limitations": "Excludes raw workbook rows, credentials, provider prompts, and hidden model reasoning.",
        },
        "event_log": "../ai-audit.jsonl",
    }
    target = audit_directory / f"{audit_id}.json"
    with NamedTemporaryFile("w", encoding="utf-8", dir=audit_directory, delete=False) as file:
        json.dump(document, file, ensure_ascii=True, indent=2)
        file.write("\n")
        temporary_path = Path(file.name)
    os.replace(temporary_path, target)
    return target


def workbook_decisions(understanding: dict[str, Any]) -> dict[str, Any]:
    return {
        "tables": [
            {
                "name": table["name"],
                "columns": [
                    {
                        "name": column["name"],
                        "role": column["semantic_role"],
                        "explanation": column.get("description", ""),
                    }
                    for column in table["columns"]
                ],
            }
            for table in understanding["tables"]
        ],
    }


def dashboard_decisions(plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("additionalPages"):
        from automation.report_pages import report_pages
        pages = [dashboard_decisions(page) for page in report_pages(plan)]
        return {"status": "ready", "page": plan["page"], "pages": pages,
                "visuals": [visual for page in pages for visual in page["visuals"]]}
    if plan.get("status") == "needs_clarification":
        return {
            "status": "needs_clarification",
            "question": plan["question"],
            "options": plan.get("options", []),
        }

    visuals = []
    binding_roles = (
        "category", "categoryHierarchy", "value", "field", "rows", "values", "legend", "legendBreakdown"
    )
    for visual in plan["visuals"]:
        bindings = []
        for role in binding_roles:
            fields = visual.get(role)
            if isinstance(fields, dict):
                fields = [fields]
            if isinstance(fields, list):
                bindings.extend(
                    {"role": role, "table": field["table"], "field": field["field"]}
                    for field in fields
                    if isinstance(field, dict) and "table" in field and "field" in field
                )
        comparison = visual.get("comparison")
        if isinstance(comparison, dict) and isinstance(comparison.get("value"), dict):
            field = comparison["value"]
            if "table" in field and "field" in field:
                bindings.append({"role": "comparison", "table": field["table"], "field": field["field"]})
        decision = {"type": visual["type"], "bindings": bindings}
        for key in ("title", "comparison", "icon"):
            if key in visual:
                decision[key] = visual[key]
        visuals.append(decision)
    decisions = {"status": "ready", "page": plan["page"], "visuals": visuals}
    design = plan.get("design")
    if isinstance(design, dict):
        decisions["design"] = {
            key: design[key]
            for key in ("theme", "layout", "visual_style", "kpi_style", "slicer_style",
                        "auto_kpi_cards", "auto_time_slicers", "show_variance", "show_icons", "colors")
            if key in design
        }
    rationale = plan.get("rationale")
    if isinstance(rationale, str) and rationale:
        decisions["rationale"] = rationale
    return decisions


def failure_details(error: Exception) -> dict[str, Any]:
    details: dict[str, Any] = {"type": type(error).__name__}
    response = getattr(error.__cause__, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        details["upstream_status_code"] = status_code
    return details


def write_ai_audit_event(
    *,
    source_id: str,
    operation: str,
    provider: str,
    model: str,
    outcome: str,
    validation_result: str,
    decisions: dict[str, Any] | None = None,
    failure: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    event: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "schema_version": 2,
        "event_id": str(uuid4()),
        "request_id": request_id_context.get(),
        "trace_id": trace_id_context.get(),
        "agent_run_id": agent_run_id_context.get(),
        "source_id": source_id,
        "operation": operation,
        "provider": provider,
        "model": model,
        "outcome": outcome,
        "validation_result": validation_result,
    }
    if decisions is not None:
        event["decisions"] = decisions
    if failure is not None:
        event["failure"] = failure
    metrics = ai_metrics_context.get()
    if metrics and metrics.get("operation") == operation:
        event["metrics"] = metrics
    if context is not None:
        event["context"] = context

    serialized = json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n"
    AUDIT_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        with AUDIT_FILE_PATH.open("a", encoding="utf-8", newline="\n") as audit_file:
            audit_file.write(serialized)
            audit_file.flush()
    logger.info("Audit event recorded", extra={
        "event": "audit.recorded", "operation": operation,
        "source_id": source_id, "audit_event_id": event["event_id"],
        "success": outcome == "succeeded", "validation_result": validation_result,
    })
