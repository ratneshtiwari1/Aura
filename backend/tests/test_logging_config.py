import json
import logging
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from backend.logging_config import (
    JsonFormatter,
    RequestContextFilter,
    agent_run_id_context,
    configure_logging,
    request_id_context,
    trace_id_context,
)


def test_json_formatter_emits_single_line_structured_log() -> None:
    record = logging.LogRecord(
        name="autobi.http",
        level=logging.INFO,
        pathname=__file__,
        lineno=12,
        msg="Request completed",
        args=(),
        exc_info=None,
    )
    record.created = datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp()
    record.request_id = "request-123"
    record.method = "GET"
    record.path = "/health"
    record.status_code = 200
    record.duration_ms = 1.25
    record.event = "request.completed"

    with patch.dict("os.environ", {}, clear=True):
        formatted = JsonFormatter().format(record)
    payload = json.loads(formatted)

    assert "\n" not in formatted
    assert payload["timestamp"] == "2026-01-02T00:00:00.000Z"
    assert payload["level"] == "INFO"
    assert payload["service"] == "autobi-api"
    assert payload["environment"] == "dev"
    assert payload["request_id"] == "request-123"
    assert payload["status_code"] == 200
    assert payload["event"] == "request.completed"


def test_json_formatter_omits_workflow_stage_fields() -> None:
    record = logging.LogRecord(
        name="autobi.report",
        level=logging.INFO,
        pathname=__file__,
        lineno=12,
        msg="Report generated",
        args=(),
        exc_info=None,
    )
    record.event = "report.generated"
    record.operation = "report_generation"

    payload = json.loads(JsonFormatter().format(record))

    assert "workflow_stage" not in payload
    assert "workflow_stage_number" not in payload
    assert "workflow_stage_total" not in payload
    assert "workflow_status" not in payload


def test_json_formatter_shortens_development_environment(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    record = logging.LogRecord("autobi.http", logging.INFO, __file__, 1, "ready", (), None)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["environment"] == "dev"


def test_invalid_log_level_fails_with_clear_configuration_error(monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")

    with pytest.raises(ValueError, match="Invalid LOG_LEVEL"):
        configure_logging()


def test_request_context_filter_attaches_correlation_id() -> None:
    request_token = request_id_context.set("request-context-123")
    trace_token = trace_id_context.set("trace-context-123")
    run_token = agent_run_id_context.set("run-context-123")
    try:
        record = logging.LogRecord("autobi.service", logging.INFO, __file__, 1, "inside request", (), None)
        assert RequestContextFilter().filter(record)
    finally:
        agent_run_id_context.reset(run_token)
        trace_id_context.reset(trace_token)
        request_id_context.reset(request_token)

    assert record.request_id == "request-context-123"
    assert record.trace_id == "trace-context-123"
    assert record.agent_run_id == "run-context-123"


def test_logging_configuration_routes_uvicorn_through_json_root(monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    with patch("backend.logging_config.dictConfig") as dict_config:
        configure_logging()

    config = dict_config.call_args.args[0]
    assert config["handlers"]["console"]["stream"] == "ext://sys.stdout"
    assert config["root"]["level"] == "DEBUG"
    assert config["handlers"]["console"]["filters"] == ["request_context"]
    assert config["loggers"]["uvicorn"]["propagate"] is True
    assert config["loggers"]["uvicorn.access"]["disabled"] is True
