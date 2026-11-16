from __future__ import annotations

import logging
import re
import time
from uuid import uuid4
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.api.datasource import router as datasource_router
from backend.api.report import router as report_router
from backend.api.analysis import router as analysis_router
from backend.api.agent import router as agent_router
from backend.config import get_settings
from backend.logging_config import (
    agent_run_id_context,
    chat_session_id_context,
    configure_logging,
    request_id_context,
    trace_id_context,
)
from backend.services.ai_audit_service import ai_metrics_context
from backend.services.identity_service import authenticate, current_user_id, identity_context

configure_logging()
logger = logging.getLogger("autobi.http")
settings = get_settings()
logger.info(
    "AI planning mode configured",
    extra={
        "event": "ai.mode.configured",
        "ai_enabled": settings.use_ai,
        "model_name": settings.azure_openai_deployment if settings.use_ai and settings.ai_provider == "azure_openai" else settings.ai_provider,
    },
)
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
_TRACEPARENT_PATTERN = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})\Z", re.IGNORECASE)
_AGENT_RUN_ID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)

app = FastAPI(title="AURA — AI-driven Understanding & Reporting Accelerator", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(datasource_router)
app.include_router(report_router)
app.include_router(analysis_router)
app.include_router(agent_router)
from backend.api.usage import router as usage_router
app.include_router(usage_router)


@app.middleware("http")
async def log_request(request: Request, call_next):
    """Log each request without recording request bodies or query-string values."""
    supplied_request_id = request.headers.get("x-request-id", "")
    request_id = supplied_request_id if _REQUEST_ID_PATTERN.fullmatch(supplied_request_id) else str(uuid4())
    supplied_traceparent = request.headers.get("traceparent", "")
    traceparent_match = _TRACEPARENT_PATTERN.fullmatch(supplied_traceparent)
    trace_id = (
        traceparent_match.group(1).lower()
        if traceparent_match and traceparent_match.group(1) != "0" * 32
        and traceparent_match.group(2) != "0" * 16
        else uuid4().hex
    )
    supplied_agent_run_id = request.headers.get("x-agent-run-id", "")
    supplied_chat_session_id = request.headers.get("x-aura-chat-id", "")
    if supplied_chat_session_id and not _AGENT_RUN_ID_PATTERN.fullmatch(supplied_chat_session_id):
        return JSONResponse(status_code=400, content={"detail": "Invalid chat session ID"})
    agent_run_id = (
        supplied_agent_run_id.lower()
        if _AGENT_RUN_ID_PATTERN.fullmatch(supplied_agent_run_id)
        else str(uuid4())
    )
    try:
        user_id = authenticate(
            request.headers.get("authorization"),
            request.headers.get("x-aura-user") or request.headers.get("x-autobi-user"),
        )
    except PermissionError as error:
        return JSONResponse(status_code=401, content={"detail": str(error)})
    request.state.request_id = request_id
    request.state.user_id = user_id
    request.state.trace_id = trace_id
    request.state.agent_run_id = agent_run_id
    request_context_token = request_id_context.set(request_id)
    trace_context_token = trace_id_context.set(trace_id)
    agent_run_context_token = agent_run_id_context.set(agent_run_id)
    chat_session_context_token = chat_session_id_context.set(supplied_chat_session_id.lower() or None)
    metrics_context_token = ai_metrics_context.set(None)
    identity_context_token = identity_context.set(user_id)
    started_at = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except PermissionError as error:
            return JSONResponse(
                status_code=403,
                content={"detail": str(error), "requestId": request_id},
                headers={"x-request-id": request_id, "x-trace-id": trace_id},
            )
        except Exception as error:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            logger.exception(
                "Request failed",
                extra={
                    "event": "request.failed",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": 500,
                    "duration_ms": duration_ms,
                    "exception_type": type(error).__name__,
                },
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error", "requestId": request_id},
                headers={
                    "x-request-id": request_id,
                    "x-trace-id": trace_id,
                    "x-agent-run-id": agent_run_id,
                },
            )

        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        response.headers["x-request-id"] = request_id
        response.headers["x-trace-id"] = trace_id
        response.headers["x-agent-run-id"] = agent_run_id
        status_code = response.status_code
        level = logging.ERROR if status_code >= 500 else logging.WARNING if status_code >= 400 else logging.INFO
        event = "request.failed" if status_code >= 500 else "request.completed"
        logger.log(
            level,
            "Request failed" if status_code >= 500 else "Request completed",
            extra={
                "event": event,
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
    finally:
        identity_context.reset(identity_context_token)
        ai_metrics_context.reset(metrics_context_token)
        agent_run_id_context.reset(agent_run_context_token)
        chat_session_id_context.reset(chat_session_context_token)
        trace_id_context.reset(trace_context_token)
        request_id_context.reset(request_context_token)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/identity")
def get_identity() -> dict[str, str | bool]:
    user_id = current_user_id()
    return {"userId": user_id, "isDemo": user_id == "demo-user"}


# The archive includes a built frontend. Serve it after all API routes.
frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if (frontend_dist / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def workspace():
        return FileResponse(frontend_dist / "index.html")
