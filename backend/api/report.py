import json
import logging
import os
import uuid
import time
import re
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from automation.design_system import build_theme, normalize_design_spec
from automation.ai.ai_service import AIServiceError, get_ai_provider
from automation.ai.prompts import PROMPT_VERSION
from automation.report_automation import _requested_visual_count, requested_visual_count_mode
from automation.semantic.semantic_validator import normalize_ai_measure_bindings, normalize_ai_table_bindings, validate_ai_plan
from automation.semantic.workbook_profile import build_workbook_profile, profile_for_ai
from automation.report_automation import validate_report_spec
from backend.config import get_settings
from backend.policy import (
    MAX_CONVERSATION_MESSAGES,
    MAX_MESSAGE_CHARACTERS,
    MAX_PREVIEW_POINTS,
    MAX_RANKING_COUNT,
    MAX_VISUALS_PER_PAGE,
)
from backend.services.ai_audit_service import (
    dashboard_decisions,
    failure_details,
    write_rejected_request_audit_document,
    write_ai_audit_event,
    write_report_audit_document,
)
from backend.services import ai_audit_service
from backend.services.model_service import (
    _active_relationships,
    excel_to_semantic_model,
    load_source_metadata,
    load_source_payload,
    save_source_metadata,
    validate_parsing_review,
)
from backend.services.pbir_service import create_pbir_project, ensure_report_layout
from backend.services.report_planner import check_visual_request
from backend.agents.tools import _planning_scope, execute_tool
from automation.report_pages import is_multi_page_request, page_requests, report_pages, pack_pages
from backend.services.report_edit_service import edit_report_spec
from backend.services.validator_service import validate_generated_report
from backend.services.package_service import create_pbip_project_file, validate_pbip_project_file, zip_report
from backend.services.identity_service import assert_owner, current_user_id
from backend.services.calculation_validation_service import incompatible_slicer_indexes, validate_calculation_accuracy

router = APIRouter(prefix="/api/report", tags=["report"])
logger = logging.getLogger("autobi.ai")
REPORTS: dict[str, dict] = {}
APP_ROOT = Path(__file__).resolve().parents[2]
# Kept as a module-level alias so tests and embedders can redirect storage.
APP_DATA_ROOT = get_settings().app_data_dir


def _resolved_report_path(report_id: str, stored_path: object) -> Path | None:
    """Resolve current and legacy manifest paths to the configured reports root."""
    if not isinstance(stored_path, str) or not stored_path:
        return None
    expected = (APP_DATA_ROOT / "reports" / f"{report_id}.Report").resolve()
    candidate_path = Path(stored_path)
    for root in (APP_DATA_ROOT, APP_ROOT):
        if (root / candidate_path).resolve() == expected:
            return expected
    return None


def _report_entry(report_id: str) -> dict | None:
    """Recover generated report metadata after an API process restart."""
    if not re.fullmatch(r"report-[a-f0-9]{8}", report_id):
        return None
    if report_id in REPORTS:
        entry = REPORTS[report_id]
        assert_owner(entry.get("ownerId"), "Report")
        return entry
    manifest = APP_DATA_ROOT / "reports" / f"{report_id}.json"
    if not manifest.is_file():
        return None
    try:
        entry = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(entry, dict) or entry.get("reportId") != report_id:
            return None
        if _resolved_report_path(report_id, entry.get("reportPath")) is None:
            return None
        if entry.get("status") not in {"generated", "failed"} or not isinstance(entry.get("validation"), dict):
            return None
    except (OSError, ValueError, TypeError):
        logger.warning("Report manifest could not be loaded", extra={
            "event": "report.manifest.invalid", "report_id": report_id,
        })
        return None
    REPORTS[report_id] = entry
    assert_owner(entry.get("ownerId"), "Report")
    return entry


def _spec_fingerprint(report_spec: dict) -> str:
    return hashlib.sha256(json.dumps(report_spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _normalized_spec_fingerprint(report_spec: dict) -> str:
    pages = report_pages(deepcopy(report_spec))
    for page in pages:
        page["design"] = normalize_design_spec(page)
    return _spec_fingerprint(pack_pages(pages))


@router.get("")
async def list_reports(limit: int = 50, offset: int = 0) -> dict:
    """List validated reports owned by the current user."""
    reports_dir = APP_DATA_ROOT / "reports"
    if not reports_dir.is_dir():
        return {"reports": [], "hasMore": False}

    bounded_limit = max(1, min(limit, 100))
    bounded_offset = max(0, offset)
    reports = []
    for manifest in reports_dir.glob("report-*.json"):
        try:
            entry = _report_entry(manifest.stem)
        except PermissionError:
            continue
        if not entry or entry.get("status") != "generated" or not entry.get("validation", {}).get("passed"):
            continue
        spec = entry.get("reportSpec")
        if not isinstance(spec, dict):
            continue
        pages = [spec]
        pending = list(spec.get("additionalPages", [])) if isinstance(spec.get("additionalPages"), list) else []
        while pending:
            page = pending.pop()
            if isinstance(page, dict):
                pages.append(page)
                children = page.get("additionalPages")
                if isinstance(children, list):
                    pending.extend(children)
        try:
            updated_at = datetime.fromtimestamp(manifest.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            logger.warning("Report manifest disappeared while listing reports", extra={
                "event": "report.manifest.unavailable", "report_id": manifest.stem,
            })
            continue
        reports.append({
            "reportId": entry["reportId"],
            "title": str(spec.get("page") or "Untitled report"),
            "chatTitle": entry.get("chatTitle") or str(spec.get("page") or "Untitled report"),
            "chatDeleted": entry.get("chatDeleted") is True,
            "status": entry["status"],
            "pageCount": len(pages),
            "visualCount": sum(len(page.get("visuals", [])) for page in pages if isinstance(page.get("visuals"), list)),
            "updatedAt": updated_at,
            "sourceId": entry.get("sourceId"),
            "sourceName": entry.get("sourceName"),
            "sourceType": entry.get("sourceType"),
            "reportSpec": spec,
        })

    reports.sort(key=lambda item: item["updatedAt"], reverse=True)
    return {
        "reports": reports[bounded_offset:bounded_offset + bounded_limit],
        "hasMore": len(reports) > bounded_offset + bounded_limit,
    }


def _write_json_atomically(path: Path, payload: dict) -> None:
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@router.patch("/{report_id}/conversation")
async def rename_report_conversation(report_id: str, payload: dict) -> dict:
    entry = _report_entry(report_id)
    if not entry or entry.get("chatDeleted") is True:
        raise HTTPException(status_code=404, detail="Conversation not found")
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
        raise HTTPException(status_code=400, detail="Chat name must contain 1 to 200 characters")
    updated = {**entry, "chatTitle": title.strip()}
    try:
        _write_json_atomically(APP_DATA_ROOT / "reports" / f"{report_id}.json", updated)
    except OSError as exc:
        logger.exception("Could not rename saved conversation", extra={"event": "report.conversation.rename_failed", "report_id": report_id})
        raise HTTPException(status_code=500, detail="Could not rename the chat") from exc
    REPORTS[report_id] = updated
    return {"title": updated["chatTitle"]}


@router.delete("/{report_id}/conversation")
async def delete_report_conversation(report_id: str) -> dict:
    entry = _report_entry(report_id)
    if not entry or entry.get("chatDeleted") is True:
        raise HTTPException(status_code=404, detail="Conversation not found")
    audit_path = ai_audit_service.AUDIT_FILE_PATH.parent / "reports" / f"{report_id}.json"
    try:
        if audit_path.exists():
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            if not isinstance(audit, dict) or audit.get("report_id") != report_id or audit.get("source_id") != entry.get("sourceId"):
                raise ValueError("Invalid report audit")
            audit["conversation"] = []
            _write_json_atomically(audit_path, audit)
    except (OSError, ValueError, TypeError) as exc:
        logger.exception("Could not delete saved conversation", extra={"event": "report.conversation.delete_failed", "report_id": report_id})
        raise HTTPException(status_code=500, detail="Could not delete the chat") from exc
    updated = {**entry, "chatDeleted": True}
    try:
        _write_json_atomically(APP_DATA_ROOT / "reports" / f"{report_id}.json", updated)
    except OSError as exc:
        logger.exception("Could not remove deleted conversation from recents", extra={"event": "report.conversation.hide_failed", "report_id": report_id})
        raise HTTPException(status_code=500, detail="The conversation was deleted, but could not be removed from Recents. Please retry.") from exc
    REPORTS[report_id] = updated
    return {"deleted": True}


@router.post("/approve")
async def approve_report(payload: dict) -> dict:
    """Bind human approval to the exact source and report plan being generated."""
    source_id, report_spec = payload.get("sourceId"), payload.get("reportSpec")
    if not isinstance(source_id, str) or not isinstance(report_spec, dict):
        raise HTTPException(status_code=400, detail="sourceId and reportSpec are required")
    try:
        source = load_source_payload(source_id)
        validate_report_spec(report_spec, load_source_metadata(source_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    approver = current_user_id()
    source["approval"] = {"planFingerprint": _spec_fingerprint(report_spec), "approver": approver, "status": "approved"}
    save_source_metadata(source_id, source)
    write_ai_audit_event(source_id=source_id, operation="report_approval", provider="user", model="manual", outcome="succeeded", validation_result="approved", context={"plan_fingerprint": source["approval"]["planFingerprint"]})
    return {"approved": True, "planFingerprint": source["approval"]["planFingerprint"]}


@router.post("/plan")
async def plan_report(payload: dict) -> dict:
    source_id = payload.get("sourceId")
    request_text = payload.get("request")
    if not isinstance(source_id, str) or not source_id or not isinstance(request_text, str) or not request_text.strip():
        raise HTTPException(status_code=400, detail="sourceId and request are required")
    try:
        from backend.services.report_planner import validate_requested_report_features
        validate_requested_report_features(request_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        metadata = load_source_metadata(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        if page_requests(request_text) or is_multi_page_request(request_text):
            return await execute_tool("plan_dashboard", source_id, request_text, {
                "selectedTable": payload.get("table"), "clarificationAnswer": payload.get("clarificationAnswer")
            })
    except AIServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    chart_metadata = metadata.get("metadata", metadata)
    selected_table = payload.get("table")
    if selected_table and selected_table not in chart_metadata.get("tables", {}):
        raise HTTPException(status_code=400, detail="Selected table was not found in this source")
    chart_metadata, selected_table = _planning_scope(chart_metadata, request_text, {
        "selectedTable": selected_table, "clarificationAnswer": payload.get("clarificationAnswer")
    })
    settings = get_settings()
    try:
        validate_parsing_review({"metadata": metadata, "parsingReview": metadata.get("parsingReview", {})},
                                {selected_table} if selected_table else set(chart_metadata.get("tables", {})))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        if not settings.use_ai:
            raise HTTPException(status_code=503, detail="AI is required for report planning but is disabled.")
        source_payload = load_source_payload(source_id)
        profile = source_payload.get("workbookProfile")
        if not profile and source_payload.get("path"):
            profile = build_workbook_profile(source_payload["path"], source_payload.get("metadata", {}))
        understanding = source_payload.get("aiUnderstanding")
        if not profile or not understanding:
            raise HTTPException(
                status_code=503,
                detail="AI workbook context is unavailable. Re-upload the workbook and try again.",
            )
        requested_count = _requested_visual_count(request_text)
        count_mode = requested_visual_count_mode(request_text)
        if requested_count is not None and not 1 <= requested_count <= MAX_VISUALS_PER_PAGE:
            raise HTTPException(
                status_code=400,
                detail=f"AI dashboard requests must ask for between 1 and {MAX_VISUALS_PER_PAGE} visuals",
            )
        provider_name = settings.ai_provider
        model_name = settings.azure_openai_deployment if settings.ai_provider == "azure_openai" else "mock"
        provider = get_ai_provider(settings)
        model_name = provider.model
        result = await provider.plan_dashboard(
            request_text,
            profile_for_ai(profile),
            understanding,
            chart_metadata,
            requested_count,
            visual_count_mode=count_mode,
        )
        if result.get("status") == "needs_clarification":
            write_ai_audit_event(
                source_id=source_id,
                operation="dashboard_planning",
                provider=provider_name,
                model=model_name,
                outcome="clarification",
                validation_result="passed",
                decisions=dashboard_decisions(result),
            )
            return result
        table_corrections = normalize_ai_table_bindings(result, chart_metadata)
        measure_corrections = normalize_ai_measure_bindings(result, chart_metadata)
        validate_ai_plan(result, chart_metadata, requested_count, count_mode)
        check_visual_request(result, request_text, chart_metadata)
        write_ai_audit_event(
            source_id=source_id,
            operation="dashboard_planning",
            provider=provider_name,
            model=model_name,
            outcome="succeeded",
            validation_result="passed",
            decisions=dashboard_decisions(result),
            context={"selected_table": selected_table, "requested_visual_count": requested_count,
                     "requested_visual_count_mode": count_mode,
                     "automatic_table_corrections": table_corrections,
                     "automatic_measure_corrections": measure_corrections},
        )
        result["planningEvidence"] = {"planner": "ai", "resolvedTable": selected_table}
        return {key: value for key, value in result.items() if key != "status"}
    except AIServiceError as exc:
        write_ai_audit_event(
            source_id=source_id,
            operation="dashboard_planning",
            provider=settings.ai_provider,
            model=settings.azure_openai_deployment or settings.ai_provider,
            outcome="failed",
            validation_result="failed",
            failure=failure_details(exc),
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        write_ai_audit_event(
            source_id=source_id,
            operation="dashboard_planning",
            provider=settings.ai_provider,
            model=settings.azure_openai_deployment or settings.ai_provider,
            outcome="failed",
            validation_result="failed",
            failure=failure_details(exc),
        )
        write_rejected_request_audit_document(
            source_id=source_id,
            user_request=request_text,
            reason=str(exc),
            source_metadata=chart_metadata,
        )
        raise HTTPException(status_code=502, detail=f"AI dashboard plan was rejected: {exc}") from exc


@router.get("/ai-status")
async def ai_status() -> dict:
    settings = get_settings()
    return {
        "enabled": settings.use_ai,
        "provider": settings.ai_provider if settings.use_ai else None,
        "model": settings.azure_openai_deployment if settings.use_ai and settings.ai_provider == "azure_openai" else None,
        "configured": bool(settings.ai_provider == "mock" or (
            settings.azure_openai_api_key and settings.azure_openai_endpoint and settings.azure_openai_deployment
            and settings.azure_ai_foundry_project_endpoint
        )) if settings.use_ai else False,
    }


@router.post("/ai-test")
async def test_ai_connection() -> dict:
    settings = get_settings()
    if not settings.use_ai:
        raise HTTPException(status_code=400, detail="AI mode is disabled.")
    if settings.ai_provider == "mock":
        return {"connected": True, "provider": "mock", "model": "mock"}
    try:
        provider = get_ai_provider(settings)
        await provider.test_connection()
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"connected": True, "provider": settings.ai_provider, "model": settings.azure_openai_deployment}


@router.post("/preview")
async def preview_report(payload: dict) -> dict:
    source_id = payload.get("sourceId")
    report_spec = payload.get("reportSpec") or {}
    if not source_id:
        raise HTTPException(status_code=400, detail="sourceId is required")
    if not report_spec:
        raise HTTPException(status_code=400, detail="reportSpec is required")

    try:
        metadata = load_source_metadata(source_id)
        source_payload = load_source_payload(source_id)
        validate_report_spec(report_spec, metadata)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if report_spec.get("additionalPages"):
        from automation.report_pages import validate_sync_groups
        groups = validate_sync_groups(report_spec, require_multiple_pages=True)
        filters = payload.get("filters", {})
        sync_filters = payload.get("syncFilters", {})
        if not isinstance(filters, dict) or not isinstance(sync_filters, dict):
            raise HTTPException(status_code=400, detail="filters and syncFilters must be objects")
        if any(name not in groups or not isinstance(values, list) or any(not isinstance(value, str) for value in values)
               for name, values in sync_filters.items()):
            raise HTTPException(status_code=400, detail="Invalid synchronized slicer filters")
        sync_filters = dict(sync_filters)
        selected_page = payload.get("filterPage", report_spec["page"])
        for name, group in groups.items():
            table, field = group["binding"]
            key = f"{table}.{field}"
            if selected_page in group["pages"] and key in filters:
                sync_filters[name] = filters[key]
        previews = []
        for page in report_pages(report_spec):
            page_filters = {}
            for name, values in sync_filters.items():
                if page["page"] in groups[name]["pages"]:
                    table, field = groups[name]["binding"]
                    page_filters[f"{table}.{field}"] = values
            if selected_page == page["page"]:
                page_filters.update(filters)
            previews.append(await preview_report({**payload, "reportSpec": page, "filters": page_filters, "syncFilters": {}}))
        return pack_pages(previews)
    raw_tables = source_payload.get("metadata", {}).get("tables", {})
    active_relationships = _active_relationships(metadata)
    filters = payload.get("filters", {})
    if not isinstance(filters, dict):
        raise HTTPException(status_code=400, detail="filters must be an object")

    def row_matches_filters(
        row: dict,
        table_name: str,
        ignored_table: str | None,
        ignored_field: str | None,
    ) -> bool:
        for filter_key, values in filters.items():
            if not isinstance(values, list) or not values:
                continue
            if "." in filter_key:
                filter_table, filter_column = filter_key.split(".", 1)
                if filter_table != table_name or (
                    filter_table == ignored_table and filter_column == ignored_field
                ):
                    continue
            else:
                filter_column = filter_key
                if table_name == ignored_table and filter_column == ignored_field:
                    continue
            if str(row.get(filter_column)) not in values:
                return False
        return True

    def filtered_rows(table_name: str, ignored_field: str | None = None) -> list[dict]:
        filtered = {
            name: [
                row for row in table.get("rows", [])
                if isinstance(row, dict)
                and row_matches_filters(row, name, table_name if ignored_field else None, ignored_field)
            ]
            for name, table in raw_tables.items()
        }
        target_columns = set(metadata.get("tables", {}).get(table_name, {}).get("columns", []))
        has_external_filter = any(
            isinstance(values, list)
            and values
            and (
                filter_key.split(".", 1)[0] != table_name
                if "." in filter_key
                else filter_key not in target_columns
            )
            for filter_key, values in filters.items()
        )
        if not has_external_filter:
            return filtered.get(table_name, [])

        relationships = active_relationships
        for _ in range(len(filtered)):
            changed = False
            for relationship in relationships:
                source_name = relationship.get("source_table")
                target_name = relationship.get("target_table")
                source_column = relationship.get("source_column")
                target_column = relationship.get("target_column")
                if (
                    source_name not in filtered or target_name not in filtered
                    or not source_column or not target_column
                ):
                    continue
                source_keys = {
                    str(row[source_column]) for row in filtered[source_name]
                    if row.get(source_column) is not None
                }
                target_rows = [
                    row for row in filtered[target_name]
                    if row.get(target_column) is not None and str(row[target_column]) in source_keys
                ]
                if len(target_rows) < len(filtered[target_name]):
                    filtered[target_name] = target_rows
                    changed = True
                target_keys = {
                    str(row[target_column]) for row in filtered[target_name]
                    if row.get(target_column) is not None
                }
                source_rows = [
                    row for row in filtered[source_name]
                    if row.get(source_column) is not None and str(row[source_column]) in target_keys
                ]
                if len(source_rows) < len(filtered[source_name]):
                    filtered[source_name] = source_rows
                    changed = True
            if not changed:
                break
        return filtered.get(table_name, [])

    def measure_rows(rows: list[dict], table_name: str, measure_field: str) -> tuple[list[dict], str, str]:
        table = metadata["tables"][table_name]
        definition = table.get("measureDefinitions", {}).get(measure_field)
        if definition:
            matching_rows = rows
            if definition.get("filterColumn"):
                matching_rows = [
                    row for row in rows
                    if row.get(definition["filterColumn"]) == definition["filterValue"]
                ]
            return matching_rows, definition["column"], definition.get("aggregation", "SUM")
        return rows, measure_field.removeprefix("Total "), "SUM"

    def measure_total(rows: list[dict], table_name: str, measure_field: str) -> float:
        matching_rows, measure_column, aggregation = measure_rows(rows, table_name, measure_field)
        values = []
        for row in matching_rows:
            try:
                values.append(float(row[measure_column]))
            except (KeyError, TypeError, ValueError):
                continue
        if not values:
            return 0.0
        return {
            "AVERAGE": lambda: sum(values) / len(values),
            "MIN": lambda: min(values),
            "MAX": lambda: max(values),
            "COUNT": lambda: float(len(values)),
        }.get(aggregation, lambda: sum(values))()

    def totals(rows: list[dict], table_name: str, category_field: str, measure_field: str, *, chronological: bool = False) -> list[dict]:
        matching_rows, measure_column, aggregation = measure_rows(rows, table_name, measure_field)
        grouped: dict[str, list[float]] = {}
        for row in matching_rows:
            try:
                amount = float(row.get(measure_column))
            except (TypeError, ValueError):
                continue
            label = "(Blank)" if row.get(category_field) is None else str(row[category_field])
            grouped.setdefault(label, []).append(amount)
        def aggregate(values: list[float]) -> float:
            if aggregation == "AVERAGE":
                return sum(values) / len(values)
            if aggregation == "MIN":
                return min(values)
            if aggregation == "MAX":
                return max(values)
            if aggregation == "COUNT":
                return float(len(values))
            return sum(values)
        points = [{"label": label, "value": aggregate(values)} for label, values in grouped.items()]
        # Time previews preserve chronological points, rather than selecting
        # the largest twelve observations and connecting those as a trend.
        if chronological:
            return sorted(points, key=lambda item: item["label"])[:200]
        return sorted(points, key=lambda item: item["value"], reverse=True)[:MAX_PREVIEW_POINTS]

    def joined_rows(table_names: list[str]) -> list[dict[tuple[str, str], object]]:
        table_names = list(dict.fromkeys(table_names))
        if not table_names:
            return []
        current_table = table_names[0]
        joined = [
            {(current_table, column): value for column, value in row.items()}
            for row in filtered_rows(current_table)
        ]
        connected = {current_table}
        pending = set(table_names[1:])
        relationships = active_relationships
        while pending:
            progress = False
            for relationship in relationships:
                source_table = relationship.get("source_table")
                target_table = relationship.get("target_table")
                source_column = relationship.get("source_column")
                target_column = relationship.get("target_column")
                if not source_column or not target_column:
                    continue
                if source_table in connected and target_table in pending:
                    known_table, known_column = source_table, source_column
                    new_table, new_column = target_table, target_column
                elif target_table in connected and source_table in pending:
                    known_table, known_column = target_table, target_column
                    new_table, new_column = source_table, source_column
                else:
                    continue
                lookup: dict[str, list[dict]] = {}
                for row in filtered_rows(new_table):
                    key = row.get(new_column)
                    if key is not None:
                        lookup.setdefault(str(key), []).append(row)
                expanded = []
                for row in joined:
                    key = row.get((known_table, known_column))
                    if key is None:
                        continue
                    for match in lookup.get(str(key), []):
                        expanded.append({
                            **row,
                            **{(new_table, column): value for column, value in match.items()},
                        })
                joined = expanded
                connected.add(new_table)
                pending.remove(new_table)
                progress = True
                break
            if not progress:
                raise HTTPException(
                    status_code=400,
                    detail=f"Preview fields span tables with no inferred relationship: {', '.join(sorted(pending))}",
                )
        return joined

    preview_visuals = []
    for visual in report_spec["visuals"]:
        visual_type = visual.get("type")
        if visual_type == "slicer":
            field = visual["field"]
            options = sorted({str(row[field["field"]]) for row in filtered_rows(field["table"], field["field"]) if row.get(field["field"]) is not None})
            preview_visuals.append({**visual, "data": [], "options": options, "filterKey": f"{field['table']}.{field['field']}"})
            continue
        if visual_type in {"card", "multiRowCard", "kpi", "gauge"}:
            value = visual["value"]
            total = measure_total(filtered_rows(value["table"]), value["table"], value["field"])
            preview_visuals.append({**visual, "data": [{"label": value["field"], "value": total}]})
            continue
        if visual_type == "table":
            row_fields = visual["rows"]
            values = visual.get("values", [])
            table_name = row_fields[0]["table"]
            source_rows = filtered_rows(table_name)
            groups: dict[str, tuple[list, list[dict]]] = {}
            truncated = False
            for row in source_rows:
                cells = [row.get(field["field"]) for field in row_fields]
                key = json.dumps(cells, default=str, ensure_ascii=False)
                if key not in groups:
                    if len(groups) >= MAX_PREVIEW_POINTS:
                        truncated = True
                        continue
                    groups[key] = (cells, [])
                groups[key][1].append(row)
            table_rows = [
                cells + [measure_total(group, table_name, value["field"]) for value in values]
                for cells, group in groups.values()
            ]
            data = totals(source_rows, table_name, row_fields[0]["field"], values[0]["field"]) if values else []
            preview = {**visual, "data": data, "tableRows": table_rows}
            if truncated:
                preview["previewNotice"] = f"Table preview shows the first {MAX_PREVIEW_POINTS} distinct rows."
            preview_visuals.append(preview)
            continue
        if visual_type == "pivotTable":
            row_field = visual["rows"][0]
            value = visual["values"][0]
            if row_field["table"] == value["table"]:
                data = totals(
                    filtered_rows(row_field["table"]),
                    row_field["table"],
                    row_field["field"],
                    value["field"],
                )
            else:
                rows = joined_rows([row_field["table"], value["table"]])
                definition = metadata["tables"][value["table"]].get("measureDefinitions", {}).get(value["field"], {})
                measure_column = definition.get("column", value["field"].removeprefix("Total "))
                filter_column = definition.get("filterColumn")
                aggregation = definition.get("aggregation", "SUM")
                grouped: dict[str, list[float]] = {}
                for row in rows:
                    label = row.get((row_field["table"], row_field["field"]))
                    amount = row.get((value["table"], measure_column))
                    if label is None or amount is None:
                        continue
                    if filter_column and row.get((value["table"], filter_column)) != definition.get("filterValue"):
                        continue
                    try:
                        grouped.setdefault(str(label), []).append(float(amount))
                    except (TypeError, ValueError):
                        continue
                def aggregate(values: list[float]) -> float:
                    if aggregation == "AVERAGE":
                        return sum(values) / len(values)
                    if aggregation == "MIN":
                        return min(values)
                    if aggregation == "MAX":
                        return max(values)
                    if aggregation == "COUNT":
                        return float(len(values))
                    return sum(values)
                data = [
                    {"label": label, "value": aggregate(values)}
                    for label, values in sorted(grouped.items())
                ]
            preview_visuals.append({**visual, "data": data})
            continue
        category = visual["category"]
        value = visual.get("value") or (visual.get("values") or [None])[0]
        if not value:
            raise HTTPException(status_code=400, detail="The visual does not include a measure to preview")
        table_name = category["table"]
        value_table_name = value["table"]
        measure_definition = metadata["tables"][value_table_name].get("measureDefinitions", {}).get(value["field"])
        measure_column = measure_definition["column"] if measure_definition else value["field"].removeprefix("Total ")
        available_columns = metadata["tables"].get(table_name, {}).get("columns", [])
        measure_columns = metadata["tables"].get(value_table_name, {}).get("columns", [])
        legend_fields = ([visual["legend"]] if visual.get("legend") else []) + visual.get("legendBreakdown", [])
        if (
            category["field"] not in available_columns
            or measure_column not in measure_columns
            or any(
                legend_field["field"] not in metadata["tables"].get(legend_field["table"], {}).get("columns", [])
                for legend_field in legend_fields
            )
        ):
            raise HTTPException(status_code=400, detail="The planned fields could not be matched to uploaded data")

        comparison_fields = visual.get("values", []) if visual.get("comparison") else []
        involved_tables = [
            table_name,
            value_table_name,
            *(field["table"] for field in comparison_fields),
            *(field["table"] for field in legend_fields),
        ]
        if len(set(involved_tables)) > 1:
            contexts = joined_rows(involved_tables)

            def cross_table_totals(
                rows: list[dict[tuple[str, str], object]],
                measure_field: dict[str, str],
                legend_value: tuple[str, ...] | None = None,
            ) -> list[dict[str, object]]:
                measure_table = measure_field["table"]
                measure_definition = metadata["tables"][measure_table].get("measureDefinitions", {}).get(
                    measure_field["field"], {}
                )
                measure_column = measure_definition.get(
                    "column", measure_field["field"].removeprefix("Total ")
                )
                filter_column = measure_definition.get("filterColumn")
                aggregation = measure_definition.get("aggregation", "SUM")
                grouped: dict[str, list[float]] = {}
                for row in rows:
                    category_value = row.get((table_name, category["field"]))
                    measure_value = row.get((measure_table, measure_column))
                    if category_value is None or measure_value is None:
                        continue
                    if filter_column and row.get((measure_table, filter_column)) != measure_definition.get("filterValue"):
                        continue
                    if legend_value is not None and tuple(
                        str(row.get((field["table"], field["field"])))
                        for field in legend_fields
                    ) != legend_value:
                        continue
                    try:
                        grouped.setdefault(str(category_value), []).append(float(measure_value))
                    except (TypeError, ValueError):
                        continue

                def aggregate(values: list[float]) -> float:
                    if aggregation == "AVERAGE":
                        return sum(values) / len(values)
                    if aggregation == "MIN":
                        return min(values)
                    if aggregation == "MAX":
                        return max(values)
                    if aggregation == "COUNT":
                        return float(len(values))
                    return sum(values)

                return sorted(
                    [{"label": label, "value": aggregate(values)} for label, values in grouped.items()],
                    key=lambda point: point["label"] if visual_type == "lineChart" else point["value"],
                    reverse=visual_type != "lineChart",
                )[:200 if visual_type == "lineChart" else MAX_PREVIEW_POINTS]

            if visual.get("comparison"):
                series = [
                    {"name": measure_field["field"], "data": cross_table_totals(contexts, measure_field)}
                    for measure_field in comparison_fields
                ]
                preview_visuals.append({**visual, "data": series[0]["data"], "series": series})
                continue

            data = cross_table_totals(contexts, value)
            if visual_type == "lineChart":
                data.sort(key=lambda point: point["label"])
            if legend_fields:
                legend_values = sorted({
                    tuple(str(row.get((field["table"], field["field"]))) for field in legend_fields)
                    for row in contexts
                    if all(row.get((field["table"], field["field"])) is not None for field in legend_fields)
                })
                series = []
                for legend_value in legend_values:
                    series_data = cross_table_totals(contexts, value, legend_value)
                    if visual_type == "lineChart":
                        series_data.sort(key=lambda point: point["label"])
                    series.append({"name": " · ".join(legend_value), "data": series_data})
                preview_visuals.append({**visual, "data": data, "series": series})
            else:
                preview_visuals.append({**visual, "data": data})
            continue

        rows = raw_tables.get(table_name, {}).get("rows", [])
        selected_rows = filtered_rows(category["table"])
        data = totals(selected_rows, table_name, category["field"], value["field"], chronological=visual_type == "lineChart")
        if visual_type == "lineChart":
            data.sort(key=lambda point: point["label"])
        if visual.get("comparison"):
            series = [{"name": measure["field"], "data": totals(selected_rows, table_name, category["field"], measure["field"], chronological=visual_type == "lineChart")} for measure in visual["values"]]
            preview_visuals.append({**visual, "data": data, "series": series})
        elif legend_fields:
            legend_values = sorted({
                tuple(str(row[field["field"]]) for field in legend_fields)
                for row in selected_rows
                if all(row.get(field["field"]) is not None for field in legend_fields)
            })
            series = []
            for legend_value in legend_values:
                legend_rows = [
                    row for row in selected_rows
                    if tuple(str(row.get(field["field"])) for field in legend_fields) == legend_value
                ]
                series_data = totals(legend_rows, table_name, category["field"], value["field"], chronological=visual_type == "lineChart")
                if visual_type == "lineChart":
                    series_data.sort(key=lambda point: point["label"])
                series.append({"name": " · ".join(legend_value), "data": series_data})
            preview_visuals.append({**visual, "data": data, "series": series})
        else:
            preview_visuals.append({**visual, "data": data})

    for visual in preview_visuals:
        ranking = visual.get("ranking")
        if isinstance(ranking, dict) and ranking.get("direction") in {"top", "bottom"}:
            count = ranking.get("count")
            if isinstance(count, int) and 1 <= count <= MAX_RANKING_COUNT:
                reverse = ranking["direction"] == "top"
                available_count = len(visual["data"])
                visual["data"] = sorted(visual["data"], key=lambda point: point["value"], reverse=reverse)[:count]
                if available_count < count:
                    visual["previewNotice"] = (
                        f"Requested {ranking['direction']} {count}; only {available_count} distinct categories are available."
                    )
                if visual.get("series"):
                    for series in visual["series"]:
                        series["data"] = sorted(series["data"], key=lambda point: point["value"], reverse=reverse)[:count]
    for visual in preview_visuals:
        if visual.get("type") == "lineChart":
            visual["previewLimit"] = 200
            visual["previewNotice"] = "Line previews show up to the first 200 chronological points per series."
    theme = build_theme(normalize_design_spec(report_spec))
    return {
        "page": report_spec["page"],
        "visuals": preview_visuals,
        "previewTheme": {
            "dataColors": theme["dataColors"],
            "background": theme["background"],
            "backgroundLight": theme["backgroundLight"],
            "foreground": theme["foreground"],
            "foregroundNeutralSecondary": theme["foregroundNeutralSecondary"],
            "tableAccent": theme["tableAccent"],
        },
    }


@router.post("/edit")
async def edit_report(payload: dict) -> dict:
    source_id = payload.get("sourceId")
    current = payload.get("reportSpec")
    instruction = payload.get("instruction")
    if not isinstance(source_id, str) or not isinstance(current, dict) or not isinstance(instruction, str) or not instruction.strip():
        raise HTTPException(status_code=400, detail="sourceId, reportSpec, and instruction are required")
    try:
        metadata = load_source_metadata(source_id)
        validate_report_spec(current, metadata)
        from backend.agents.graph_workflow import execute_agent_action
        proposal = await execute_agent_action("edit_report", source_id, instruction.strip(), {
            "reportSpec":current,"conversation":payload.get("conversation",[]),
            "clarificationAnswer":payload.get("clarificationAnswer")})
        revised, changes = proposal["reportSpec"], proposal["changes"]
        validate_report_spec(revised, metadata)
    except AIServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        from backend.services.ai_analysis_service import AnalysisClarificationRequired
        if isinstance(exc, AnalysisClarificationRequired):
            raise HTTPException(status_code=409, detail={"status":"needs_clarification","question":exc.question,"options":exc.options}) from exc
        raise
    write_ai_audit_event(
        source_id=source_id, operation="conversational_report_edit", provider="azure_openai", model=get_settings().azure_openai_deployment,
        outcome="succeeded", validation_result="passed", decisions=dashboard_decisions(revised),
        context={"instruction": instruction, "changes": changes},
    )
    return {"reportSpec": revised, "changes": changes, "message": "Edit proposed. Review the preview, then generate the PBIP."}


@router.post("/{report_id}/repair")
async def repair_report(report_id: str, payload: dict) -> dict:
    """Propose a reviewable repair for unsupported slicer propagation."""
    entry = _report_entry(report_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Report not found")
    current = payload.get("reportSpec")
    saved = entry.get("reportSpec")
    approved = entry.get("approvedReportSpec", saved)
    if not isinstance(current, dict) or not isinstance(saved, dict) or not isinstance(approved, dict):
        raise HTTPException(status_code=409, detail="The report plan is unavailable. Generate and validate the current plan first.")
    try:
        matches = _normalized_spec_fingerprint(current) == _normalized_spec_fingerprint(approved)
        generated_matches = _normalized_spec_fingerprint(saved) == _normalized_spec_fingerprint(approved)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"The report plan is invalid: {exc}") from exc
    if not matches:
        raise HTTPException(status_code=409, detail="The report plan has changed since validation. Generate and validate the current plan first.")
    if not generated_matches:
        raise HTTPException(status_code=409, detail="Generated visuals differ from the approved plan. Review the report and edit it before regenerating.")
    validation = entry.get("validation", {})
    accuracy = validation.get("calculationAccuracy", {})
    checks = accuracy.get("checks", [])
    failures = [check for check in checks if isinstance(check, dict) and check.get("passed") is False]
    if (entry.get("status") != "failed" or not failures
        or any(check.get("type") != "filterPropagation" for check in failures)
        or len(validation.get("errors", [])) != len(failures)
        or len(accuracy.get("errors", [])) != len(failures)):
        raise HTTPException(status_code=409, detail="Automatic repair is only available when the validation failures are incompatible slicers. Review the validation details and edit the report plan.")
    try:
        metadata = load_source_metadata(entry["sourceId"])
        validate_report_spec(saved, metadata)
        pages = report_pages(saved)
        changes = []
        repaired_pages = []
        for page in pages:
            removed = set(incompatible_slicer_indexes(page, metadata))
            for index in sorted(removed):
                field = page["visuals"][index]["field"]
                affected = "all visuals" if any(
                    visual.get("type") == "table" and not visual.get("values")
                    for visual in page["visuals"]
                ) else "all measures"
                changes.append(f"Removed slicer {field['table']}[{field['field']}] from {page['page']}; it cannot filter {affected} on that page.")
            repaired_pages.append({**page, "visuals": [
                visual for index, visual in enumerate(page["visuals"]) if index not in removed
            ]})
        if not changes:
            raise ValueError("The incompatible slicers could not be identified in the current model. Review the relationships and report plan.")
        revised = pack_pages(repaired_pages)
        validate_report_spec(revised, metadata)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"Could not prepare a repair: {exc}") from exc
    return {"reportSpec": revised, "changes": changes}


@router.post("/generate")
async def generate_report(payload: dict) -> dict:
    started = time.perf_counter()
    source_id = payload.get("sourceId")
    report_spec = payload.get("reportSpec") or {}
    user_request = payload.get("userRequest")
    conversation = payload.get("conversation")
    if not source_id:
        raise HTTPException(status_code=400, detail="sourceId is required")
    if not report_spec:
        raise HTTPException(status_code=400, detail="reportSpec is required; create and review a plan before generation")
    if user_request is not None and (
        not isinstance(user_request, str) or len(user_request) > MAX_MESSAGE_CHARACTERS
    ):
        raise HTTPException(
            status_code=400,
            detail=f"userRequest must be a string no longer than {MAX_MESSAGE_CHARACTERS} characters",
        )
    if conversation is not None and (
        not isinstance(conversation, list) or len(conversation) > MAX_CONVERSATION_MESSAGES
        or any(not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}
               or not isinstance(item.get("text"), str)
               or len(item["text"]) > MAX_MESSAGE_CHARACTERS for item in conversation)
    ):
        raise HTTPException(
            status_code=400,
            detail=f"conversation must contain up to {MAX_CONVERSATION_MESSAGES} user or assistant text messages",
        )

    try:
        source_payload = load_source_payload(source_id)
        metadata = load_source_metadata(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    source_type = source_payload.get("sourceType")
    if source_type not in {"excel", "sql"}:
        raise HTTPException(status_code=400, detail="PBIP generation supports Excel or materialized SQL sources")
    workbook_path = Path(source_payload.get("path", "missing.xlsx")).resolve()
    if source_type == "excel" and not workbook_path.is_file():
        raise HTTPException(status_code=400, detail="The uploaded Excel workbook is no longer available")
    if source_type == "sql" and any(not isinstance(table.get("rows"), list) for table in source_payload.get("metadata", {}).get("tables", {}).values()):
        raise HTTPException(status_code=409, detail="Materialize the selected SQL tables before generating a PBIP")
    report_id = f"report-{uuid.uuid4().hex[:8]}"
    base_dir = APP_DATA_ROOT / "reports"
    report_dir = base_dir / f"{report_id}.Report"
    target_model_dir = base_dir / f"{report_id}.SemanticModel"
    try:
        validate_report_spec(report_spec, metadata)
        binding_tables = {binding["table"] for visual in dashboard_decisions(report_spec)["visuals"]
                          for binding in visual["bindings"]}
        validate_parsing_review(source_payload, binding_tables)
        if source_payload.get("approval", {}).get("planFingerprint") != _spec_fingerprint(report_spec):
            raise HTTPException(status_code=409, detail="Human approval for this exact source and report plan is required before generation")
        approved_report_spec = deepcopy(report_spec)
        ensure_report_layout(report_dir, f"../{target_model_dir.name}")
        source_metadata = source_payload.get("metadata", {})
        # Both Excel and materialized SQL use the same deterministic semantic-model writer.
        excel_to_semantic_model(workbook_path, source_metadata, target_model_dir)
        report_relative_path = report_dir.relative_to(APP_DATA_ROOT)
        checks = []
        errors = []
        pages = report_pages(report_spec)
        for page in pages:
            create_pbir_project(report_relative_path, page, metadata, APP_DATA_ROOT)
            preview_snapshot = await preview_report({"sourceId": source_id, "reportSpec": page, "filters": {}})
            accuracy = validate_calculation_accuracy(page, metadata, source_payload, target_model_dir, preview_snapshot)
            checks.extend({**check, "page": page["page"]} for check in accuracy["checks"])
            errors.extend(f"{page['page']}: {error}" for error in accuracy["errors"])
        if len(pages) > 1:
            index = report_dir / "definition" / "pages" / "pages.json"
            manifest = json.loads(index.read_text(encoding="utf-8"))
            manifest["activePageName"] = manifest["pageOrder"][0]
            index.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        validation = validate_generated_report(report_relative_path, metadata, APP_DATA_ROOT)
        calculation_accuracy = {**accuracy, "passed": not errors, "checks": checks, "errors": errors}
        validation["calculationAccuracy"] = calculation_accuracy
        validation["calculationParity"] = calculation_accuracy
        validation.setdefault("errors", []).extend(calculation_accuracy["errors"])
        validation["passed"] = bool(validation["passed"] and calculation_accuracy["passed"])
        create_pbip_project_file(report_relative_path, APP_DATA_ROOT)
    except HTTPException:
        raise
    except (OSError, ValueError) as exc:
        write_ai_audit_event(
            source_id=source_id, operation="report_generation", provider="generator", model="pbir",
            outcome="failed", validation_result="failed", failure=failure_details(exc),
            context={"report_id": report_id,
                     "latency_ms": round((time.perf_counter() - started) * 1000, 2)},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    REPORTS[report_id] = {
        "reportId": report_id,
        "sourceId": source_id,
        "sourceName": source_payload.get("sourceName"),
        "sourceType": source_type,
        "ownerId": current_user_id(),
        "status": "generated" if validation["passed"] else "failed",
        "validation": validation,
        "reportPath": str(report_dir.relative_to(APP_DATA_ROOT)),
        "reportSpec": pack_pages(pages),
        "approvedReportSpec": approved_report_spec,
    }
    manifest = base_dir / f"{report_id}.json"
    temporary_manifest = base_dir / f"{report_id}.json.tmp"
    temporary_manifest.write_text(json.dumps(REPORTS[report_id], indent=2), encoding="utf-8")
    temporary_manifest.replace(manifest)
    write_report_audit_document(
        report_id=report_id,
        source_id=source_id,
        report_spec=report_spec,
        validation=validation,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
        user_request=user_request,
        conversation=conversation,
        source_metadata=metadata,
    )
    write_ai_audit_event(
        source_id=source_id, operation="report_generation", provider="generator", model="pbir",
        outcome="succeeded" if validation["passed"] else "failed",
        validation_result="passed" if validation["passed"] else "failed",
        decisions=dashboard_decisions(report_spec),
        context={"report_id": report_id, "validation": validation,
                 "latency_ms": round((time.perf_counter() - started) * 1000, 2)},
    )
    logger.info("Report generation completed", extra={
        "event": "report.generated", "source_id": source_id, "report_id": report_id,
        "visual_count": sum(len(p["visuals"]) for p in pages), "success": validation["passed"],
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    })

    return {"reportId": report_id, "status": "generated" if validation["passed"] else "failed", "validation": validation}


@router.get("/{report_id}/status")
async def get_status(report_id: str) -> dict:
    entry = _report_entry(report_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Report not found")
    return {"reportId": report_id, "status": entry["status"], "message": "Report generated and validated successfully." if entry["status"] == "generated" else "Report validation failed.", "validation": entry["validation"]}


@router.get("/{report_id}/conversation")
async def get_report_conversation(report_id: str) -> dict:
    entry = _report_entry(report_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Report not found")
    audit_path = ai_audit_service.AUDIT_FILE_PATH.parent / "reports" / f"{report_id}.json"
    if not audit_path.exists():
        return {"available": False, "conversation": []}
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if not isinstance(audit, dict):
            raise ValueError("Invalid report audit")
        conversation = audit["conversation"]
        if (audit.get("report_id") != report_id or audit.get("source_id") != entry.get("sourceId")
                or not isinstance(conversation, list)
                or any(not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}
                       or not isinstance(message.get("text"), str) for message in conversation)):
            raise ValueError("Invalid report conversation")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        logger.warning("Report conversation could not be loaded", extra={
            "event": "report.conversation.invalid", "report_id": report_id,
        })
        raise HTTPException(status_code=500, detail="Report conversation could not be loaded") from exc
    return {"available": bool(conversation), "conversation": [
        {"role": message["role"], "text": message["text"]} for message in conversation
    ]}


@router.get("/{report_id}/download")
async def download_report(report_id: str) -> FileResponse:
    entry = _report_entry(report_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Report not found")
    if entry["status"] != "generated" or not entry.get("validation", {}).get("passed"):
        raise HTTPException(status_code=409, detail="Download is blocked until the report passes the quality gate")
    report_path = _resolved_report_path(report_id, entry["reportPath"])
    if report_path is None:
        raise HTTPException(status_code=404, detail="Report not found")
    zip_path = zip_report(report_path.relative_to(APP_DATA_ROOT), APP_DATA_ROOT)
    return FileResponse(zip_path, media_type="application/zip", filename=f"{report_id}.pbip.zip")


@router.post("/{report_id}/open")
async def open_report_in_power_bi(report_id: str) -> dict:
    entry = _report_entry(report_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Report not found")
    if entry["status"] != "generated":
        raise HTTPException(status_code=400, detail="Report must pass validation before it can be opened")
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="Opening Power BI Desktop is only supported on Windows")

    report_path = _resolved_report_path(report_id, entry["reportPath"])
    if report_path is None:
        raise HTTPException(status_code=404, detail="Report not found")
    project_path = report_path.parent / f"{report_id}.pbip"
    if not project_path.is_file():
        raise HTTPException(status_code=404, detail="Generated PBIP project file was not found")
    try:
        project = validate_pbip_project_file(project_path, APP_DATA_ROOT)
        os.startfile(str(project["projectPath"]))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"PBIP project is not safe to open: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not open the PBIP project: {exc}") from exc
    return {"message": "PBIP project opened in Power BI Desktop"}
