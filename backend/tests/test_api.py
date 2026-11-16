import io
import json
import logging
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import sql_service

client = TestClient(app)


def approve_plan(source_id: str, report_spec: dict) -> None:
    approval = client.post("/api/report/approve", json={"sourceId": source_id, "reportSpec": report_spec})
    assert approval.status_code == 200, approval.text


def enable_mock_ai(monkeypatch) -> None:
    from automation.ai.ai_service import MockProvider
    from backend.api import datasource as datasource_api
    from backend.api import report as report_api

    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setattr(datasource_api, "get_ai_provider", lambda settings: MockProvider())
    monkeypatch.setattr(report_api, "get_ai_provider", lambda settings: MockProvider())


def test_report_manifest_recovers_after_restart_and_rejects_external_paths(monkeypatch, tmp_path) -> None:
    from backend.api import report

    monkeypatch.setattr(report, "APP_ROOT", tmp_path)
    monkeypatch.setattr(report, "APP_DATA_ROOT", tmp_path / "backend" / ".app_data")
    monkeypatch.setattr(report, "REPORTS", {})
    reports = report.APP_DATA_ROOT / "reports"
    reports.mkdir(parents=True)
    report_id = "report-1234abcd"
    entry = {"reportId": report_id, "status": "generated", "validation": {"passed": True},
             "reportPath": f"backend/.app_data/reports/{report_id}.Report"}
    manifest = reports / f"{report_id}.json"
    manifest.write_text(json.dumps(entry), encoding="utf-8")
    assert report._report_entry(report_id) == entry
    report.REPORTS.clear()
    entry["reportPath"] = "../external.Report"
    manifest.write_text(json.dumps(entry), encoding="utf-8")
    assert report._report_entry(report_id) is None


@pytest.fixture(autouse=True)
def ai_disabled_by_default(monkeypatch) -> None:
    monkeypatch.setenv("USE_AI", "false")
    monkeypatch.setenv("AI_PROVIDER", "gemini")


def test_health_endpoint() -> None:
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'
    assert response.headers['x-request-id']


def test_recent_chat_rename_delete_preserves_published_report(monkeypatch, tmp_path) -> None:
    from backend.api import report as report_api
    from backend.services import ai_audit_service

    monkeypatch.setattr(report_api, "APP_DATA_ROOT", tmp_path)
    monkeypatch.setattr(report_api, "REPORTS", {})
    monkeypatch.setattr(ai_audit_service, "AUDIT_FILE_PATH", tmp_path / "audit" / "events.jsonl")
    reports = tmp_path / "reports"
    reports.mkdir()
    report_id = "report-1234abcd"
    entry = {
        "reportId": report_id, "ownerId": "chat-owner", "sourceId": "src-12345678",
        "status": "generated", "validation": {"passed": True},
        "reportPath": f"reports/{report_id}.Report",
        "reportSpec": {"page": "Published report", "visuals": []},
    }
    manifest = reports / f"{report_id}.json"
    manifest.write_text(json.dumps(entry), encoding="utf-8")
    report_path = reports / f"{report_id}.Report"
    report_path.mkdir()
    audit_path = tmp_path / "audit" / "reports" / f"{report_id}.json"
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text(json.dumps({
        "report_id": report_id, "source_id": "src-12345678", "conversation": [
            {"role": "user", "text": "Build a report"},
            {"role": "assistant", "text": "Done"},
        ], "decision_debug": {"preserved": True},
    }), encoding="utf-8")
    owner = {"x-aura-user": "chat-owner"}
    other = {"x-aura-user": "other-user"}

    assert client.patch(f"/api/report/{report_id}/conversation", json={"title": "  New chat  "}, headers=other).status_code == 403
    assert client.delete(f"/api/report/{report_id}/conversation", headers=other).status_code == 403
    assert client.patch(f"/api/report/{report_id}/conversation", json={"title": " "}, headers=owner).status_code == 400
    assert client.patch(f"/api/report/{report_id}/conversation", json={"title": "  New chat  "}, headers=owner).json() == {"title": "New chat"}
    report_api.REPORTS.clear()
    listing = client.get("/api/report", headers=owner).json()["reports"][0]
    assert listing["chatTitle"] == "New chat"
    assert listing["title"] == "Published report"
    assert listing["chatDeleted"] is False
    assert len(client.get(f"/api/report/{report_id}/conversation", headers=owner).json()["conversation"]) == 2

    assert client.delete(f"/api/report/{report_id}/conversation", headers=owner).json() == {"deleted": True}
    report_api.REPORTS.clear()
    listing = client.get("/api/report", headers=owner).json()["reports"][0]
    assert listing["chatDeleted"] is True
    assert listing["title"] == "Published report"
    assert client.get(f"/api/report/{report_id}/conversation", headers=owner).json() == {"available": False, "conversation": []}
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["conversation"] == []
    assert audit["decision_debug"] == {"preserved": True}
    assert report_path.is_dir()
    assert client.patch(f"/api/report/{report_id}/conversation", json={"title": "Again"}, headers=owner).status_code == 404


def test_excel_stream_reports_real_stages_and_cached_profile(monkeypatch, tmp_path) -> None:
    from openpyxl import Workbook
    from backend.services import model_service

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(model_service, "APP_DATA_DIR", tmp_path)
    workbook = Workbook()
    workbook.active.append(["Region", "Revenue"])
    workbook.active.append(["North", 100])
    workbook.active["D1"] = "=1+1"
    workbook.create_sheet("Other")["A1"] = "=2+2"
    content = io.BytesIO()
    workbook.save(content)
    workbook.close()

    def upload():
        response = client.post(
            "/api/datasource/excel/stream",
            files={"file": ("sales.xlsx", content.getvalue())},
            headers={"x-aura-user": "stream-test-user"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        return [json.loads(line) for line in response.text.splitlines()]

    events = upload()
    stages = [event["stage"] for event in events if event["type"] == "progress"]
    assert stages[:2] == ["reading_workbook", "detecting_tables"]
    assert "reading_formula_results" in stages
    assert "profiling_columns" in stages
    assert stages[-1] == "finalizing"
    assert events[-1]["type"] == "result"
    assert events[-1]["result"]["sheets"]
    assert events[-1]["result"]["formulaCellCount"] == 2
    memory = client.get(f"/api/agent/memory/{events[-1]['result']['sourceId']}", headers={"x-aura-user": "stream-test-user"})
    assert memory.status_code == 200
    assert memory.json()["formulaCellCount"] == 2
    from backend.services.identity_service import identity_context
    from backend.services.model_service import load_source_payload
    token = identity_context.set("stream-test-user")
    try:
        assert load_source_payload(events[-1]["result"]["sourceId"])["ownerId"] == "stream-test-user"
    finally:
        identity_context.reset(token)
    cached = upload()
    assert [event["stage"] for event in cached if event["type"] == "progress"] == [
        "loading_cached_profile", "finalizing",
    ]
    assert cached[-1]["result"]["profileCacheHit"] is True
    assert cached[-1]["result"]["formulaCellCount"] == 2


def test_excel_stream_reports_invalid_files(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    bad_extension = client.post("/api/datasource/excel/stream", files={"file": ("bad.csv", b"bad")})
    assert bad_extension.status_code == 400
    invalid = client.post("/api/datasource/excel/stream", files={"file": ("bad.xlsx", b"bad")})
    assert invalid.status_code == 200
    assert json.loads(invalid.text)["type"] == "error"
    assert "not a valid .xlsx" in json.loads(invalid.text)["detail"]
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "1")
    too_large = client.post("/api/datasource/excel/stream", files={"file": ("large.xlsx", b"123")})
    assert too_large.status_code == 413


def test_identity_and_published_report_list_are_user_scoped(monkeypatch, tmp_path) -> None:
    from backend.api import report as report_api

    monkeypatch.setattr(report_api, "APP_DATA_ROOT", tmp_path / "app-data")
    monkeypatch.setattr(report_api, "REPORTS", {})
    reports_dir = report_api.APP_DATA_ROOT / "reports"
    reports_dir.mkdir(parents=True)
    entries = [
        {
            "reportId": "report-1234abcd",
            "ownerId": "dashboard-reader",
            "status": "generated",
            "validation": {"passed": True},
            "reportPath": "reports/report-1234abcd.Report",
            "sourceId": "src-12345678",
            "sourceName": "sales.xlsx",
            "sourceType": "excel",
            "reportSpec": {"page": "Sample report", "visuals": [{}, {}], "additionalPages": [{"page": "Page 2", "visuals": [{}]}]},
        },
        {
            "reportId": "report-5678abcd",
            "ownerId": "another-user",
            "status": "generated",
            "validation": {"passed": True},
            "reportPath": "reports/report-5678abcd.Report",
            "reportSpec": {"page": "Private report", "visuals": []},
        },
    ]
    for entry in entries:
        (reports_dir / f"{entry['reportId']}.json").write_text(json.dumps(entry), encoding="utf-8")

    headers = {"X-AURA-User": "dashboard-reader"}
    identity = client.get("/api/identity", headers=headers)
    assert identity.status_code == 200
    assert identity.json() == {"userId": "dashboard-reader", "isDemo": False}

    response = client.get("/api/report", headers=headers)
    assert response.status_code == 200
    assert response.json()["hasMore"] is False
    assert response.json()["reports"][0]["title"] == "Sample report"
    assert response.json()["reports"][0]["pageCount"] == 2
    assert response.json()["reports"][0]["visualCount"] == 3
    assert response.json()["reports"][0]["sourceId"] == "src-12345678"
    assert response.json()["reports"][0]["sourceName"] == "sales.xlsx"
    assert response.json()["reports"][0]["sourceType"] == "excel"
    assert response.json()["reports"][0]["reportSpec"]["page"] == "Sample report"


def test_saved_report_conversation_is_owner_scoped_and_handles_missing_or_invalid_audit(monkeypatch, tmp_path) -> None:
    from backend.api import report as report_api
    from backend.services import ai_audit_service

    monkeypatch.setattr(report_api, "APP_DATA_ROOT", tmp_path / "app-data")
    monkeypatch.setattr(report_api, "REPORTS", {})
    monkeypatch.setattr(ai_audit_service, "AUDIT_FILE_PATH", tmp_path / "audit" / "ai-audit.jsonl")
    reports = report_api.APP_DATA_ROOT / "reports"
    reports.mkdir(parents=True)
    report_id = "report-1234abcd"
    entry = {
        "reportId": report_id, "ownerId": "owner", "sourceId": "src-12345678",
        "status": "generated", "validation": {"passed": True},
        "reportPath": f"reports/{report_id}.Report",
    }
    (reports / f"{report_id}.json").write_text(json.dumps(entry), encoding="utf-8")
    url = f"/api/report/{report_id}/conversation"
    owner = {"X-AURA-User": "owner"}
    assert client.get(url, headers=owner).json() == {"available": False, "conversation": []}
    audit_dir = ai_audit_service.AUDIT_FILE_PATH.parent / "reports"
    audit_dir.mkdir(parents=True)
    audit = {"report_id": report_id, "source_id": entry["sourceId"], "conversation": [
        {"role": "user", "text": "Build a report"},
        {"role": "assistant", "text": "Here is the draft"},
    ]}
    audit_file = audit_dir / f"{report_id}.json"
    audit_file.write_text(json.dumps(audit), encoding="utf-8")
    assert client.get(url, headers=owner).json() == {"available": True, "conversation": audit["conversation"]}
    assert client.get(url, headers={"X-AURA-User": "another-user"}).status_code == 403
    audit["source_id"] = "src-99999999"
    audit_file.write_text(json.dumps(audit), encoding="utf-8")
    assert client.get(url, headers=owner).status_code == 500
    assert client.get("/api/report/not-a-report/conversation", headers=owner).status_code == 404


def test_agent_run_history_api_returns_owner_scoped_audit_without_tool_outputs(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    from backend.services import run_service
    from backend.services.identity_service import identity_context

    monkeypatch.setattr(run_service, "get_settings", lambda: SimpleNamespace(
        app_data_dir=tmp_path, auth_required=False, api_tokens={},
    ))
    identity_token = identity_context.set("history-owner")
    try:
        run = run_service.create_run(
            "src-12345678",
            "Create a sales report",
            [{"action": "plan_dashboard", "reason": "Build a grounded report"}],
            source_type="excel",
            selected_table="Sales",
            report_name="Sales overview",
        )
        run["steps"][0]["result"] = {"page": "Sales overview", "rows": [{"value": "private"}]}
        run_service.save_run(run)
    finally:
        identity_context.reset(identity_token)

    owner_headers = {"X-AURA-User": "history-owner"}
    history = client.get("/api/agent/runs", headers=owner_headers)
    assert history.status_code == 200
    assert history.json()["hasMore"] is False
    assert history.json()["runs"][0]["runId"] == run["runId"]
    assert history.json()["runs"][0]["selectedTable"] == "Sales"

    audit = client.get(f"/api/agent/runs/{run['runId']}/audit", headers=owner_headers)
    assert audit.status_code == 200
    assert audit.json()["format"] == "autobi-run-audit"
    assert "result" not in audit.json()["run"]["steps"][0]
    assert "results" not in audit.json()["run"]

    linked = client.post(
        f"/api/agent/runs/{run['runId']}/report",
        headers=owner_headers,
        json={"reportId": "report-1234abcd", "reportName": "Sales overview", "traceId": "0123456789abcdef0123456789abcdef"},
    )
    assert linked.status_code == 200
    refreshed_audit = client.get(f"/api/agent/runs/{run['runId']}/audit", headers=owner_headers).json()["run"]
    assert refreshed_audit["reportId"] == "report-1234abcd"
    assert refreshed_audit["traceIds"] == ["0123456789abcdef0123456789abcdef"]

    other_user_history = client.get("/api/agent/runs", headers={"X-AURA-User": "other-user"})
    assert other_user_history.status_code == 200
    assert other_user_history.json()["runs"] == []
    other_user_audit = client.get(f"/api/agent/runs/{run['runId']}/audit", headers={"X-AURA-User": "other-user"})
    assert other_user_audit.status_code == 403


def test_agent_run_creation_persists_audit_context_and_trace(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    from backend.api import agent as agent_api
    from backend.services import run_service

    monkeypatch.setattr(run_service, "get_settings", lambda: SimpleNamespace(
        app_data_dir=tmp_path, auth_required=False, api_tokens={},
    ))
    monkeypatch.setattr(agent_api, "load_source_metadata", lambda _source_id: {})

    async def decide(*_args):
        return {
            "plannedSteps": [{"action": "plan_dashboard", "reason": "Build requested report"}],
            "requiresApproval": True,
            "planner": "rules",
            "fallbackUsed": False,
        }

    monkeypatch.setattr(agent_api.orchestrator, "decide", decide)
    response = client.post(
        "/api/agent/runs",
        headers={"X-AURA-User": "run-creator"},
        json={
            "sourceId": "src-12345678",
            "request": "Create a sales report",
            "hasReport": False,
            "table": "Sales",
            "sourceType": "excel",
            "traceId": "0123456789abcdef0123456789abcdef",
            "conversation": [],
        },
    )

    assert response.status_code == 200
    run = response.json()
    assert run["auditContext"] == {
        "sourceType": "excel",
        "selectedTable": "Sales",
        "reportName": None,
    }
    assert run["traceIds"] == ["0123456789abcdef0123456789abcdef"]
    assert any(event["event"] == "trace_linked" for event in run["events"])


def test_ambiguous_excel_region_requires_explicit_confirmation(monkeypatch) -> None:
    from openpyxl import Workbook

    enable_mock_ai(monkeypatch)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet.append(["Region", "Revenue"])
    sheet.append(["North", 100])
    sheet.append(["South", None])
    sheet.merge_cells("B2:B3")
    content = io.BytesIO()
    workbook.save(content)
    workbook.close()
    upload = client.post('/api/datasource/excel', files={'file': ('merged.xlsx', content.getvalue())})
    assert upload.status_code == 200
    source_id = upload.json()["sourceId"]
    review = upload.json()["sheets"][0]["parsingReview"]
    payload = {"sourceId": source_id, "table": "Data", "request": "Show total revenue by region"}
    assert client.post('/api/report/plan', json=payload).status_code == 400
    generation = client.post('/api/report/generate', json={
        "sourceId": source_id,
        "reportSpec": {"page": "Review test", "visuals": [{"type": "card", "value": {
            "table": "Data", "field": "Total Revenue", "fieldType": "measure",
        }}]},
    })
    assert generation.status_code == 400
    assert "Review and confirm" in generation.json()["detail"]
    confirmation = {"sourceId": source_id, "table": "Data", "range": review["range"], "headerRow": review["headerRow"]}
    assert client.post('/api/datasource/excel/review', json={**confirmation, "range": "A1:C9"}).status_code == 409
    assert client.post('/api/datasource/excel/review', json=confirmation).status_code == 200
    assert client.post('/api/report/plan', json=payload).status_code == 200


def test_slicer_repair_requires_review_and_generates_validated_report(monkeypatch, tmp_path) -> None:
    from openpyxl import Workbook
    from backend.api import report as report_api
    from backend.services import ai_audit_service

    monkeypatch.setattr(report_api, "APP_DATA_ROOT", tmp_path)
    monkeypatch.setattr(report_api, "REPORTS", {})
    monkeypatch.setattr(ai_audit_service, "AUDIT_FILE_PATH", tmp_path / "audit" / "events.jsonl")
    workbook = Workbook()
    workbook.active.title = "Sales"
    workbook.active.append(["Region", "Revenue"])
    workbook.active.append(["North", 100])
    workbook.active.append(["South", 200])
    costs = workbook.create_sheet("Costs")
    costs.append(["Category", "Cost"])
    costs.append(["Transport", 25])
    costs.append(["Office", 50])
    content = io.BytesIO()
    workbook.save(content)
    upload = client.post("/api/datasource/excel", files={"file": ("data.xlsx", content.getvalue())})
    assert upload.status_code == 200, upload.text
    source = upload.json()["sourceId"]
    sales_card = {"type": "card", "value": {"table": "Sales", "field": "Total Revenue", "fieldType": "measure"}}
    costs_card = {"type": "card", "value": {"table": "Costs", "field": "Total Cost", "fieldType": "measure"}}
    slicer = {"type": "slicer", "field": {"table": "Sales", "field": "Region", "fieldType": "column"}}
    spec = {"page": "Overview", "visuals": [sales_card, costs_card, slicer],
            "additionalPages": [{"page": "Sales detail", "visuals": [sales_card, slicer]}]}
    approve_plan(source, spec)
    generated = client.post("/api/report/generate", json={"sourceId": source, "reportSpec": spec})
    assert generated.status_code == 200, generated.text
    failed = generated.json()
    assert failed["validation"]["passed"] is False
    assert any("cannot filter" in error for error in failed["validation"]["errors"])
    repair_url = f"/api/report/{failed['reportId']}/repair"
    stale = client.post(repair_url, json={"reportSpec": {**spec, "page": "Changed"}})
    assert stale.status_code == 409
    assert client.post(repair_url, json={"reportSpec": spec}, headers={"x-aura-user": "someone-else"}).status_code == 403
    entry = report_api.REPORTS[failed["reportId"]]
    approved_spec = entry.pop("approvedReportSpec")
    legacy_repair = client.post(repair_url, json={"reportSpec": spec})
    assert legacy_repair.status_code == 200, legacy_repair.text
    entry["approvedReportSpec"] = approved_spec
    entry["validation"]["errors"].append("Another validation error")
    assert client.post(repair_url, json={"reportSpec": spec}).status_code == 409
    entry["validation"]["errors"].pop()
    repaired = client.post(repair_url, json={"reportSpec": spec})
    assert repaired.status_code == 200, repaired.text
    revised = repaired.json()["reportSpec"]
    assert revised["visuals"] == [sales_card, costs_card]
    assert revised["additionalPages"][0]["visuals"] == [sales_card, slicer]
    assert repaired.json()["changes"] == [
        "Removed slicer Sales[Region] from Overview; it cannot filter all measures on that page."
    ]
    assert client.post("/api/report/generate", json={"sourceId": source, "reportSpec": revised}).status_code == 409
    approve_plan(source, revised)
    regenerated = client.post("/api/report/generate", json={"sourceId": source, "reportSpec": revised})
    assert regenerated.status_code == 200, regenerated.text
    assert regenerated.json()["validation"]["passed"], regenerated.json()["validation"]


def test_slicer_repair_rejects_other_validation_failures(monkeypatch) -> None:
    from backend.api import report as report_api

    spec = {"page": "Overview", "visuals": []}
    monkeypatch.setattr(report_api, "REPORTS", {"report-1234abcd": {
        "ownerId": "demo-user", "status": "failed", "reportSpec": spec,
        "validation": {"errors": ["Bad DAX"], "calculationAccuracy": {
            "errors": ["Bad DAX"], "checks": [{"type": "measure", "passed": False}],
        }},
    }})
    response = client.post("/api/report/report-1234abcd/repair", json={"reportSpec": spec})
    assert response.status_code == 409
    assert "only available" in response.json()["detail"]


def test_request_id_is_preserved_when_safe_and_replaced_when_invalid() -> None:
    valid_id = "client-request_123.abc"
    valid_response = client.get("/health", headers={"x-request-id": valid_id})
    assert valid_response.headers["x-request-id"] == valid_id
    assert valid_response.headers["x-trace-id"]
    assert valid_response.headers["x-agent-run-id"]

    invalid_response = client.get("/health", headers={"x-request-id": "client request id"})
    generated_id = invalid_response.headers["x-request-id"]
    assert generated_id != "client request id"
    assert len(generated_id) == 36
    assert invalid_response.json() == {"status": "ok"}


def test_trace_and_agent_run_context_are_propagated() -> None:
    trace_id = "0123456789abcdef0123456789abcdef"
    agent_run_id = "123e4567-e89b-42d3-a456-426614174000"
    response = client.get(
        "/health",
        headers={
            "traceparent": f"00-{trace_id}-0123456789abcdef-01",
            "x-agent-run-id": agent_run_id,
        },
    )

    assert response.status_code == 200
    assert response.headers["x-trace-id"] == trace_id
    assert response.headers["x-agent-run-id"] == agent_run_id


def test_invalid_trace_and_agent_run_ids_are_replaced() -> None:
    response = client.get(
        "/health",
        headers={"traceparent": "invalid", "x-agent-run-id": "not-a-uuid"},
    )

    assert len(response.headers["x-trace-id"]) == 32
    assert len(response.headers["x-agent-run-id"]) == 36


def test_client_error_is_logged_as_warning_with_request_context(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="autobi.http"):
        response = client.get("/missing-route", headers={"x-request-id": "not-found-123"})

    assert response.status_code == 404
    request_log = next(record for record in caplog.records if record.name == "autobi.http")
    assert request_log.levelno == logging.WARNING
    assert request_log.request_id == "not-found-123"
    assert request_log.status_code == 404
    assert request_log.event == "request.completed"


def test_unhandled_request_errors_return_safe_json_with_request_id(caplog) -> None:
    from backend.main import app

    async def fail_request() -> dict:
        raise RuntimeError("sensitive internal detail")

    app.add_api_route("/__test_logging_error", fail_request, methods=["GET"], name="test_logging_error")
    try:
        response = client.get("/__test_logging_error", headers={"x-request-id": "error-test-123"})
    finally:
        app.router.routes[:] = [
            route for route in app.router.routes if getattr(route, "name", None) != "test_logging_error"
        ]

    error_log = next(
        record for record in caplog.records
        if record.name == "autobi.http" and getattr(record, "event", None) == "request.failed"
    )
    assert response.status_code == 500
    assert response.headers["x-request-id"] == "error-test-123"
    assert response.headers["x-trace-id"]
    assert response.headers["x-agent-run-id"]
    assert response.json() == {
        "detail": "Internal server error",
        "requestId": "error-test-123",
    }
    assert "sensitive internal detail" not in response.text
    assert error_log.levelno == logging.ERROR
    assert error_log.exception_type == "RuntimeError"


def test_excel_upload_and_plan_generation(monkeypatch) -> None:
    enable_mock_ai(monkeypatch)
    workbook = io.BytesIO()
    from openpyxl import Workbook

    wb = Workbook()
    sheet = wb.active
    sheet.title = 'Sales'
    sheet.append(['Region', 'Revenue'])
    sheet.append(['North', 1000])
    sheet.append(['South', 2000])
    wb.save(workbook)
    workbook.seek(0)

    upload = client.post('/api/datasource/excel', files={'file': ('sales.xlsx', workbook.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert upload.status_code == 200, upload.text
    source_id = upload.json()['sourceId']
    from backend.services.model_service import load_source_payload
    stored_source = load_source_payload(source_id)
    assert stored_source["workbookProfile"]["workbook"]["sheet_count"] == 1
    assert stored_source.get("aiUnderstanding")
    assert upload.json()['sheets'][0]['sampleRows'][0] == {'Region': 'North', 'Revenue': 1000}

    plan = client.post('/api/report/plan', json={'sourceId': source_id, 'request': 'Create a sales dashboard showing total revenue by region.'})
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body['page']
    assert body['visuals'][0]['category']['field'] == 'Region'

    preview = client.post('/api/report/preview', json={'sourceId': source_id, 'reportSpec': body})
    assert preview.status_code == 200, preview.text
    assert preview.json()['previewTheme']['dataColors'][:3] == ['#F40000', '#EDD382', '#A1C181']
    assert preview.json()['previewTheme']['backgroundLight'] == '#FAF7F0'
    assert preview.json()['visuals'][0]['data'] == [
        {'label': 'South', 'value': 2000.0},
        {'label': 'North', 'value': 1000.0},
    ]


    line_spec = {**body, 'visuals': [{**body['visuals'][0], 'type': 'lineChart'}]}
    line_preview = client.post('/api/report/preview', json={'sourceId': source_id, 'reportSpec': line_spec})
    assert line_preview.status_code == 200, line_preview.text
    assert line_preview.json()['visuals'][0]['data'] == [
        {'label': 'North', 'value': 1000.0},
        {'label': 'South', 'value': 2000.0},
    ]
    custom_spec = {**body, 'design': {'theme': 'dar_light', 'colors': {'primary': '#123456'}}}
    custom_preview = client.post('/api/report/preview', json={'sourceId': source_id, 'reportSpec': custom_spec})
    assert custom_preview.status_code == 200, custom_preview.text
    assert custom_preview.json()['previewTheme']['dataColors'][0] == '#123456'
    multi_page_spec = {
        **body,
        'additionalPages': [{**body, 'page': 'Second page'}],
    }
    multi_preview = client.post('/api/report/preview', json={'sourceId': source_id, 'reportSpec': multi_page_spec})
    assert multi_preview.status_code == 200, multi_preview.text
    assert multi_preview.json()['previewTheme']['dataColors'][0] == '#F40000'
    assert multi_preview.json()['additionalPages'][0]['previewTheme']['dataColors'][0] == '#F40000'
    alternate_spec = {**body, 'design': {'theme': 'corporate'}}
    alternate_preview = client.post('/api/report/preview', json={'sourceId': source_id, 'reportSpec': alternate_spec})
    assert alternate_preview.status_code == 200, alternate_preview.text
    assert alternate_preview.json()['previewTheme']['dataColors'][0] == '#145DA0'

    approve_plan(source_id, body)
    generate = client.post('/api/report/generate', json={'sourceId': source_id, 'reportSpec': body})
    assert generate.status_code == 200, generate.text
    result = generate.json()
    assert result['status'] == 'generated'
    assert result['validation']['passed'] is True

    from backend.api import report as report_api
    opened_projects = []
    if report_api.os.name == 'nt':
        monkeypatch.setattr(report_api.os, 'startfile', lambda path: opened_projects.append(path))
    open_response = client.post(f"/api/report/{result['reportId']}/open")
    assert open_response.status_code == (200 if report_api.os.name == 'nt' else 501), open_response.text
    project_path = report_api.APP_ROOT / 'backend' / '.app_data' / 'reports' / f"{result['reportId']}.pbip"
    assert opened_projects == ([str(project_path)] if report_api.os.name == 'nt' else [])
    assert json.loads(project_path.read_text(encoding='utf-8'))['artifacts'][0]['report']['path'] == f"{result['reportId']}.Report"

    download = client.get(f"/api/report/{result['reportId']}/download")
    assert download.status_code == 200
    assert download.headers['content-disposition'].endswith(f'filename="{result["reportId"]}.pbip.zip"')
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        names = archive.namelist()
        project_name = f"{result['reportId']}.pbip"
        project = json.loads(archive.read(project_name))
        assert project['artifacts'][0]['report']['path'] == f"{result['reportId']}.Report"
        report_definition = json.loads(archive.read(f"{result['reportId']}.Report/definition.pbir"))
        assert report_definition['datasetReference']['byPath']['path'] == f"../{result['reportId']}.SemanticModel"
        report_json = json.loads(archive.read(f"{result['reportId']}.Report/definition/report.json"))
        assert report_json['$schema'].endswith('/report/3.3.0/schema.json')
        assert report_json['themeCollection']['baseTheme']['name'] == 'DAR Light'
        assert report_json['themeCollection']['baseTheme']['reportVersionAtImport'] == {
            'visual': '2.12.0', 'report': '3.4.0', 'page': '2.3.1',
        }
        assert report_json['resourcePackages'][0]['items'][0] == {
            'name': 'DAR Light', 'path': 'BaseThemes/DAR Light.json', 'type': 'BaseTheme',
        }
        assert report_json['settings']['defaultDrillFilterOtherVisuals'] is True
        theme_path = f"{result['reportId']}.Report/StaticResources/SharedResources/BaseThemes/DAR Light.json"
        assert theme_path in names
        theme = json.loads(archive.read(theme_path))
        expected_theme = json.loads(
            (Path(__file__).resolve().parents[2] / "docs" / "DAR_PowerBI_Light.json").read_text(encoding="utf-8")
        )
        slicer_items = expected_theme["visualStyles"]["slicer"]["*"]["items"]
        if slicer_items:
            slicer_items[0]["outline"] = "None"
        else:
            slicer_items.append({"outline": "None"})
        assert theme == expected_theme
        assert theme['dataColors'][:3] == ['#F40000', '#EDD382', '#A1C181']
        assert theme['background'] == '#FFFFFF'
        assert theme['foreground'] == '#1A1A1A'
        assert theme['textClasses']['title']['fontFace'].startswith("'TCCC Unity Head Bold'")
        assert theme['visualStyles']['*']['*']['valueAxis'][0]['gridlineColor']['solid']['color'] == '#E5E5E5'
        assert theme['visualStyles']['slicer']['*']['items'][0]['outline'] == 'None'
        custom = report_json['themeCollection']['customTheme']
        assert custom['type'] == 'RegisteredResources'
        custom_name = custom['name']
        assert custom_name.startswith('DAR Light-') and custom_name.endswith('.json')
        registered = next(p for p in report_json['resourcePackages'] if p['name'] == 'RegisteredResources')
        assert {'name': custom_name, 'path': custom_name, 'type': 'CustomTheme'} in registered['items']
        custom_theme_path = f"{result['reportId']}.Report/StaticResources/RegisteredResources/{custom_name}"
        assert json.loads(archive.read(custom_theme_path)) == {**theme, 'name': custom_name}
        pages_json = json.loads(archive.read(f"{result['reportId']}.Report/definition/pages/pages.json"))
        assert pages_json['$schema'].endswith('/pagesMetadata/1.1.0/schema.json')
        assert f"{result['reportId']}.Report/definition/version.json" in names
        assert f"{result['reportId']}.SemanticModel/definition.pbism" in names
        database_tmdl = archive.read(f"{result['reportId']}.SemanticModel/definition/database.tmdl").decode('utf-8')
        assert database_tmdl.splitlines()[0] == f"database '{result['reportId']}'"
        assert 'model:' not in database_tmdl
        model_tmdl = archive.read(f"{result['reportId']}.SemanticModel/definition/model.tmdl").decode('utf-8')
        assert model_tmdl.splitlines()[0] == 'model Model'
        model_tmdl_path = next(name for name in names if f"{result['reportId']}.SemanticModel/definition/tables/" in name and name.endswith('.tmdl'))
        model_tmdl = archive.read(model_tmdl_path).decode('utf-8')
        assert 'dataType: int64' in model_tmdl
        assert "SUM('Sales'[Revenue])" in model_tmdl
        assert 'partition \'Sales\' = calculated' in model_tmdl
        assert 'DATATABLE(' in model_tmdl
        assert '"Region", STRING' in model_tmdl
        assert '#table(' not in model_tmdl
        assert 'North' in model_tmdl and 'South' in model_tmdl

    edit = client.post('/api/report/edit', json={
        'sourceId': source_id,
        'reportSpec': body,
        'instruction': 'Change the column chart to a line chart',
    })
    assert edit.status_code == 200, edit.text
    revised = edit.json()['reportSpec']
    assert revised['visuals'][0]['type'] == 'lineChart'
    stale_approval = client.post('/api/report/generate', json={'sourceId': source_id, 'reportSpec': revised})
    assert stale_approval.status_code == 409
    approve_plan(source_id, revised)
    regenerated = client.post('/api/report/generate', json={'sourceId': source_id, 'reportSpec': revised})
    assert regenerated.status_code == 200, regenerated.text
    assert regenerated.json()['reportId'] != result['reportId']
    assert regenerated.json()['validation']['calculationAccuracy']['passed'] is True


def test_excel_upload_warns_when_formula_results_are_not_saved() -> None:
    from openpyxl import Workbook

    workbook = io.BytesIO()
    wb = Workbook()
    sheet = wb.active
    sheet.title = "Calculated"
    sheet.append(["Amount", "Doubled"])
    sheet.append([10, "=A2*2"])
    wb.save(workbook)
    workbook.seek(0)

    response = client.post(
        "/api/datasource/excel",
        files={"file": ("formulas.xlsx", workbook.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["formulaCellCount"] == 1
    assert any("does not recalculate formulas" in warning for warning in response.json()["warnings"])
    assert any("no saved result" in warning for warning in response.json()["warnings"])
    returned_columns = response.json()["sheets"][0]["columns"]
    assert all("formulaExpressions" not in column for column in returned_columns)
    assert next(column for column in returned_columns if column["name"] == "Doubled")["formulaCount"] == 1


def test_excel_request_generates_multiple_visuals(monkeypatch) -> None:
    enable_mock_ai(monkeypatch)
    workbook = io.BytesIO()
    from openpyxl import Workbook

    wb = Workbook()
    sheet = wb.active
    sheet.title = 'Sales'
    sheet.append(['Region', 'Product', 'Revenue'])
    sheet.append(['North', 'Widget', 1000])
    sheet.append(['South', 'Gadget', 2000])
    wb.save(workbook)
    workbook.seek(0)

    upload = client.post('/api/datasource/excel', files={'file': ('sales.xlsx', workbook.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert upload.status_code == 200, upload.text
    source_id = upload.json()['sourceId']
    plan = client.post('/api/report/plan', json={
        'sourceId': source_id,
        'table': 'Sales',
        'request': 'Create a sales dashboard showing total revenue by region and product as a column chart.',
    })
    assert plan.status_code == 200, plan.text
    assert len(plan.json()['visuals']) == 2

    approve_plan(source_id, plan.json())
    generate = client.post('/api/report/generate', json={'sourceId': source_id, 'reportSpec': plan.json()})
    assert generate.status_code == 200, generate.text
    download = client.get(f"/api/report/{generate.json()['reportId']}/download")
    assert download.status_code == 200, download.text
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        visual_files = [
            name for name in archive.namelist()
            if name.endswith('/visual.json') and '/visuals/autobi-decoration-' not in name
        ]
    assert len(visual_files) == 2


def test_ai_mode_profiles_multiple_sheets_and_uses_shared_report_generator(monkeypatch, tmp_path) -> None:
    from automation.ai.ai_service import MockProvider
    from backend.api import datasource as datasource_api
    from backend.api import report as report_api
    from backend.services import ai_audit_service
    from backend.services.model_service import load_source_payload
    from openpyxl import Workbook

    class TestProvider(MockProvider):
        async def plan_dashboard(self, request, profile, understanding, metadata, visual_count, repair_context=None, visual_count_mode="analytical"):
            return {
                "status": "ready",
                "page": "Sales and Targets Dashboard",
                "rationale": "Revenue and target values are compared across regions.",
                "visuals": [
                    {
                        "type": "clusteredColumnChart",
                        "category": {"table": "Sales", "field": "Region", "fieldType": "column"},
                        "value": {"table": "Sales", "field": "Total Revenue", "fieldType": "measure"},
                    },
                    {
                        "type": "clusteredBarChart",
                        "category": {"table": "Targets", "field": "Region", "fieldType": "column"},
                        "value": {"table": "Targets", "field": "Total Target", "fieldType": "measure"},
                    },
                ],
            }

    monkeypatch.setenv("USE_AI", "yes")
    monkeypatch.setenv("AI_PROVIDER", "mock")
    audit_path = tmp_path / "audit" / "ai-audit.jsonl"
    monkeypatch.setattr(ai_audit_service, "AUDIT_FILE_PATH", audit_path)
    monkeypatch.setattr(datasource_api, "get_ai_provider", lambda settings: MockProvider())
    monkeypatch.setattr(report_api, "get_ai_provider", lambda settings: TestProvider())

    workbook = io.BytesIO()
    wb = Workbook()
    sales = wb.active
    sales.title = "Sales"
    sales.append(["Region", "Revenue"])
    sales.append(["North", 100])
    sales.append(["South", 200])
    targets = wb.create_sheet("Targets")
    targets.append(["Region", "Target"])
    targets.append(["North", 120])
    targets.append(["South", 210])
    wb.save(workbook)
    workbook.seek(0)

    upload = client.post(
        "/api/datasource/excel",
        files={"file": ("multi.xlsx", workbook.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["aiEnabled"] is True
    assert upload.json()["aiAnalyzed"] is True
    source_id = upload.json()["sourceId"]
    source_payload = load_source_payload(source_id)
    assert [sheet["name"] for sheet in source_payload["workbookProfile"]["sheets"]] == ["Sales", "Targets"]

    plan = client.post("/api/report/plan", json={
        "sourceId": source_id,
        "table": "Sales",
        "request": (
            "Create a 2 visual dashboard:\n"
            "1. Total Revenue by Region as a column chart\n"
            "2. Total Target by Region as a bar chart"
        ),
    })
    assert plan.status_code == 200, plan.text
    assert [visual["value"]["table"] for visual in plan.json()["visuals"]] == ["Sales", "Targets"]
    audit_events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [event["operation"] for event in audit_events] == [
        "workbook_understanding",
        "dashboard_planning",
    ]
    assert audit_events[0]["decisions"]["tables"][0]["columns"][1]["role"] == "measure"
    assert audit_events[1]["decisions"]["visuals"][0] == {
        "type": "clusteredColumnChart",
        "bindings": [
            {"role": "category", "table": "Sales", "field": "Region"},
            {"role": "value", "table": "Sales", "field": "Total Revenue"},
        ],
    }
    assert audit_events[1]["decisions"]["rationale"] == (
        "Revenue and target values are compared across regions."
    )

    approve_plan(source_id, plan.json())
    generated = client.post("/api/report/generate", json={"sourceId": source_id, "reportSpec": plan.json()})
    assert generated.status_code == 200, generated.text
    assert generated.json()["validation"]["passed"] is True
    download = client.get(f"/api/report/{generated.json()['reportId']}/download")
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        model_tables = [
            name for name in archive.namelist()
            if name.endswith(".SemanticModel/definition/tables/Sales.tmdl")
            or name.endswith(".SemanticModel/definition/tables/Targets.tmdl")
        ]
        assert len(model_tables) == 2


def test_ai_status_endpoint_exposes_mode_without_secrets(monkeypatch) -> None:
    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "must-never-be-returned")
    monkeypatch.setenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/default")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "report-planner")

    response = client.get("/api/report/ai-status")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "provider": "azure_openai",
        "model": "report-planner",
        "configured": True,
    }
    assert "must-never-be-returned" not in response.text


def test_ai_connection_test_uses_mock_without_external_call(monkeypatch) -> None:
    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "mock")

    response = client.post("/api/report/ai-test")

    assert response.status_code == 200
    assert response.json() == {"connected": True, "provider": "mock", "model": "mock"}


def test_report_planning_does_not_fall_back_when_ai_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr('backend.api.report.load_source_metadata', lambda source_id: {
        'tables': {'Records': {'name': 'Records', 'columns': ['Area'], 'measures': ['Total Amount']}}
    })
    response = client.post('/api/report/plan', json={
        'sourceId': 'src-test',
        'request': 'Show unknown metric by unknown category',
    })
    assert response.status_code == 503
    assert 'AI is required for report planning but is disabled' in response.json()['detail']


def test_preview_slicer_filters_related_table_rows(monkeypatch) -> None:
    from backend.api import report as report_api

    metadata = {
        "tables": {
            "Regions": {"columns": ["Region"], "measures": []},
            "Orders": {"columns": ["Region", "Amount"], "measures": ["Total Amount"]},
        },
        "relationships": [{
            "source_table": "Orders",
            "source_column": "Region",
            "target_table": "Regions",
            "target_column": "Region",
            "confidence": 0.95,
        }],
    }
    payload = {
        "metadata": {
            "tables": {
                "Regions": {"rows": [{"Region": "North"}, {"Region": "South"}]},
                "Orders": {"rows": [
                    {"Region": "North", "Amount": 100},
                    {"Region": "South", "Amount": 200},
                ]},
            }
        }
    }
    monkeypatch.setattr(report_api, "load_source_metadata", lambda _source_id: metadata)
    monkeypatch.setattr(report_api, "load_source_payload", lambda _source_id: payload)
    spec = {
        "page": "Regional Sales",
        "visuals": [
            {"type": "slicer", "field": {"table": "Regions", "field": "Region", "fieldType": "column"}},
            {
                "type": "clusteredColumnChart",
                "category": {"table": "Orders", "field": "Region", "fieldType": "column"},
                "value": {"table": "Orders", "field": "Total Amount", "fieldType": "measure"},
            },
        ],
    }

    response = client.post(
        "/api/report/preview",
        json={"sourceId": "src-test", "reportSpec": spec, "filters": {"Regions.Region": ["North"]}},
    )

    assert response.status_code == 200, response.text
    assert response.json()["visuals"][0]["options"] == ["North", "South"]
    assert response.json()["visuals"][1]["data"] == [{"label": "North", "value": 100.0}]


def test_preview_does_not_drop_unmatched_rows_without_a_cross_table_filter(monkeypatch) -> None:
    from backend.api import report as report_api

    metadata = {
        "tables": {
            "Regions": {"columns": ["Region"], "measures": []},
            "Orders": {
                "columns": ["Region", "Amount"],
                "measures": ["Total Amount"],
                "measureDefinitions": {
                    "Total Amount": {"column": "Amount", "aggregation": "SUM"},
                },
            },
        },
        "relationships": [{
            "source_table": "Orders",
            "source_column": "Region",
            "target_table": "Regions",
            "target_column": "Region",
            "confidence": 0.95,
        }],
    }
    payload = {
        "metadata": {
            "tables": {
                "Regions": {"rows": [{"Region": "North"}]},
                "Orders": {"rows": [
                    {"Region": "North", "Amount": 100},
                    {"Region": None, "Amount": 50},
                ]},
            }
        }
    }
    monkeypatch.setattr(report_api, "load_source_metadata", lambda _source_id: metadata)
    monkeypatch.setattr(report_api, "load_source_payload", lambda _source_id: payload)
    spec = {
        "page": "Orders",
        "visuals": [{
            "type": "card",
            "value": {"table": "Orders", "field": "Total Amount", "fieldType": "measure"},
        }],
    }

    response = client.post(
        "/api/report/preview",
        json={"sourceId": "src-test", "reportSpec": spec},
    )

    assert response.status_code == 200, response.text
    assert response.json()["visuals"][0]["data"] == [
        {"label": "Total Amount", "value": 150.0},
    ]


def test_report_planning_requires_ai_for_related_dimension_binding(monkeypatch) -> None:
    from backend.api import report as report_api

    metadata = {
        "tables": {
            "Orders": {"columns": ["RegionID", "Amount"], "measures": ["Total Amount"]},
            "Regions": {"columns": ["RegionID", "Region"], "measures": []},
        },
        "relationships": [{
            "source_table": "Orders",
            "source_column": "RegionID",
            "target_table": "Regions",
            "target_column": "RegionID",
            "confidence": 0.95,
        }],
    }
    monkeypatch.setattr(report_api, "load_source_metadata", lambda _source_id: metadata)

    response = client.post(
        "/api/report/plan",
        json={
            "sourceId": "src-test",
            "table": "Orders",
            "request": "Show total amount by region as a column chart",
        },
    )

    assert response.status_code == 503, response.text
    assert "AI is required for report planning" in response.json()["detail"]


def test_report_planning_requires_ai_for_chart_view_selection(monkeypatch) -> None:
    from backend.api import report as report_api

    metadata = {
        "tables": {
            "Sheet_A1_I7": {
                "columns": ["Metric", "Scenario 2024"],
                "measures": ["Total Scenario 2024"],
                "measureDefinitions": {},
                "derivedFrom": None,
            },
            "Sheet_A1_I7_ChartData": {
                "columns": ["Segment", "Scenario", "Year", "Metric", "Value"],
                "measures": ["Real GDP Growth"],
                "measureDefinitions": {
                    "Real GDP Growth": {
                        "column": "Value",
                        "filterColumn": "Metric",
                        "filterValue": "Real GDP Growth",
                        "aggregation": "SUM",
                    },
                },
                "derivedFrom": "Sheet_A1_I7",
            },
            "Sheet_A11_I17": {
                "columns": ["Metric", "Scenario 2024"],
                "measures": ["Total Scenario 2024"],
                "measureDefinitions": {},
                "derivedFrom": None,
            },
            "Sheet_A11_I17_ChartData": {
                "columns": ["Segment", "Scenario", "Year", "Metric", "Value"],
                "measures": ["Real GDP Growth"],
                "measureDefinitions": {
                    "Real GDP Growth": {
                        "column": "Value",
                        "filterColumn": "Metric",
                        "filterValue": "Real GDP Growth",
                        "aggregation": "SUM",
                    },
                },
                "derivedFrom": "Sheet_A11_I17",
            },
        },
        "relationships": [{
            "source_table": "Sheet_A1_I7",
            "source_column": "Metric",
            "target_table": "Sheet_A11_I17",
            "target_column": "Metric",
            "confidence": 0.95,
        }],
    }
    monkeypatch.setattr(report_api, "load_source_metadata", lambda _source_id: metadata)

    response = client.post(
        "/api/report/plan",
        json={
            "sourceId": "src-test",
            "table": "Sheet_A1_I7",
            "request": (
                "Create an economic dashboard with 2 visuals:\n"
                "1. Add a slicer for Scenario\n"
                "2. Show Real GDP Growth by Year as a line chart"
            ),
        },
    )

    assert response.status_code == 503, response.text
    assert "AI is required for report planning" in response.json()["detail"]


def test_report_generation_requires_reviewed_plan() -> None:
    response = client.post('/api/report/generate', json={'sourceId': 'src-missing', 'reportSpec': {}})
    assert response.status_code == 400


def test_source_id_rejects_path_traversal() -> None:
    response = client.post('/api/report/plan', json={
        'sourceId': '../../outside',
        'request': 'Show total amount by area',
    })
    assert response.status_code == 404
    assert response.json()['detail'] == 'Source ID is invalid'


def test_sql_connection_discovers_metadata_without_echoing_credentials(monkeypatch) -> None:
    class Cursor:
        def execute(self, query):
            self.query = query

        def fetchone(self):
            return (1,)

        def fetchall(self):
            assert "INFORMATION_SCHEMA.COLUMNS" in self.query
            return [("dbo", "Facts", "Area", "nvarchar"), ("dbo", "Facts", "Amount", "decimal")]

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(sql_service, "connect_sql_server", lambda payload: Connection())
    tested = client.post('/api/datasource/sql/test', json={
        'server': 'localhost', 'database': 'warehouse', 'authentication': 'sql',
        'username': 'private-user', 'password': 'private-password',
    })
    assert tested.status_code == 200, tested.text
    assert 'private-user' not in tested.text
    assert 'private-password' not in tested.text

    schema = client.post('/api/datasource/sql/schema', json={'connectionId': tested.json()['connectionId']})
    assert schema.status_code == 200, schema.text
    assert schema.json()['tables'][0]['columns'][1] == {'name': 'Amount', 'dataType': 'decimal'}
    assert schema.json()['sourceId'].startswith('src-')
