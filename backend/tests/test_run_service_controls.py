import asyncio
from types import SimpleNamespace

import pytest

from automation.ai.ai_service import AIServiceError
from backend.services.ai_analysis_service import AnalysisClarificationRequired
from backend.services import run_service
from backend.services.identity_service import identity_context


def _settings(tmp_path):
    return SimpleNamespace(app_data_dir=tmp_path, auth_required=False, api_tokens={})


def test_step_outputs_are_chained_into_later_tools(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(run_service, "get_settings", lambda: _settings(tmp_path))
    contexts = []

    async def execute(action, source_id, goal, context):
        contexts.append(context)
        if action == "understand_workbook":
            return {"tables": [{"name": "Revenue"}]}
        return {"page": "Executive", "visuals": []}

    monkeypatch.setattr(run_service, "execute_tool", execute)
    run = run_service.create_run("src-12345678", "Create a dashboard", [
        {"action": "understand_workbook", "reason": "Map the source"},
        {"action": "plan_dashboard", "reason": "Build the plan"},
    ])

    result = asyncio.run(run_service.execute_run(run, {}))

    assert result["status"] == "completed"
    assert contexts[1]["previousStepResult"] == {"tables": [{"name": "Revenue"}]}
    assert contexts[1]["stepResults"]["0"] == contexts[1]["previousStepResult"]


def test_dashboard_retry_receives_failed_page_and_visual(monkeypatch, tmp_path) -> None:
    from backend.agents import tools

    metadata = {"tables": {"Sales": {
        "columns": ["Region", "Revenue"], "columnTypes": {"Revenue": "decimal"},
        "measures": ["Total Revenue"],
    }}, "relationships": []}
    row = {"table": "Sales", "field": "Region", "fieldType": "column"}
    value = {"table": "Sales", "field": "Total Revenue", "fieldType": "measure"}
    contexts = []

    class Provider:
        model = "test"

        async def plan_dashboard(self, goal, profile, understanding, schema, count, repair_context=None, **kwargs):
            contexts.append(repair_context)
            table = {"type": "table", "values": [value], "unexpected": "must not enter audit"}
            if repair_context["validation_error"]:
                assert "Page Details, visual 1" in repair_context["validation_error"]
                assert repair_context["rejected_visual"] == {"type": "table", "values": [value]}
                table["rows"] = [row]
            return {"status": "ready", "page": "Overview",
                    "visuals": [{"type": "card", "value": value}],
                    "additionalPages": [{"page": "Details", "visuals": [table]}]}

    monkeypatch.setattr(run_service, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(tools, "get_settings", lambda: SimpleNamespace(use_ai=True))
    monkeypatch.setattr(tools, "get_ai_provider", lambda _: Provider())
    monkeypatch.setattr(tools, "load_source_metadata", lambda _: metadata)
    monkeypatch.setattr(tools, "load_source_payload", lambda _: {"workbookProfile": {"sheets": []}, "aiUnderstanding": {"tables": []}})
    monkeypatch.setattr(run_service, "execute_tool", tools.execute_tool)
    run = run_service.create_run("src-12345678", "Create a multi-page dashboard", [
        {"action": "plan_dashboard", "reason": "Build the plan"},
    ])
    result = asyncio.run(run_service.execute_run(run, {}))
    assert result["status"] == "completed", result.get("help")
    assert result["steps"][0]["attempts"] == 2
    assert result["steps"][0]["result"]["additionalPages"][0]["visuals"][0]["rows"] == [row]
    assert len(contexts) == 2
    assert "rejectedVisual" not in str(result["events"])


def test_multi_page_dashboard_accepts_table_column_values(monkeypatch, tmp_path) -> None:
    from backend.agents import tools

    metadata = {"tables": {"KPI Summary": {
        "columns": ["KPI", "Value"], "columnTypes": {"Value": "decimal"}, "measures": [],
    }}, "relationships": []}
    kpi = {"table": "KPI Summary", "field": "KPI", "fieldType": "column"}
    value = {"table": "KPI Summary", "field": "Value", "fieldType": "column"}

    class Provider:
        model = "test"

        async def plan_dashboard(self, *args, **kwargs):
            return {"status": "ready", "page": "Overview",
                    "visuals": [{"type": "table", "rows": [kpi]}],
                    "additionalPages": [{"page": "KPI Summary", "visuals": [
                        {"type": "table", "rows": [kpi], "values": [value]}]}]}

    monkeypatch.setattr(run_service, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(tools, "get_settings", lambda: SimpleNamespace(use_ai=True))
    monkeypatch.setattr(tools, "get_ai_provider", lambda _: Provider())
    monkeypatch.setattr(tools, "load_source_metadata", lambda _: metadata)
    monkeypatch.setattr(tools, "load_source_payload", lambda _: {"workbookProfile": {"sheets": []}, "aiUnderstanding": {"tables": []}})
    monkeypatch.setattr(run_service, "execute_tool", tools.execute_tool)
    run = run_service.create_run("src-12345678", "Create a multi-page dashboard covering the workbook", [
        {"action": "plan_dashboard", "reason": "Build the plan"},
    ])
    result = asyncio.run(run_service.execute_run(run, {}))
    assert result["status"] == "completed", result.get("help")
    assert result["steps"][0]["attempts"] == 1
    visual = result["steps"][0]["result"]["additionalPages"][0]["visuals"][0]
    assert visual["rows"] == [kpi, value]
    assert visual["values"] == []


def test_schema_resolved_table_is_carried_to_later_steps_and_audit(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(run_service, "get_settings", lambda: _settings(tmp_path))
    selected_tables = []

    async def execute(action, source_id, goal, context):
        selected_tables.append(context.get("selectedTable"))
        if action == "prepare_data":
            return {"proposal": "Use the matching source", "applied": False, "planningTable": "MatchingSource"}
        return {"page": "Executive", "visuals": []}

    monkeypatch.setattr(run_service, "execute_tool", execute)
    run = run_service.create_run(
        "src-12345678",
        "Create a dashboard",
        [
            {"action": "prepare_data", "reason": "Resolve the source"},
            {"action": "plan_dashboard", "reason": "Build the plan"},
        ],
        selected_table="StaleSelection",
    )

    result = asyncio.run(run_service.execute_run(run, {"selectedTable": "StaleSelection"}))

    assert result["status"] == "completed"
    assert selected_tables == ["StaleSelection", "MatchingSource"]
    assert result["auditContext"]["selectedTable"] == "MatchingSource"
    assert any(
        event["event"] == "source_context_changed" and event.get("reason") == "schema_match"
        for event in result["events"]
    )


def test_ai_plan_is_not_marked_as_fallback_in_run_audit_metadata(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(run_service, "get_settings", lambda: _settings(tmp_path))

    async def execute(action, source_id, goal, context):
        return {
            "page": "Executive",
            "visuals": [{"type": "card"}],
            "planningEvidence": {"planner": "ai"},
        }

    monkeypatch.setattr(run_service, "execute_tool", execute)
    run = run_service.create_run(
        "src-12345678",
        "Create a dashboard",
        [{"action": "plan_dashboard", "reason": "Build the plan"}],
    )

    result = asyncio.run(run_service.execute_run(run, {}))

    assert result["status"] == "completed"
    assert result["fallbackUsed"] is False
    assert run_service.build_audit_record(result)["run"]["fallbackUsed"] is False


def test_ai_service_failure_fails_run_instead_of_requesting_user_clarification(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(run_service, "get_settings", lambda: _settings(tmp_path))

    async def execute(action, source_id, goal, context):
        raise AIServiceError("AI provider is unavailable.")

    monkeypatch.setattr(run_service, "execute_tool", execute)
    run = run_service.create_run(
        "src-12345678",
        "What is total revenue?",
        [{"action": "answer_data_question", "reason": "Analyze the request"}],
        retry_limit=2,
    )

    result = asyncio.run(run_service.execute_run(run, {}))

    assert result["status"] == "failed"
    assert result["steps"][0]["status"] == "failed"
    assert result["steps"][0]["attempts"] == 1
    assert result["help"]["message"] == "AI provider is unavailable."
    assert any(event["event"] == "run_failed" for event in result["events"])


def test_analysis_clarification_resumes_original_question_across_multiple_turns(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(run_service, "get_settings", lambda: _settings(tmp_path))
    calls = []

    async def execute(action, source_id, goal, context):
        calls.append((
            goal,
            context.get("clarificationAnswer"),
            context.get("clarificationHistory", []),
        ))
        if len(calls) == 1:
            raise AnalysisClarificationRequired(
                "Which table should I use?",
                ["Regional Sales_B5_G23", "SalesTransactions"],
                provider="azure_openai",
                model="test-model",
            )
        if len(calls) == 2:
            raise AnalysisClarificationRequired(
                "Which measure should I use?",
                ["Total Revenue", "Total Cost"],
                provider="azure_openai",
                model="test-model",
            )
        return {"answer": "Total Revenue by Region is ready.", "findings": [{"region": "North"}]}

    monkeypatch.setattr(run_service, "execute_tool", execute)
    run = run_service.create_run(
        "src-12345678",
        "Can you give me region wise data?",
        [{"action": "answer_data_question", "reason": "Analyze the request"}],
    )

    first = asyncio.run(run_service.execute_run(run, {}))
    assert first["status"] == "needs_user"
    assert first["help"]["message"] == "Which table should I use?"
    assert first["help"]["options"] == ["Regional Sales_B5_G23", "SalesTransactions"]

    second = asyncio.run(run_service.execute_run(first, {
        "clarificationAnswer": "Regional Sales Table",
        "conversation": [
            {"role": "user", "text": "Can you give me region wise data?"},
            {"role": "assistant", "text": "Which table should I use?"},
            {"role": "user", "text": "Regional Sales Table"},
        ],
    }))
    assert second["status"] == "needs_user"
    assert second["help"]["message"] == "Which measure should I use?"

    completed = asyncio.run(run_service.execute_run(second, {
        "clarificationAnswer": "Total Revenue",
        "conversation": [
            {"role": "user", "text": "Can you give me region wise data?"},
            {"role": "assistant", "text": "Which table should I use?"},
            {"role": "user", "text": "Regional Sales Table"},
            {"role": "assistant", "text": "Which measure should I use?"},
            {"role": "user", "text": "Total Revenue"},
        ],
    }))
    assert completed["status"] == "completed"
    assert calls == [
        ("Can you give me region wise data?", None, []),
        ("Can you give me region wise data?", "Regional Sales Table", [
            {"question": "Which table should I use?", "answer": "Regional Sales Table"},
        ]),
        ("Can you give me region wise data?", "Total Revenue", [
            {"question": "Which table should I use?", "answer": "Regional Sales Table"},
            {"question": "Which measure should I use?", "answer": "Total Revenue"},
        ]),
    ]


def test_validation_error_changes_retry_context(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(run_service, "get_settings", lambda: _settings(tmp_path))
    repairs = []

    async def execute(action, source_id, goal, context):
        repairs.append(context.get("repair"))
        return {} if len(repairs) == 1 else {"page": "Executive", "visuals": []}

    monkeypatch.setattr(run_service, "execute_tool", execute)
    run = run_service.create_run("src-12345678", "Create a dashboard", [
        {"action": "plan_dashboard", "reason": "Build the plan"},
    ], retry_limit=1)

    result = asyncio.run(run_service.execute_run(run, {}))

    assert result["status"] == "completed"
    assert repairs[0] is None
    assert "missing" in repairs[1]["validationError"].casefold()
    assert any(event["event"] == "step_repair" for event in result["events"])


def test_field_clarification_includes_qualified_names_with_spaces(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(run_service, "get_settings", lambda: _settings(tmp_path))

    async def execute(action, source_id, goal, context):
        raise ValueError(
            "Could not resolve category; choose one: "
            "Finance Matrix_A4_G10[Business Unit], Finance Matrix_A4_G10[Department]"
        )

    monkeypatch.setattr(run_service, "execute_tool", execute)
    run = run_service.create_run("src-12345678", "Create a dashboard", [
        {"action": "plan_dashboard", "reason": "Build the plan"},
    ], retry_limit=0)

    result = asyncio.run(run_service.execute_run(run, {}))

    assert result["status"] == "needs_user"
    assert result["help"]["message"] == "I couldn't map the requested field. Which available field should I use?"
    assert result["help"]["options"] == [
        "Use Finance Matrix_A4_G10[Business Unit]",
        "Use Finance Matrix_A4_G10[Department]",
    ]


def test_clarification_answer_survives_approval_and_resume(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(run_service, "get_settings", lambda: _settings(tmp_path))
    answers = []
    goals = []

    async def execute(action, source_id, goal, context):
        answers.append(context.get("clarificationAnswer"))
        goals.append(goal)
        if len(answers) == 1:
            raise ValueError("Could not resolve category; choose one: Revenue[Region]")
        return {"page": "Revenue", "visuals": []}

    monkeypatch.setattr(run_service, "execute_tool", execute)
    run = run_service.create_run(
        "src-12345678",
        "Create a dashboard",
        [{"action": "plan_dashboard", "reason": "Build the plan"}],
        retry_limit=0,
        requires_approval=True,
    )
    run_service.approve_run(run, run["planVersion"], "test-user")
    conversation = [{"role": "user", "text": "Create a dashboard"}]

    result = asyncio.run(run_service.execute_run(run, {"conversation": conversation}))
    assert result["status"] == "needs_user"

    conversation.append({"role": "user", "text": "Use Revenue[Region]"})
    result = asyncio.run(run_service.execute_run(run, {"conversation": conversation}))
    assert result["status"] == "awaiting_approval"
    assert run["clarificationAnswer"] == "Use Revenue[Region]"

    run_service.approve_run(run, run["planVersion"], "test-user")
    result = asyncio.run(run_service.execute_run(run, {"conversation": conversation}))

    assert result["status"] == "completed"
    assert answers == [None, "Use Revenue[Region]"]
    assert goals == ["Create a dashboard", "Create a dashboard"]


def test_cancellation_stops_before_the_next_step(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(run_service, "get_settings", lambda: _settings(tmp_path))
    run = run_service.create_run("src-12345678", "Inspect", [
        {"action": "understand_workbook", "reason": "Map the source"},
    ])
    run_service.request_cancellation(run)

    result = asyncio.run(run_service.execute_run(run, {}))

    assert result["status"] == "cancelled"
    assert result["completedSteps"] == []


def test_duplicate_execution_lease_is_rejected(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(run_service, "get_settings", lambda: _settings(tmp_path))
    run = run_service.create_run("src-12345678", "Inspect", [])
    descriptor = run_service._acquire_lease(run["runId"])
    try:
        with pytest.raises(run_service.RunBusyError, match="already executing"):
            asyncio.run(run_service.execute_run(run, {}))
    finally:
        run_service._release_lease(run["runId"], descriptor)


def test_run_history_is_persistent_owner_scoped_and_excludes_tool_outputs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(run_service, "get_settings", lambda: _settings(tmp_path))
    owner_token = identity_context.set("audit-owner")
    try:
        run = run_service.create_run(
            "src-12345678",
            "Create a finance report",
            [{"action": "plan_dashboard", "reason": "Build the requested report"}],
            source_type="excel",
            selected_table="Finance",
            report_name="Finance dashboard",
        )
        run["steps"][0]["result"] = {"page": "Finance dashboard", "rows": [{"secret": "excluded"}]}
        run["results"]["0"] = run["steps"][0]["result"]
        run_service.record_trace_id(run, "0123456789abcdef0123456789abcdef")
        audit = run_service.build_audit_record(run)
        history = run_service.list_user_runs()

        assert history[0]["runId"] == run["runId"]
        assert history[0]["selectedTable"] == "Finance"
        assert audit["run"]["traceIds"] == ["0123456789abcdef0123456789abcdef"]
        assert audit["run"]["events"][-1]["event"] == "trace_linked"
        assert "results" not in audit["run"]
        assert "result" not in audit["run"]["steps"][0]

        other_user_token = identity_context.set("different-user")
        try:
            assert run_service.list_user_runs() == []
            with pytest.raises(PermissionError):
                run_service.load_run(run["runId"])
        finally:
            identity_context.reset(other_user_token)
    finally:
        identity_context.reset(owner_token)


def test_runs_are_stored_in_timestamped_session_and_legacy_history_remains_visible(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(run_service, "get_settings", lambda: _settings(tmp_path))

    previous_run = run_service.create_run("src-12345678", "Previous session", [])
    previous_path = run_service._path(previous_run["runId"])
    archived_path = tmp_path / "sessions" / "2025-01-02_03-04-05-123456" / "runs" / previous_path.name
    archived_path.parent.mkdir(parents=True)
    previous_path.replace(archived_path)

    current_run = run_service.create_run("src-12345678", "Current session", [])
    current_path = run_service._path(current_run["runId"])
    legacy_path = tmp_path / "runs" / f"run-000000000001.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        '{"runId":"run-000000000001","ownerId":"demo-user","goal":"Legacy run","steps":[],"completedSteps":[]}',
        encoding="utf-8",
    )

    assert "sessions" in current_path.parts
    assert run_service.load_run(previous_run["runId"])["goal"] == "Previous session"
    assert {item["goal"] for item in run_service.list_user_runs()} == {
        "Previous session",
        "Current session",
        "Legacy run",
    }
