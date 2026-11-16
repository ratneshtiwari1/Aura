import asyncio
import json
import hashlib
import logging
import re
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Callable

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from automation.excel import ExcelAnalyzer, WorkbookAnalysisCancelled
from automation.ai.ai_service import (
    AIServiceError,
    get_ai_provider,
    validate_workbook_understanding,
)
from automation.semantic.workbook_profile import profile_for_ai
from backend.config import get_settings
from backend.services.ai_audit_service import failure_details, workbook_decisions, write_ai_audit_event
from backend.services.model_service import metadata_to_semantic_model, save_source_metadata, load_source_payload, _source_json_default, _source_json_object_hook
from backend.services.sql_service import discover_schema, materialize_tables, open_connection

router = APIRouter(prefix="/api/datasource", tags=["datasource"])
logger = logging.getLogger("autobi.ai")
_UPLOAD_CHUNK_BYTES = 1024 * 1024
_SQL_NUMERIC_TYPES = {
    "bigint", "int", "smallint", "tinyint", "decimal", "numeric",
    "money", "smallmoney", "float", "real",
}
_NON_ADDITIVE_WORDS = {"average", "avg", "margin", "percent", "percentage", "pct", "rate", "ratio", "share", "index"}
_IDENTIFIER_WORDS = {"id", "identifier", "key", "code", "number", "year"}


def _sql_measure_proposals(columns: list[dict]) -> list[dict[str, str]]:
    """Propose, but do not silently create, aggregations for SQL numeric fields."""
    proposals: list[dict[str, str]] = []
    for column in columns:
        name = str(column.get("name", "")).strip()
        data_type = str(column.get("dataType", "")).casefold()
        words = set(re.findall(r"[a-z0-9]+", name.casefold()))
        compact_name = re.sub(r"[^a-z0-9]", "", name.casefold())
        looks_like_identifier = bool(words & _IDENTIFIER_WORDS) or compact_name.endswith(("id", "key", "code"))
        if not name or data_type not in _SQL_NUMERIC_TYPES or looks_like_identifier:
            continue
        aggregation = "AVERAGE" if words & _NON_ADDITIVE_WORDS else "SUM"
        prefix = "Average" if aggregation == "AVERAGE" else "Total"
        proposals.append({
            "name": f"{prefix} {name}",
            "column": name,
            "aggregation": aggregation,
            "status": "proposal",
        })
    return proposals


@router.post("/excel")
async def upload_excel(file: UploadFile = File(...)) -> dict:
    return await _upload_excel(file)


async def _upload_excel(file: UploadFile, on_progress: Callable[[str], None] | None = None) -> dict:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Only .xlsx Excel files are supported")

    settings = get_settings()
    source_id = f"src-{uuid.uuid4().hex[:8]}"
    storage_dir = settings.app_data_dir / "uploads"
    storage_dir.mkdir(parents=True, exist_ok=True)
    uploaded_path = storage_dir / f"{source_id}.xlsx"

    uploaded_bytes = 0
    signature = bytearray()
    try:
        with uploaded_path.open("wb") as destination:
            while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
                uploaded_bytes += len(chunk)
                if uploaded_bytes > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Excel file exceeds the configured {settings.max_upload_bytes}-byte upload limit",
                    )
                if len(signature) < 4:
                    signature.extend(chunk[: 4 - len(signature)])
                destination.write(chunk)
    except HTTPException:
        uploaded_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        uploaded_path.unlink(missing_ok=True)
        logger.exception(
            "Excel upload could not be stored",
            extra={"event": "datasource.excel.store_failed", "source_id": source_id},
        )
        raise HTTPException(status_code=500, detail="Unable to store the uploaded Excel file") from exc
    finally:
        await file.close()

    if bytes(signature) != b"PK\x03\x04" or not zipfile.is_zipfile(uploaded_path):
        uploaded_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid .xlsx workbook")

    profile_key = hashlib.sha256(uploaded_path.read_bytes()).hexdigest()
    profile_cache = settings.app_data_dir / "profiles" / f"v4-{profile_key}.json"
    cache_hit = profile_cache.is_file()
    try:
        if cache_hit:
            if on_progress:
                on_progress("loading_cached_profile")
            cached = json.loads(profile_cache.read_text(encoding="utf-8"), object_hook=_source_json_object_hook)
            metadata, workbook_profile = cached["metadata"], cached["workbookProfile"]
            workbook_analysis = None
        else:
            if on_progress:
                workbook_analysis = await asyncio.to_thread(ExcelAnalyzer().analyze, uploaded_path, on_progress)
            else:
                workbook_analysis = ExcelAnalyzer().analyze(uploaded_path)
            metadata = workbook_analysis.to_metadata()
            workbook_profile = workbook_analysis.to_dict()
            profile_cache.parent.mkdir(parents=True, exist_ok=True)
            profile_cache.write_text(json.dumps({"metadata": metadata, "workbookProfile": workbook_profile}, default=_source_json_default), encoding="utf-8")
        if not metadata.get("tables"):
            raise ValueError("No usable tables or data regions were found in the Excel workbook.")
    except WorkbookAnalysisCancelled:
        uploaded_path.unlink(missing_ok=True)
        raise
    except Exception as exc:  # pragma: no cover - defensive log path
        uploaded_path.unlink(missing_ok=True)
        logger.exception(
            "Excel workbook analysis failed",
            extra={"event": "datasource.excel.analysis_failed", "source_id": source_id},
        )
        raise HTTPException(
            status_code=400,
            detail="Unable to read this workbook. Verify that it contains a usable Excel table or data region.",
        ) from exc

    source = {
        "sourceType": "excel",
        "sourceName": file.filename.replace("\\", "/").rsplit("/", 1)[-1],
        "metadata": metadata,
        "workbookProfile": workbook_profile,
        "path": str(uploaded_path),
        "parsingReview": {
            table["name"]: {"reasons": table["review_reasons"], "confirmed": False,
                            "range": table["range"], "headerRow": table["header_row"]}
            for sheet in workbook_profile.get("sheets", []) for table in sheet.get("tables", [])
            if table.get("review_reasons") and not table.get("derived_from")
        },
    }
    ai_understanding = None
    if settings.use_ai:
        started = time.perf_counter()
        provider_name = settings.ai_provider
        model_name = settings.azure_openai_deployment if settings.ai_provider == "azure_openai" else "mock"
        try:
            if on_progress:
                on_progress("analyzing_workbook")
            provider = get_ai_provider(settings)
            provider_name = settings.ai_provider
            model_name = provider.model
            ai_understanding = validate_workbook_understanding(
                await provider.analyze_workbook(profile_for_ai(workbook_profile)),
                workbook_profile,
            )
            write_ai_audit_event(
                source_id=source_id,
                operation="workbook_understanding",
                provider=provider_name,
                model=model_name,
                outcome="succeeded",
                validation_result="passed",
                decisions=workbook_decisions(ai_understanding),
            )
            source["workbookProfile"] = workbook_profile
            source["aiUnderstanding"] = ai_understanding
            logger.info(
                "Workbook understanding completed",
                extra={
                    "event": "ai.operation.completed",
                    "operation": "workbook_understanding",
                    "model_name": provider.model,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "success": True,
                    "validation_result": "passed",
                    "retry_count": 0,
                },
            )
        except WorkbookAnalysisCancelled:
            uploaded_path.unlink(missing_ok=True)
            raise
        except AIServiceError as exc:
            write_ai_audit_event(
                source_id=source_id,
                operation="workbook_understanding",
                provider=provider_name,
                model=model_name,
                outcome="failed",
                validation_result="failed",
                failure=failure_details(exc),
            )
            uploaded_path.unlink(missing_ok=True)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            write_ai_audit_event(
                source_id=source_id,
                operation="workbook_understanding",
                provider=provider_name,
                model=model_name,
                outcome="failed",
                validation_result="failed",
                failure=failure_details(exc),
            )
            uploaded_path.unlink(missing_ok=True)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            write_ai_audit_event(
                source_id=source_id,
                operation="workbook_understanding",
                provider=provider_name,
                model=model_name,
                outcome="failed",
                validation_result="failed",
                failure=failure_details(exc),
            )
            uploaded_path.unlink(missing_ok=True)
            raise

    if on_progress:
        try:
            on_progress("finalizing")
        except WorkbookAnalysisCancelled:
            uploaded_path.unlink(missing_ok=True)
            raise
    save_source_metadata(source_id, source)

    warnings = list(workbook_profile.get("warnings", []))

    return {"sourceId": source_id, "sourceType": "excel", "profileCacheHit": cache_hit,
            "formulaCellCount": workbook_profile.get("workbook", {}).get("formula_count"), "sheets": [
        {
            "name": table_name,
            "columns": [
                {key: value for key, value in column.items() if key != "formulaExpressions"}
                for column in table.get("columns", [])
            ],
            "sampleRows": table.get("sampleRows", []),
            "metricValues": table.get("metricValues", []),
            "sourceRange": table.get("sourceRange"),
            "sourceSheet": table.get("sourceSheet"),
            "parsingReview": source["parsingReview"].get(table.get("derivedFrom") or table_name),
        }
        for table_name, table in metadata.get("tables", {}).items()
    ], "aiEnabled": settings.use_ai, "aiAnalyzed": ai_understanding is not None, "warnings": warnings}


@router.post("/excel/stream")
async def upload_excel_stream(file: UploadFile = File(...)) -> StreamingResponse:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Only .xlsx Excel files are supported")

    temporary = tempfile.SpooledTemporaryFile(max_size=_UPLOAD_CHUNK_BYTES)
    try:
        size = 0
        while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
            size += len(chunk)
            if size > get_settings().max_upload_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Excel file exceeds the configured {get_settings().max_upload_bytes}-byte upload limit",
                )
            temporary.write(chunk)
        temporary.seek(0)
    except BaseException:
        temporary.close()
        raise
    finally:
        await file.close()

    copied_file = UploadFile(file=temporary, filename=file.filename)

    async def events():
        queue: asyncio.Queue[dict] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        cancelled = threading.Event()
        loop_thread = threading.get_ident()

        def report(stage: str) -> None:
            if cancelled.is_set():
                raise WorkbookAnalysisCancelled()
            event = {"type": "progress", "stage": stage}
            if threading.get_ident() == loop_thread:
                queue.put_nowait(event)
            else:
                loop.call_soon_threadsafe(queue.put_nowait, event)

        async def run_upload() -> None:
            try:
                result = await _upload_excel(copied_file, report)
                await queue.put({"type": "result", "result": result})
            except HTTPException as exc:
                await queue.put({"type": "error", "detail": str(exc.detail)})
            except WorkbookAnalysisCancelled:
                pass
            except Exception:
                logger.exception("Streaming Excel upload failed", extra={"event": "datasource.excel.stream_failed"})
                await queue.put({"type": "error", "detail": "Excel upload failed. Please try again."})
            finally:
                await copied_file.close()
                await queue.put({"type": "end"})

        task = asyncio.create_task(run_upload())
        try:
            while True:
                event = await queue.get()
                if event["type"] == "end":
                    break
                yield json.dumps(event, default=_source_json_default) + "\n"
        finally:
            cancelled.set()
            if task.done():
                await task

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/excel/review")
async def review_excel_table(payload: dict) -> dict:
    """Confirm a detected source region after inspecting its headers and sample."""
    source_id, table_name = payload.get("sourceId"), payload.get("table")
    try:
        source = load_source_payload(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    table = source.get("metadata", {}).get("tables", {}).get(table_name) if isinstance(table_name, str) else None
    if table is None:
        raise HTTPException(status_code=400, detail="Selected table was not found")
    source_table = table.get("derivedFrom") or table_name
    review = source.get("parsingReview", {}).get(source_table)
    if review:
        if payload.get("range") != review["range"] or payload.get("headerRow") != review["headerRow"]:
            raise HTTPException(status_code=409, detail="The detected range or header changed; review it again")
        review["confirmed"] = True
        save_source_metadata(source_id, source)
        write_ai_audit_event(
            source_id=source_id, operation="parsing_review", provider="user", model="manual",
            outcome="succeeded", validation_result="confirmed",
            context={"table": source_table, "range": review["range"], "header_row": review["headerRow"]},
        )
    return {"confirmed": True}


@router.post("/sql/test")
async def sql_test(payload: dict) -> dict:
    server = payload.get("server", "")
    database = payload.get("database", "")
    if not server or not database:
        raise HTTPException(status_code=400, detail="Server and database are required")
    authentication = payload.get("authentication", "sql")
    if authentication == "sql" and (not payload.get("username") or not payload.get("password")):
        raise HTTPException(status_code=400, detail="Username and password are required for SQL authentication")
    try:
        connection_id = open_connection(payload)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unable to connect to SQL Server. Check the server, database, and authentication settings.") from exc
    return {"connected": True, "connectionId": connection_id}


@router.post("/sql/schema")
async def sql_schema(payload: dict) -> dict:
    connection_id = payload.get("connectionId")
    if not connection_id:
        raise HTTPException(status_code=400, detail="connectionId is required")
    try:
        tables = discover_schema(connection_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Unable to discover SQL Server schema.") from exc

    normalized_tables: dict[str, dict] = {}
    response_tables = []
    for table in tables:
        table_key = f"{table['schema']}.{table['name']}"
        columns = table["columns"]
        measure_proposals = _sql_measure_proposals(columns)
        normalized_tables[table_key] = {
            "name": table_key,
            "columns": columns,
            "measures": [],
            "measureProposals": measure_proposals,
        }
        response_tables.append({**table, "measureProposals": measure_proposals})

    source_id = f"src-{uuid.uuid4().hex[:8]}"
    metadata = {"tables": normalized_tables}
    save_source_metadata(source_id, {"sourceType": "sql", "metadata": metadata, "requiresApproval": True})
    model_dir = get_settings().app_data_dir / "models" / f"{source_id}.SemanticModel"
    metadata_to_semantic_model(metadata, model_dir)
    return {"sourceId": source_id, "tables": response_tables}


@router.post("/sql/materialize")
async def materialize_sql(payload: dict) -> dict:
    """Create a bounded SQL import snapshot that can be generated as a PBIP."""
    source_id, connection_id = payload.get("sourceId"), payload.get("connectionId")
    if not isinstance(source_id, str) or not isinstance(connection_id, str):
        raise HTTPException(status_code=400, detail="sourceId and connectionId are required")
    try:
        source = load_source_payload(source_id)
        discovered = discover_schema(connection_id)
        selected = payload.get("tables") or [f"{t['schema']}.{t['name']}" for t in discovered]
        selected_set = set(selected) if isinstance(selected, list) else set()
        tables = [table for table in discovered if f"{table['schema']}.{table['name']}" in selected_set]
        if not tables or len(tables) != len(selected_set):
            raise ValueError("Select one or more discovered SQL tables")
        rows = materialize_tables(connection_id, tables, int(payload.get("rowLimit", 10000)))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    source["metadata"]["tables"] = {
        name: {**table, "rows": rows[name]}
        for name, table in source["metadata"]["tables"].items()
        if name in rows
    }
    approved = set(payload.get("approvedMeasures", [])) if isinstance(payload.get("approvedMeasures", []), list) else set()
    for table_name, table in source["metadata"]["tables"].items():
        proposals = {f"{table_name}.{item['name']}": item for item in table.get("measureProposals", [])}
        selected_measures = [proposals[key] for key in approved if key in proposals]
        table["measures"] = [item["name"] for item in selected_measures]
        table["measureDefinitions"] = {
            item["name"]: {"column": item["column"], "aggregation": item["aggregation"]}
            for item in selected_measures
        }
    save_source_metadata(source_id, source)
    return {"sourceId": source_id, "materializedTables": sorted(rows), "requiresApproval": True}
