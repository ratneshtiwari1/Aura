"""JSON logging configuration for the API process."""

from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.config import dictConfig
from typing import Any


_STRUCTURED_FIELDS = (
    "request_id",
    "trace_id",
    "agent_run_id",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "exception_type",
    "event",
    "operation",
    "step",
    "attempt",
    "outcome",
    "failure_type",
    "model_name",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "success",
    "fallback_used",
    "validation_result",
    "retry_count",
    "prompt_version",
    "requested_visual_count",
    "ai_enabled",
    "source_id",
    "report_id",
    "audit_event_id",
    "visual_count",
    "workbook",
    "sheet",
    "table",
    "range",
    "confidence",
    "row_count",
    "column_count",
)
request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_id_context: ContextVar[str | None] = ContextVar("trace_id", default=None)
agent_run_id_context: ContextVar[str | None] = ContextVar("agent_run_id", default=None)
chat_session_id_context: ContextVar[str | None] = ContextVar("chat_session_id", default=None)


class RequestContextFilter(logging.Filter):
    """Attach the active request ID to any log emitted while handling a request."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            request_id = request_id_context.get()
            if request_id:
                record.request_id = request_id
        if not getattr(record, "trace_id", None):
            trace_id = trace_id_context.get()
            if trace_id:
                record.trace_id = trace_id
        if not getattr(record, "agent_run_id", None):
            agent_run_id = agent_run_id_context.get()
            if agent_run_id:
                record.agent_run_id = agent_run_id
        return True


class JsonFormatter(logging.Formatter):
    """Format each log record as one JSON object on a single line."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, timezone.utc)
        environment = os.getenv("APP_ENV", "dev")
        if environment == "development":
            environment = "dev"
        payload: dict[str, Any] = {
            "timestamp": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": "autobi-api",
            "environment": environment,
            "process_id": record.process,
        }
        for field in _STRUCTURED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=True, default=str)


def configure_logging() -> None:
    """Configure stdout JSON logging for application and Uvicorn records."""
    configured_level = os.getenv("LOG_LEVEL", "INFO").upper()
    level = logging.getLevelName(configured_level)
    if not isinstance(level, int):
        raise ValueError(f"Invalid LOG_LEVEL {configured_level!r}; use DEBUG, INFO, WARNING, ERROR, or CRITICAL")

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {"()": "backend.logging_config.JsonFormatter"},
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "json",
                    "filters": ["request_context"],
                },
            },
            "filters": {
                "request_context": {"()": "backend.logging_config.RequestContextFilter"},
            },
            "root": {
                "handlers": ["console"],
                "level": configured_level,
            },
            "loggers": {
                "autobi": {"level": configured_level, "propagate": True},
                "uvicorn": {"level": configured_level, "handlers": [], "propagate": True},
                "uvicorn.error": {"level": configured_level, "handlers": [], "propagate": True},
                "uvicorn.access": {"level": "CRITICAL", "handlers": [], "propagate": False, "disabled": True},
            },
        }
    )
