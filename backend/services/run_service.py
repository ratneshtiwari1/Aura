"""Durable, versioned execution state for bounded AURA agent runs."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from automation.ai.ai_service import AIServiceError
from backend.services.ai_analysis_service import AnalysisClarificationRequired
from backend.agents.tools import evaluate_tool_result
from backend.tools import TOOL_CONTRACTS
from backend.agents.graph_workflow import execute_agent_action as execute_tool
from backend.config import SESSION_ID, get_settings
from backend.services.identity_service import assert_owner, current_user_id
from backend.services.report_planner import UnsupportedReportFeature


class RunBusyError(RuntimeError):
    pass


logger = logging.getLogger("autobi.agent.runs")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(run_id: str) -> Path:
    if not re.fullmatch(r"run-[a-f0-9]{12}", run_id):
        raise ValueError("Run was not found")
    base_dir = get_settings().app_data_dir
    legacy_path = base_dir / "runs" / f"{run_id}.json"
    if legacy_path.is_file():
        return legacy_path
    sessions_dir = base_dir / "sessions"
    if sessions_dir.is_dir():
        for session_dir in sessions_dir.iterdir():
            candidate = session_dir / "runs" / f"{run_id}.json"
            if candidate.is_file():
                return candidate
    root = sessions_dir / SESSION_ID / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{run_id}.json"


def _lease_path(run_id: str) -> Path:
    return _path(run_id).with_suffix(".lease")


def _cancel_path(run_id: str) -> Path:
    return _path(run_id).with_suffix(".cancel")


def _acquire_lease(run_id: str) -> int:
    lease = _lease_path(run_id)
    try:
        return os.open(lease, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        try:
            if time.time() - lease.stat().st_mtime > 300:
                lease.unlink()
                return os.open(lease, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            pass
        raise RunBusyError("This run is already executing; the duplicate resume was ignored") from error


def _release_lease(run_id: str, descriptor: int) -> None:
    os.close(descriptor)
    _lease_path(run_id).unlink(missing_ok=True)


def _cancel_requested(run_id: str) -> bool:
    return _cancel_path(run_id).is_file()


def _field_choice_options(error: str | None) -> list[str]:
    if not error:
        return []
    choice_match = re.search(r"\bchoose one:\s*", error, re.I)
    if not choice_match:
        return []
    choices = error[choice_match.end():]
    return list(dict.fromkeys(
        f"Use {table.strip()}[{field.strip()}]"
        for table, field in re.findall(r"([^,\[\]]+)\[([^\[\]]+)\]", choices)
        if table.strip() and field.strip()
    ))


def request_cancellation(run: dict[str, Any]) -> dict[str, Any]:
    if run.get("status") in {"completed", "cancelled"}:
        return run
    _cancel_path(run["runId"]).write_text(_now(), encoding="utf-8")
    run["cancelRequested"] = True
    record(run, "cancellation_requested", requestedBy=current_user_id())
    return run


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def create_run(source_id: str, goal: str, steps: list[dict[str, Any]], *, retry_limit: int = 2,
               requires_approval: bool = False, planner: str = "rules", fallback_used: bool = False,
               context: dict[str, Any] | None = None, source_type: str | None = None,
               selected_table: str | None = None, report_name: str | None = None) -> dict[str, Any]:
    plan = [{"index": index, **step} for index, step in enumerate(steps)]
    report_spec_fingerprint = fingerprint((context or {}).get("reportSpec"))
    base_plan_fingerprint = fingerprint({"sourceId": source_id, "goal": goal, "steps": plan})
    plan_version = fingerprint({"basePlan": base_plan_fingerprint, "reportSpec": report_spec_fingerprint})
    mutating = any(TOOL_CONTRACTS[step["action"]].mutates for step in plan)
    run = {
        "runId": f"run-{uuid4().hex[:12]}", "sourceId": source_id, "goal": goal,
        "ownerId": current_user_id(),
        "planVersion": plan_version, "status": "awaiting_approval" if requires_approval or mutating else "running",
        "steps": [{**step, "status": "pending", "attempts": 0, "result": None, "error": None} for step in plan],
        "completedSteps": [], "results": {}, "retryLimit": max(0, min(int(retry_limit), 5)),
        "requiresApproval": bool(requires_approval or mutating), "approvals": [],
        "planner": planner, "fallbackUsed": fallback_used, "currentStep": None,
        "contextFingerprint": fingerprint(context or {}),
        "basePlanFingerprint": base_plan_fingerprint, "reportSpecFingerprint": report_spec_fingerprint,
        "auditContext": {
            "sourceType": source_type if source_type in {"excel", "sql"} else None,
            "selectedTable": selected_table[:256] if isinstance(selected_table, str) else None,
            "reportName": report_name[:256] if isinstance(report_name, str) else None,
        },
        "traceIds": [],
        "createdAt": _now(), "updatedAt": _now(), "events": [],
    }
    save_run(run)
    return run


def load_run(run_id: str) -> dict[str, Any]:
    if not _path(run_id).is_file():
        raise ValueError("Run was not found")
    run = json.loads(_path(run_id).read_text(encoding="utf-8"))
    assert_owner(run.get("ownerId"), "Run")
    return run


def save_run(run: dict[str, Any]) -> None:
    run["updatedAt"] = _now()
    target = _path(run["runId"])
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(run, ensure_ascii=True, indent=2, default=str), encoding="utf-8")
    # Virus scanners and indexers can briefly hold a newly-written JSON file
    # on Windows. Retry the atomic replacement instead of failing the run.
    for attempt in range(4):
        try:
            temporary.replace(target)
            return
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.05 * (attempt + 1))


def record(run: dict[str, Any], event: str, **details: Any) -> None:
    run.setdefault("events", []).append({"at": _now(), "event": event, **details})
    save_run(run)


def record_trace_id(run: dict[str, Any], trace_id: object) -> None:
    if not isinstance(trace_id, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", trace_id):
        return
    trace_ids = run.setdefault("traceIds", [])
    if trace_id in trace_ids:
        return
    trace_ids.append(trace_id.lower())
    record(run, "trace_linked", traceId=trace_id.lower())


def link_report(run: dict[str, Any], report_id: str, report_name: str | None = None) -> dict[str, Any]:
    audit_context = run.setdefault("auditContext", {})
    if not isinstance(audit_context, dict):
        audit_context = {}
        run["auditContext"] = audit_context
    audit_context["reportId"] = report_id[:128]
    if isinstance(report_name, str):
        audit_context["reportName"] = report_name[:256]
    record(run, "report_linked", reportId=audit_context["reportId"], reportName=audit_context.get("reportName"))
    return run


def list_user_runs(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    base_dir = get_settings().app_data_dir
    roots = [base_dir / "runs"]
    sessions_dir = base_dir / "sessions"
    if sessions_dir.is_dir():
        roots.extend(session_dir / "runs" for session_dir in sessions_dir.iterdir() if session_dir.is_dir())
    runs = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.glob("run-*.json"):
            try:
                run = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                logger.warning("Skipping unreadable agent run record", extra={
                    "event": "agent.run.history.invalid_record",
                    "run_file": path.name,
                    "failure_type": type(error).__name__,
                })
                continue
            if not isinstance(run, dict):
                continue
            try:
                assert_owner(run.get("ownerId"), "Run")
            except PermissionError:
                continue
            audit_context = run.get("auditContext", {})
            runs.append({
                "runId": run.get("runId"),
                "status": run.get("status", "unknown"),
                "goal": run.get("goal", ""),
                "sourceId": run.get("sourceId"),
                "createdAt": run.get("createdAt"),
                "updatedAt": run.get("updatedAt"),
                "sourceType": audit_context.get("sourceType") if isinstance(audit_context, dict) else None,
                "selectedTable": audit_context.get("selectedTable") if isinstance(audit_context, dict) else None,
                "reportName": audit_context.get("reportName") if isinstance(audit_context, dict) else None,
                "stepCount": len(run.get("steps", [])),
                "completedStepCount": len(run.get("completedSteps", [])),
            })
    runs.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
    start = max(0, int(offset))
    count = max(1, min(int(limit), 101))
    return runs[start:start + count]


def build_audit_record(run: dict[str, Any]) -> dict[str, Any]:
    """Return a portable audit view without tool outputs or workbook rows."""
    events = []
    for event in run.get("events", []):
        if not isinstance(event, dict):
            continue
        entry = {key: event[key] for key in (
            "at", "event", "step", "action", "attempt", "approver", "planVersion",
            "reason", "error", "completedSteps", "traceId", "reportId", "reportName",
        ) if key in event}
        events.append(entry)
    audit_context = run.get("auditContext", {})
    if not isinstance(audit_context, dict):
        audit_context = {}
    steps = []
    for step in run.get("steps", []):
        if not isinstance(step, dict):
            continue
        steps.append({
            "index": step.get("index"),
            "action": step.get("action"),
            "reason": step.get("reason"),
            "status": step.get("status"),
            "attempts": step.get("attempts", 0),
            "error": step.get("error"),
        })
    started_at = run.get("createdAt")
    updated_at = run.get("updatedAt")
    elapsed = None
    if isinstance(started_at, str) and isinstance(updated_at, str):
        try:
            elapsed_seconds = max(0, int((datetime.fromisoformat(updated_at) - datetime.fromisoformat(started_at)).total_seconds()))
            elapsed = f"{elapsed_seconds // 60}m {elapsed_seconds % 60}s"
        except ValueError:
            pass
    return {
        "format": "autobi-run-audit",
        "version": 1,
        "exportedAt": _now(),
        "notice": "This summary excludes raw tool outputs, workbook rows, and credentials.",
        "run": {
            "runId": run.get("runId"),
            "traceIds": run.get("traceIds", []),
            "status": run.get("status", "unknown"),
            "startedAt": run.get("createdAt"),
            "lastUpdatedAt": run.get("updatedAt"),
            "elapsed": elapsed,
            "sourceId": run.get("sourceId"),
            "sourceType": audit_context.get("sourceType"),
            "selectedTable": audit_context.get("selectedTable"),
            "reportName": audit_context.get("reportName"),
            "reportId": audit_context.get("reportId"),
            "request": run.get("goal", ""),
            "planVersion": run.get("planVersion"),
            "planner": run.get("planner"),
            "fallbackUsed": bool(run.get("fallbackUsed")),
            "approvals": [
                {
                    "approver": item.get("approver"),
                    "approvedAt": item.get("approvedAt"),
                    "planVersion": item.get("planVersion"),
                }
                for item in run.get("approvals", [])
                if isinstance(item, dict)
            ],
            "steps": steps,
            "events": events,
        },
    }


def approve_run(run: dict[str, Any], plan_version: str, approver: str) -> dict[str, Any]:
    if plan_version != run["planVersion"]:
        raise ValueError("Approval does not match the current plan version")
    approval = {"planVersion": plan_version, "approver": approver[:128], "approvedAt": _now()}
    run["approvals"] = [approval]
    run["status"] = "running"
    record(run, "approved", approver=approval["approver"], planVersion=plan_version)
    return run


def _has_current_approval(run: dict[str, Any]) -> bool:
    return any(item.get("planVersion") == run["planVersion"] for item in run.get("approvals", []))


async def execute_run(run: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    descriptor = _acquire_lease(run["runId"])
    try:
        result = await _execute_run_locked(run, context)
        from backend.services.conversation_memory import remember
        remember(result, context or {})
        return result
    finally:
        _release_lease(run["runId"], descriptor)


async def _execute_run_locked(run: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    if run.get("status") in {"failed", "completed", "cancelled"}:
        return run
    context = context or {}
    selected_table = context.get("selectedTable")
    audit_context = run.setdefault("auditContext", {})
    if isinstance(selected_table, str) and isinstance(audit_context, dict) and selected_table != audit_context.get("selectedTable"):
        audit_context["selectedTable"] = selected_table[:256]
        record(run, "source_context_changed", selectedTable=audit_context["selectedTable"])
    if _cancel_requested(run["runId"]):
        run.update({"status": "cancelled", "currentStep": None, "cancelRequested": False})
        _cancel_path(run["runId"]).unlink(missing_ok=True)
        record(run, "run_cancelled")
        return run
    incoming_context_fingerprint = fingerprint(context)
    if run.get("status") == "needs_user":
        explicit_answer = context.get("clarificationAnswer")
        user_turns = [item.get("text") for item in context.get("conversation", []) if item.get("role") == "user"]
        answer = explicit_answer if isinstance(explicit_answer, str) and explicit_answer.strip() else (
            user_turns[-1] if user_turns and incoming_context_fingerprint != run.get("contextFingerprint") else None)
        if not isinstance(answer, str) or not answer.strip():
            return run
        answer = answer.strip()
        options = run.get("help", {}).get("options", [])
        if isinstance(answer, str) and options and answer not in options:
            exact = [o for o in options if answer.strip().casefold() in {
                o.casefold(), o.removeprefix("Use ").casefold(),
                o.rsplit("[", 1)[-1].rstrip("]").casefold()
            }]
            if len(exact) == 1:
                answer = exact[0]
        if (
            isinstance(answer, str)
            and isinstance(options, list)
            and options
            and answer not in options
            and not run.get("help", {}).get("allowFreeform")
        ):
            # A field-choice prompt is an allow-list. Do not reinterpret a
            # confirmation or free-form sentence as a schema field.
            run["status"] = "needs_user"
            run["help"]["message"] = "Choose one of the available fields to continue."
            record(run, "user_guidance_rejected", reason="answer_not_in_options")
            return run
        if isinstance(answer, str):
            clarification = {
                "question": run.get("help", {}).get("message", ""),
                "answer": answer,
            }
            clarification_history = run.setdefault("clarificationHistory", [])
            if clarification not in clarification_history:
                clarification_history.append(clarification)
            run["clarificationRounds"] = run.get("clarificationRounds", 0) + 1
            run["clarificationAnswer"] = answer
            run["clarificationFingerprint"] = fingerprint(answer)
            run["planVersion"] = fingerprint({
                "basePlan": run["basePlanFingerprint"],
                "reportSpec": run.get("reportSpecFingerprint"),
                "clarification": run["clarificationFingerprint"],
            })
            run["approvals"] = []
        if isinstance(answer, str) and all(step.get("action") == "clarify" and step.get("status") == "needs_user" for step in run["steps"]):
            from backend.agents.orchestrator import AgentOrchestrator
            from backend.services.model_service import load_source_metadata
            decision = await AgentOrchestrator().decide(
                run["sourceId"], run["goal"], bool(context.get("reportSpec")),
                context.get("selectedTable"), load_source_metadata(run["sourceId"]),
                context.get("conversation", []), answer)
            # Replan this clarification-only run; completed tool steps are never replayed.
            run["steps"] = [{"index":i, **step, "status":"pending", "attempts":0,
                             "result":None,"error":None} for i,step in enumerate(decision["plannedSteps"])]
            run["requiresApproval"] = bool(decision["requiresApproval"] or any(TOOL_CONTRACTS[x["action"]].mutates for x in run["steps"]))
            run["basePlanFingerprint"] = fingerprint(run["steps"])
            run["planVersion"] = fingerprint({"steps":run["steps"],"clarification":answer,"report":context.get("reportSpec")})
            context["plannerMessage"] = decision.get("message", "")
            run["approvals"] = []
        for step in run["steps"]:
            if step.get("status") == "needs_user":
                step.update({"status": "pending", "attempts": 0, "error": None})
                step["clarificationRounds"] = step.get("clarificationRounds", 0) + 1
        run.pop("help", None)
        record(run, "user_guidance_received")
    if isinstance(run.get("clarificationAnswer"), str):
        context["clarificationAnswer"] = run["clarificationAnswer"]
    if run.get("clarificationHistory"):
        context["clarificationHistory"] = [dict(item) for item in run["clarificationHistory"]]
    new_context_fingerprint = fingerprint(context)
    new_report_fingerprint = fingerprint(context.get("reportSpec"))
    previous_report_fingerprint = run.get("reportSpecFingerprint", fingerprint(None))
    if new_report_fingerprint != previous_report_fingerprint:
        run["approvals"] = []
        run["reportSpecFingerprint"] = new_report_fingerprint
        run["planVersion"] = fingerprint({"basePlan": run["basePlanFingerprint"], "reportSpec": new_report_fingerprint})
        record(run, "approval_invalidated", reason="report plan changed")
    run["contextFingerprint"] = new_context_fingerprint
    if run.get("requiresApproval") and not _has_current_approval(run):
        run["status"] = "awaiting_approval"
        save_run(run)
        return run

    run["status"] = "running"
    for step in run["steps"]:
        if step["status"] == "completed":
            continue
        run["currentStep"] = step["index"]
        step["status"] = "running"
        record(run, "step_started", step=step["index"], action=step["action"])
        last_error: str | None = None
        repair_context: dict[str, Any] | None = None
        for attempt in range(step["attempts"] + 1, run["retryLimit"] + 2):
            if _cancel_requested(run["runId"]):
                run.update({"status": "cancelled", "currentStep": None, "cancelRequested": False})
                _cancel_path(run["runId"]).unlink(missing_ok=True)
                record(run, "run_cancelled", step=step["index"])
                return run
            step["attempts"] = attempt
            try:
                # `goal` is the original, user-authored request. A
                # clarification remains a separate context value so it cannot
                # accidentally become a new report request or be parsed twice.
                effective_goal = run.get("resolvedGoal", run["goal"])
                step_context = {
                    **context,
                    "stepResults": dict(run["results"]),
                    "previousStepResult": run["results"].get(str(step["index"] - 1)),
                    "repair": repair_context,
                }
                result = await execute_tool(step["action"], run["sourceId"], effective_goal, step_context)
                if _cancel_requested(run["runId"]):
                    run.update({"status": "cancelled", "currentStep": None, "cancelRequested": False})
                    _cancel_path(run["runId"]).unlink(missing_ok=True)
                    record(run, "run_cancelled", step=step["index"])
                    return run
                passed, evaluation_error = evaluate_tool_result(step["action"], result)
                if not passed:
                    raise ValueError(evaluation_error or "Tool result failed evaluation")
                step.update({"status": "completed", "result": result, "error": None})
                run["results"][str(step["index"])] = result
                evidence = result.get("planningEvidence") if isinstance(result, dict) else None
                planning_table = result.get("planningTable") if isinstance(result, dict) else None
                if not isinstance(planning_table, str) and isinstance(result, dict):
                    planning_table = evidence.get("resolvedTable") if isinstance(evidence, dict) else None
                audit_context = run.setdefault("auditContext", {})
                if (
                    isinstance(planning_table, str)
                    and isinstance(audit_context, dict)
                    and planning_table != audit_context.get("selectedTable")
                ):
                    context["selectedTable"] = planning_table
                    audit_context["selectedTable"] = planning_table[:256]
                    record(run, "source_context_changed", selectedTable=audit_context["selectedTable"], reason="schema_match")
                if step["action"] in {"plan_dashboard", "edit_report"} and isinstance(result, dict):
                    if isinstance(audit_context, dict) and isinstance(result.get("page"), str):
                        audit_context["reportName"] = result["page"][:256]
                if step["index"] not in run["completedSteps"]:
                    run["completedSteps"].append(step["index"])
                record(run, "step_completed", step=step["index"], action=step["action"], attempt=attempt)
                break
            except AnalysisClarificationRequired as exc:
                previous_questions = [item.get("question", "") for item in run.get("clarificationHistory", [])]
                normalize_question = lambda value: re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
                repeated = normalize_question(exc.question) in {normalize_question(q) for q in previous_questions}
                if repeated or run.get("clarificationRounds", 0) >= 3:
                    error = ("The planner repeated an answered question or exceeded three clarification rounds. "
                             "Your answers have been saved. Start a new request with an explicit table and metric.")
                    step.update({"status": "failed", "error": error})
                    run.update({"status": "failed", "currentStep": None,
                                "help": {"message": error, "options": []}})
                    record(run, "clarification_limit_reached", step=step["index"], question=exc.question)
                    return run
                step.update({"status": "needs_user", "error": str(exc)})
                run.update({"status": "needs_user", "currentStep": step["index"]})
                run["help"] = {
                    "message": exc.question,
                    "step": step["index"],
                    "error": str(exc),
                    "options": exc.options,
                    "allowFreeform": True,
                }
                record(
                    run, "user_help_required", step=step["index"],
                    error=str(exc), options=exc.options,
                )
                return run
            except (AIServiceError, UnsupportedReportFeature) as exc:
                error = str(exc)
                step.update({"status": "failed", "error": error})
                run.update({"status": "failed", "currentStep": None})
                run["help"] = {
                    "message": error,
                    "step": step["index"],
                    "error": error,
                    "options": [],
                }
                record(run, "step_failed", step=step["index"], action=step["action"], attempt=attempt, error=error)
                record(run, "run_failed", step=step["index"], error=error)
                return run
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                step["error"] = last_error
                repair_context = {
                    "attempt": attempt + 1,
                    "validationError": str(exc),
                    "instruction": "Revise the tool inputs or plan to satisfy this validation error; do not repeat the rejected output.",
                }
                rejected_visual = getattr(exc, "rejected_visual", None)
                if isinstance(rejected_visual, dict):
                    repair_context["rejectedVisual"] = rejected_visual
                record(
                    run, "step_repair", step=step["index"], action=step["action"],
                    attempt=attempt, error=last_error,
                    repair={key: value for key, value in repair_context.items() if key != "rejectedVisual"},
                )
                if isinstance(exc, ValueError) and ("Ambiguous" in str(exc) or "Could not resolve" in str(exc)):
                    break
        if step["status"] != "completed":
            options = _field_choice_options(last_error)
            if not options:
                step["status"] = "failed"
                run.update({"status": "failed", "currentStep": None})
                run["help"] = {"message": f"The generated plan failed validation after bounded repair: {last_error}. "
                                          "Review the error and start a revised request.",
                               "error": last_error, "options": []}
                record(run, "run_failed", step=step["index"], error=last_error)
                return run
            step["status"] = "needs_user"
            run["status"] = "needs_user"
            options = _field_choice_options(last_error)
            run["help"] = {
                "message": (
                    "I couldn't map the requested field. Which available field should I use?"
                    if options else
                    (last_error or "AURA could not repair this step within the retry limit.")
                ),
                "step": step["index"], "error": last_error, "options": options,
            }
            record(run, "user_help_required", step=step["index"], error=last_error)
            return run
    run["status"] = "completed"
    run["currentStep"] = None
    record(run, "run_completed", completedSteps=run["completedSteps"])
    return run
