import asyncio
from types import SimpleNamespace

import pytest
from automation.ai.ai_service import AIServiceError

from backend.agents.contracts import validate_agent_plan
from backend.agents.orchestrator import AgentOrchestrator
from backend.agents.router import route_agent_request
from backend.api.agent import _contextual_goal
from backend.agents import tools as agent_tools
from backend.agents.tools import resolve_planning_table


def test_rule_router_answers_greetings_and_clarifies_vague_requests() -> None:
    greeting = route_agent_request("Hello!", has_report=False)
    assert greeting["action"] == "respond"
    assert greeting["message"]

    vague = route_agent_request("make it better", has_report=True)
    assert vague["action"] == "clarify"
    assert vague["message"].endswith("?")


@pytest.mark.parametrize("query", [
    "What are my top products?",
    "Show me the bottom performers",
])
def test_rule_router_investigates_ranked_questions_even_with_report(query: str) -> None:
    decision = route_agent_request(query, has_report=True)

    assert decision["action"] == "answer_data_question"


def test_rule_router_uses_one_grounded_tool_for_schema_and_data_questions() -> None:
    schema_question = route_agent_request("What fields are available?", has_report=False)
    year_question = route_agent_request(
        "How many years of revenue details are available in the data?",
        has_report=False,
    )

    assert schema_question["action"] == "answer_data_question"
    assert year_question["action"] == "answer_data_question"
    assert {
        contract["action"] for contract in agent_tools.public_tool_contracts()
    }.isdisjoint({"answer_schema", "investigate_data"})
    assert "answer_data_question" in {
        contract["action"] for contract in agent_tools.public_tool_contracts()
    }

def test_rule_router_distinguishes_workbook_inventory_from_report_requests() -> None:
    inventory = route_agent_request("What tables and measures are available in this workbook?", has_report=False)
    assert inventory["action"] == "understand_workbook"
    report = route_agent_request("Create a report using available tables and measures", has_report=False)
    assert report["action"] == "plan_dashboard"


@pytest.mark.parametrize("query", [
    "Show revenue by year",
    "List available regions",
    "Calculate total revenue",
    "How many years are in the data?",
])
def test_rule_router_sends_common_data_queries_to_the_unified_tool(query: str) -> None:
    assert route_agent_request(query, has_report=False)["action"] == "answer_data_question"


def test_rule_router_edits_explicit_top_n_chart_request() -> None:
    decision = route_agent_request("Add a top 10 products chart", has_report=True)

    assert decision["action"] == "edit_report"


def test_rule_router_suggests_kpis_even_with_report() -> None:
    decision = route_agent_request("Suggest KPIs for this report", has_report=True)

    assert decision["action"] == "suggest_kpis"


def test_rule_router_clarifies_unrecognized_requests() -> None:
    decision = route_agent_request("Can you help me?", has_report=False)

    assert decision["action"] == "clarify"
    assert decision["message"]


def test_rule_router_plans_explicit_dashboard_requests() -> None:
    decision = route_agent_request("Build a dashboard for sales", has_report=False)

    assert decision["action"] == "plan_dashboard"
    assert decision["requiresApproval"] is False


def test_dashboard_planning_skips_unrequested_preparation_and_keeps_explicit_review(monkeypatch) -> None:
    class Provider:
        model = "test-model"

        async def route_agent(self, request, context):
            return {
                "steps": [
                    {"action": "prepare_data", "reason": "Review the source structure"},
                    {"action": "plan_dashboard", "reason": "Draft the report preview"},
                ],
                "requires_approval": True,
            }

    monkeypatch.setattr(
        "backend.agents.orchestrator.get_settings",
        lambda: SimpleNamespace(use_ai=True, ai_provider="mock"),
    )
    monkeypatch.setattr("backend.agents.orchestrator.get_ai_provider", lambda settings: Provider())
    monkeypatch.setattr("backend.agents.orchestrator.write_ai_audit_event", lambda **kwargs: None)

    decision = asyncio.run(AgentOrchestrator().decide(
        "src-1", "Build an executive dashboard", False,
    ))

    assert decision["requiresApproval"] is False
    assert [step["action"] for step in decision["plannedSteps"]] == ["plan_dashboard"]

    reviewed = asyncio.run(AgentOrchestrator().decide(
        "src-1",
        "Review and confirm detected ranges, headers, and types before building a report.",
        False,
    ))
    assert [step["action"] for step in reviewed["plannedSteps"]] == ["prepare_data", "plan_dashboard"]


def test_report_goal_does_not_accumulate_previous_requests() -> None:
    conversation = [
        {"role": "user", "text": "Create a dashboard with seven charts"},
        {"role": "assistant", "text": "Please clarify the grouping."},
    ]

    goal = _contextual_goal(
        "Create a dashboard with four visuals",
        conversation,
        "Use Revenue[Scenario]",
    )

    assert goal == (
        "Create a dashboard with four visuals\n"
        "Clarification answer: Use Revenue[Scenario]"
    )
    assert "seven charts" not in goal


def test_dashboard_tool_uses_ai_planner_with_step_and_repair_context(monkeypatch) -> None:
    captured = {}
    metadata = {
        "tables": {"Revenue": {
            "columns": ["Region", "Amount"],
            "columnTypes": {"Region": "string", "Amount": "decimal"},
            "measures": ["Total Amount"],
            "measureDefinitions": {"Total Amount": {"column": "Amount", "aggregation": "SUM"}},
        }},
        "relationships": [],
    }

    class Provider:
        model = "azure-test"

        async def plan_dashboard(self, request, profile, understanding, available, count, repair_context=None, visual_count_mode="analytical"):
            captured.update({"request": request, "repair": repair_context})
            return {
                "status": "ready", "page": "Revenue", "visuals": [{
                    "type": "clusteredColumnChart",
                    "category": {"table": "Revenue", "field": "Region", "fieldType": "column"},
                    "value": {"table": "Revenue", "field": "Total Amount", "fieldType": "measure"},
                }],
            }

    monkeypatch.setattr(agent_tools, "load_source_metadata", lambda source_id: metadata)
    monkeypatch.setattr(agent_tools, "load_source_payload", lambda source_id: {
        "workbookProfile": {"profile": True}, "aiUnderstanding": {"tables": []},
    })
    monkeypatch.setattr(agent_tools, "profile_for_ai", lambda profile: profile)
    monkeypatch.setattr(agent_tools, "get_settings", lambda: SimpleNamespace(use_ai=True))
    monkeypatch.setattr(agent_tools, "get_ai_provider", lambda settings: Provider())

    result = asyncio.run(agent_tools._plan_dashboard_tool(
        "src-1", "Show total amount by region", {
            "stepResults": {"0": {"relationships": []}},
            "repair": {"validationError": "Previous category was invalid", "instruction": "Revise it"},
            "conversation": [
                {"role": "user", "text": f"Earlier turn {index}"}
                for index in range(24)
            ],
            "clarificationHistory": [
                {"question": "Which dataset?", "answer": "Revenue"},
                {"question": "Formatting preferences?", "answer": "No"},
            ],
        },
    ))

    assert result["page"] == "Revenue"
    assert captured["repair"]["completed_step_outputs"] == {"0": {"relationships": []}}
    assert len(captured["repair"]["conversation"]) == 24
    assert captured["repair"]["clarification_history"] == [
        {"question": "Which dataset?", "answer": "Revenue"},
        {"question": "Formatting preferences?", "answer": "No"},
    ]
    assert captured["repair"]["validation_error"] == "Previous category was invalid"


def test_dashboard_planner_defaults_optional_preferences_instead_of_prompting(monkeypatch) -> None:
    captured = []
    metadata = {
        "preferredTable": "Sales",
        "tables": {
            "Sales": {
                "columns": ["Region", "Revenue"],
                "columnTypes": {"Region": "string", "Revenue": "decimal"},
                "measures": ["Total Revenue"],
                "measureDefinitions": {
                    "Total Revenue": {"column": "Revenue", "aggregation": "SUM"},
                },
                "columnRoles": {"Region": "dimension", "Revenue": "measure"},
                "columnProfiles": {},
                "columnFormats": {},
            },
        },
        "relationships": [],
    }

    class Provider:
        model = "test"

        async def plan_dashboard(self, *args, repair_context=None, **kwargs):
            captured.append(repair_context)
            if len(captured) == 1:
                return {
                    "status": "needs_clarification",
                    "question": "Should the description for each page include a general overview?",
                    "options": [],
                }
            return {
                "status": "ready",
                "page": "Sales",
                "visuals": [{
                    "type": "clusteredBarChart",
                    "category": {"table": "Sales", "field": "Region", "fieldType": "column"},
                    "value": {"table": "Sales", "field": "Total Revenue", "fieldType": "measure"},
                }],
            }

    monkeypatch.setattr(agent_tools, "load_source_metadata", lambda _: metadata)
    monkeypatch.setattr(agent_tools, "load_source_payload", lambda _: {
        "workbookProfile": {"profile": True}, "aiUnderstanding": {"tables": []},
    })
    monkeypatch.setattr(agent_tools, "profile_for_ai", lambda profile: profile)
    monkeypatch.setattr(agent_tools, "get_settings", lambda: SimpleNamespace(use_ai=True))
    monkeypatch.setattr(agent_tools, "get_ai_provider", lambda _: Provider())
    monkeypatch.setattr(agent_tools, "normalize_ai_measure_bindings", lambda *_: [])
    monkeypatch.setattr(agent_tools, "validate_ai_plan", lambda *_: None)
    monkeypatch.setattr(agent_tools, "check_visual_request", lambda *_: None)

    result = asyncio.run(agent_tools._plan_one_page(
        "src-1", "Create a report with Total Revenue by Region", {},
    ))

    assert result["page"] == "Sales"
    assert len(captured) == 2
    assert captured[1]["clarification_history"] == [{
        "question": "Should the description for each page include a general overview?",
        "answer": "Use sensible defaults and proceed with the visuals and page titles in the original request.",
    }]


def test_dashboard_planner_stops_repeated_optional_questions_after_one_default_retry(monkeypatch) -> None:
    captured = []
    questions = [
        "What specific details should be included in the descriptions of Page 1 and Page 2?",
        "Should the descriptions for Page 1 and Page 2 include only the visual elements or also their data sources and purposes?",
        "Should the slicers for Customer Segment and Region allow multi-selection or single-selection?",
        "Should the report include any specific formatting or styling preferences for the visual elements?",
        "What should be the title for Page 1 and Page 2 in the report?",
    ]
    prior_answers = [
        {"question": "Which dataset should be used?", "answer": "sales transactions"},
        {"question": questions[0], "answer": "i already told"},
        {"question": questions[1], "answer": "visual only"},
        {"question": questions[2], "answer": "multi selection"},
    ]
    metadata = {
        "preferredTable": "Sales",
        "tables": {
            "Sales": {
                "columns": ["Region", "Revenue"],
                "columnTypes": {"Region": "string", "Revenue": "decimal"},
                "measures": ["Total Net Revenue"],
                "measureDefinitions": {
                    "Total Net Revenue": {"column": "Revenue", "aggregation": "SUM"},
                },
                "columnRoles": {"Region": "dimension", "Revenue": "measure"},
                "columnProfiles": {},
                "columnFormats": {},
            },
        },
        "relationships": [],
    }

    class Provider:
        model = "test"

        async def plan_dashboard(self, *args, repair_context=None, **kwargs):
            captured.append(repair_context)
            question_index = len(captured) - 1
            if question_index < len(questions):
                return {
                    "status": "needs_clarification",
                    "question": questions[question_index],
                    "options": [],
                }
            return {
                "status": "ready",
                "page": "Regional Performance",
                "visuals": [{
                    "type": "clusteredBarChart",
                    "category": {"table": "Sales", "field": "Region", "fieldType": "column"},
                    "value": {"table": "Sales", "field": "Total Net Revenue", "fieldType": "measure"},
                }],
            }

    monkeypatch.setattr(agent_tools, "load_source_metadata", lambda _: metadata)
    monkeypatch.setattr(agent_tools, "load_source_payload", lambda _: {
        "workbookProfile": {"profile": True}, "aiUnderstanding": {"tables": []},
    })
    monkeypatch.setattr(agent_tools, "profile_for_ai", lambda profile: profile)
    monkeypatch.setattr(agent_tools, "get_settings", lambda: SimpleNamespace(use_ai=True))
    monkeypatch.setattr(agent_tools, "get_ai_provider", lambda _: Provider())
    monkeypatch.setattr(agent_tools, "normalize_ai_measure_bindings", lambda *_: [])
    monkeypatch.setattr(agent_tools, "validate_ai_plan", lambda *_: None)
    monkeypatch.setattr(agent_tools, "check_visual_request", lambda *_: None)

    with pytest.raises(AIServiceError, match="could not apply the default"):
        asyncio.run(agent_tools._plan_one_page(
            "src-1", "Create a report with Total Net Revenue by Region", {
                "clarificationHistory": prior_answers,
            },
        ))

    assert len(captured) == 2
    final_history = captured[-1]["clarification_history"]
    assert final_history[:len(prior_answers)] == prior_answers
    assert [item["question"] for item in final_history[len(prior_answers):]] == [questions[0]]
    assert all(
        item["answer"] == "Use sensible defaults and proceed with the visuals and page titles in the original request."
        for item in final_history[len(prior_answers):]
    )


def test_page_requests_accepts_called_with_phrasing() -> None:
    from automation.report_pages import page_requests
    goal = ("Create a two-page report. Page 1 - Regional Performance: KPI card for Total Net Revenue; "
            "bar chart of Total Net Revenue by Region; slicer for Customer Segment. "
            "Page 2 called Customer Mix with a bar chart of Total Net Revenue by Customer Segment and a slicer for Region.")
    pages = page_requests(goal)
    assert [name for name, _ in pages] == ["Regional Performance", "Customer Mix"]
    assert pages[0][1].endswith("slicer for Customer Segment")
    assert pages[1][1] == "a bar chart of Total Net Revenue by Customer Segment and a slicer for Region"
    assert page_requests("Page 1 Sales Overview: a; Page 2 Product Detail: b") == [("Sales Overview", "a"), ("Product Detail", "b")]


def test_broad_page_requests_and_explicit_page_validation() -> None:
    from automation.report_pages import is_multi_page_request, page_requests, requested_page_count

    broad = "Can you draw multi page dashboard using this workbook, which cover all the scenario?"
    assert is_multi_page_request(broad)
    assert page_requests(broad) == []
    assert is_multi_page_request("Create a report with multiple pages")
    assert is_multi_page_request("Create a report with more than one page")
    assert requested_page_count("Create a three-page dashboard") == 3
    with pytest.raises(ValueError, match="between 2 and 12"):
        page_requests("Create a 13-page dashboard")
    with pytest.raises(ValueError, match="Describe each page"):
        page_requests("Create a two-page report. Page 1 Sales: Revenue")
    with pytest.raises(ValueError, match="Requested 3 pages"):
        page_requests("Create a three-page report. Page 1 Sales: Revenue; Page 2 Costs: Cost")


def test_single_page_slicer_is_not_marked_as_synchronized() -> None:
    report = {
        "page": "Overview",
        "visuals": [{"type": "card", "value": {"table": "Sales", "field": "Total Revenue", "fieldType": "measure"}}],
        "additionalPages": [{
            "page": "Forecast",
            "visuals": [{
                "type": "slicer",
                "field": {"table": "Forecast", "field": "Scenario", "fieldType": "column"},
                "syncGroup": "Forecast[Scenario]",
            }],
        }],
    }

    agent_tools._clear_ai_sync_groups(report)

    assert "syncGroup" not in report["additionalPages"][0]["visuals"][0]


def test_broad_multi_page_ai_plan_is_checked_page_by_page(monkeypatch) -> None:
    metadata = {"tables": {"Sales": {"columns": ["Region", "Revenue"], "columnTypes": {"Revenue": "decimal"}, "measures": ["Total Revenue"]}}, "relationships": []}
    calls = []

    class Provider:
        model = "test"

        async def plan_dashboard(self, goal, profile, understanding, scoped, count, repair_context=None, **kwargs):
            from copy import deepcopy
            calls.append(repair_context)
            card = {"type": "card", "value": {"table": "Sales", "field": "Total Revenue", "fieldType": "measure"}}
            return {"status": "ready", "page": "Overview", "visuals": [card],
                    "additionalPages": [{"page": "Details", "visuals": [deepcopy(card)]}]}

    monkeypatch.setattr(agent_tools, "load_source_metadata", lambda _: metadata)
    monkeypatch.setattr(agent_tools, "load_source_payload", lambda _: {"workbookProfile": {"sheets": []}, "aiUnderstanding": {"tables": []}})
    monkeypatch.setattr(agent_tools, "get_settings", lambda: SimpleNamespace(use_ai=True))
    monkeypatch.setattr(agent_tools, "get_ai_provider", lambda _: Provider())
    goal = "Can you draw multi page dashboard using this workbook, which cover all the scenario?"
    result = asyncio.run(agent_tools.execute_tool("plan_dashboard", "source", goal, {}))
    assert result["page"] == "Overview"
    assert result["additionalPages"][0]["page"] == "Details"
    assert calls[0]["multi_page"] is True
    assert calls[0]["requested_page_count"] is None

    with pytest.raises(agent_tools.DashboardPlanValidationError, match="Expected 3 report pages"):
        asyncio.run(agent_tools.execute_tool("plan_dashboard", "source", "Create a three-page dashboard", {}))

    class InvalidProvider(Provider):
        async def plan_dashboard(self, *args, **kwargs):
            result = await super().plan_dashboard(*args, **kwargs)
            result["additionalPages"][0]["visuals"][0]["value"]["field"] = "Missing"
            return result

    monkeypatch.setattr(agent_tools, "get_ai_provider", lambda _: InvalidProvider())
    with pytest.raises(agent_tools.DashboardPlanValidationError, match="Unknown"):
        asyncio.run(agent_tools.execute_tool("plan_dashboard", "source", goal, {}))


def test_report_clarification_does_not_replace_original_page_request(monkeypatch) -> None:
    goal = (
        "Create a two-page report. Page 1 - Regional Performance: KPI card for Total Net Revenue; "
        "bar chart of Total Net Revenue by Region. Page 2 called Customer Mix with a bar chart "
        "of Total Net Revenue by Customer Segment."
    )
    captured = []

    async def plan(source_id, request, context):
        captured.append((request, context))
        return {"page": "Regional Performance", "visuals": []}

    monkeypatch.setattr(agent_tools, "_plan_dashboard_tool", plan)
    result = asyncio.run(agent_tools.execute_tool(
        "plan_dashboard", "src-1", goal, {
            "clarificationAnswer": "Page 1 and Page 2",
            "clarificationHistory": [{
                "question": "What should be the title for Page 1 and Page 2?",
                "answer": "Page 1 and Page 2",
            }],
        },
    ))

    assert captured[0][0] == goal
    assert result["page"] == "Regional Performance"


def test_explicit_dataset_clarification_scopes_report_schema() -> None:
    metadata = {
        "tables": {
            "Sales Transactions": {
                "columns": ["Region", "Net Revenue"],
                "measures": ["Total Net Revenue"],
                "measureDefinitions": {"Total Net Revenue": {"aggregation": "SUM"}},
            },
            "Regional Sales": {
                "columns": ["Region", "Revenue"],
                "measures": ["Total Revenue"],
                "measureDefinitions": {"Total Revenue": {"aggregation": "SUM"}},
            },
        },
        "relationships": [],
    }

    scoped, selected = agent_tools._planning_scope(metadata, "Total Net Revenue by Region", {
        "clarificationHistory": [{
            "question": "Which dataset should be used for Total Net Revenue?",
            "answer": "Use Sales Transactions",
        }],
    })

    assert selected == "Sales Transactions"
    assert scoped["preferredTable"] == "Sales Transactions"
    assert set(scoped["tables"]) == {"Sales Transactions"}


def test_planning_scope_keeps_exact_kpi_measures_from_other_tables() -> None:
    metadata = {
        "tables": {
            "Regional Sales": {"columns": ["Region"], "measures": ["Regional Revenue", "Gross Profit"]},
            "Units": {"columns": ["Region"], "measures": ["Total Units Sold"]},
            "Transactions": {"columns": ["Region"], "measures": ["Total Net Revenue"]},
        },
        "relationships": [],
    }

    scoped, selected = agent_tools._planning_scope(
        metadata,
        "Add KPI cards for Regional Revenue, Gross Profit, Total Units Sold, and Total Net Revenue.",
        {"selectedTable": "Regional Sales"},
    )

    assert selected == "Regional Sales"
    assert set(scoped["tables"]) == {"Regional Sales", "Units", "Transactions"}


def test_page_title_questions_are_optional_and_user_numbers_are_allowed() -> None:
    assert agent_tools._is_optional_report_preference("What should be the title for Page 1 and Page 2 in the report?")
    assert agent_tools._is_optional_report_preference("Should the report include any specific formatting or styling preferences?")
    assert not agent_tools._is_optional_report_preference("Which dataset should be used for Total Net Revenue?")

    from backend.agents import graph_workflow as workflow

    async def reply(op, prompt, payload):
        return {"message": "Page 1 and Page 2 are drafted.", "evidenceKeys": ["page"]}

    original = workflow._json
    workflow._json = reply
    try:
        result = asyncio.run(workflow._response({
            "action": "plan_dashboard",
            "goal": "Create a report. Page 1 - A; Page 2 - B",
            "result": {"page": "A", "visuals": []},
        }))
    finally:
        workflow._json = original
    assert result["result"]["assistantMessage"] == "Report draft ready for review: 1 page(s), 0 visual(s)."


def test_report_response_allows_numbers_from_clarification_history(monkeypatch) -> None:
    from backend.agents import graph_workflow as workflow

    async def reply(op, prompt, payload):
        return {"message": "Your report contains Page 1 and Page 2.", "evidenceKeys": ["page"]}

    monkeypatch.setattr(workflow, "_json", reply)
    result = asyncio.run(workflow._response({
        "action": "plan_dashboard",
        "goal": "Create a report",
        "context": {"clarificationHistory": [{"question": "Page titles?", "answer": "Page 1 and Page 2"}]},
        "result": {"page": "Regional Performance", "visuals": []},
    }))
    assert result["result"]["assistantMessage"] == "Report draft ready for review: 1 page(s), 0 visual(s)."


def test_invalid_ai_plan_fails_without_local_field_recovery(monkeypatch) -> None:
    metadata = {
        "tables": {
            "Revenue": {
                "columns": ["Region", "Revenue"],
                "columnTypes": {"Region": "string", "Revenue": "decimal"},
                "columnRoles": {"Region": "dimension", "Revenue": "measure"},
                "measures": ["Total Revenue"],
                "measureDefinitions": {"Total Revenue": {"column": "Revenue", "aggregation": "SUM"}},
            }
        },
        "relationships": [],
    }

    class Provider:
        model = "azure-test"

        async def plan_dashboard(self, *args, **kwargs):
            return {"status": "ready", "page": "Revenue", "visuals": [{"type": None}]}

    monkeypatch.setattr(agent_tools, "load_source_metadata", lambda source_id: metadata)
    monkeypatch.setattr(agent_tools, "load_source_payload", lambda source_id: {
        "workbookProfile": {"profile": True}, "aiUnderstanding": {"tables": []},
    })
    monkeypatch.setattr(agent_tools, "profile_for_ai", lambda profile: profile)
    monkeypatch.setattr(agent_tools, "get_settings", lambda: SimpleNamespace(use_ai=True))
    monkeypatch.setattr(agent_tools, "get_ai_provider", lambda settings: Provider())

    with pytest.raises(ValueError):
        asyncio.run(agent_tools._plan_dashboard_tool(
            "src-1", "Show unknown metric by unknown category", {},
        ))


def test_invalid_ai_plan_does_not_use_clarification_with_local_planner(monkeypatch) -> None:
    metadata = {
        "tables": {
            "Revenue": {
                "columns": ["Region", "Revenue"],
                "columnTypes": {"Region": "string", "Revenue": "decimal"},
                "columnRoles": {"Region": "dimension", "Revenue": "measure"},
                "measures": ["Total Revenue"],
                "measureDefinitions": {"Total Revenue": {"column": "Revenue", "aggregation": "SUM"}},
            }
        },
        "relationships": [],
    }

    class Provider:
        model = "azure-test"

        async def plan_dashboard(self, *args, **kwargs):
            return {"status": "ready", "page": "Revenue", "visuals": [{"type": None}]}

    monkeypatch.setattr(agent_tools, "load_source_metadata", lambda source_id: metadata)
    monkeypatch.setattr(agent_tools, "load_source_payload", lambda source_id: {
        "workbookProfile": {"profile": True}, "aiUnderstanding": {"tables": []},
    })
    monkeypatch.setattr(agent_tools, "profile_for_ai", lambda profile: profile)
    monkeypatch.setattr(agent_tools, "get_settings", lambda: SimpleNamespace(use_ai=True))
    monkeypatch.setattr(agent_tools, "get_ai_provider", lambda settings: Provider())

    with pytest.raises(ValueError):
        asyncio.run(agent_tools._plan_dashboard_tool(
            "src-1",
            "Show total revenue by an unknown category",
            {"clarificationAnswer": "Use Revenue[Region]"},
        ))


def test_planning_table_follows_unique_schema_match_instead_of_stale_ui_selection() -> None:
    metadata = {
        "tables": {
            "CurrentSelection": {
                "columns": ["Area", "Quarter", "Sales"],
                "measures": ["Total Sales"],
            },
            "MatchingSource": {
                "columns": ["Period", "Scenario", "Capacity Growth", "Price Change"],
                "measures": ["Average Capacity Growth", "Average Price Change"],
            },
        },
        "relationships": [],
    }

    resolved = resolve_planning_table(
        metadata,
        "Compare capacity growth and price change across scenarios",
        "CurrentSelection",
    )

    assert resolved == "MatchingSource"


def test_qualified_clarification_field_overrides_schema_scoring() -> None:
    metadata = {
        "tables": {
            "Alpha": {"columns": ["Region", "Amount"], "measures": ["Total Amount"]},
            "Beta": {"columns": ["Period", "Value"], "measures": ["Total Value"]},
        },
        "relationships": [],
    }

    assert resolve_planning_table(
        metadata,
        "Create a dashboard",
        "Alpha",
        "Use Beta[Period]",
    ) == "Beta"


def test_conversational_actions_require_a_visible_message() -> None:
    with pytest.raises(ValueError, match="require a concise message"):
        validate_agent_plan({
            "steps": [{"action": "respond", "reason": "Greeting"}],
            "requires_approval": False,
        }, has_report=False)

    result = validate_agent_plan({
        "steps": [{"action": "clarify", "reason": "Ambiguous request"}],
        "requires_approval": False,
        "message": "Which metric should the chart use?",
    }, has_report=False)
    assert result["message"] == "Which metric should the chart use?"


def test_agent_plan_orders_discovery_before_dashboard_planning() -> None:
    result = validate_agent_plan({
        "steps": [
            {"action": "plan_dashboard", "reason": "Build the requested report"},
            {"action": "inspect_relationships", "reason": "Verify table joins"},
        ],
        "requires_approval": True,
    }, has_report=False)

    assert [step["action"] for step in result["plannedSteps"]] == [
        "inspect_relationships", "plan_dashboard",
    ]


def test_ai_router_receives_redacted_schema_and_returns_a_message(monkeypatch, caplog) -> None:
    import logging

    from backend.logging_config import agent_run_id_context

    captured = {}
    audit_events = []

    class Provider:
        model = "test-model"

        async def route_agent(self, request, context):
            captured.update(context)
            return {
                "steps": [{"action": "respond", "reason": "Capability question"}],
                "requires_approval": False,
                "message": "I can build a report from the available Sales fields.",
            }

    monkeypatch.setattr(
        "backend.agents.orchestrator.get_settings",
        lambda: SimpleNamespace(use_ai=True, ai_provider="mock"),
    )
    monkeypatch.setattr("backend.agents.orchestrator.get_ai_provider", lambda settings: Provider())
    monkeypatch.setattr(
        "backend.agents.orchestrator.write_ai_audit_event",
        lambda **kwargs: audit_events.append(kwargs),
    )

    token = agent_run_id_context.set("run-test-123")
    try:
        with caplog.at_level(logging.INFO, logger="autobi.agent"):
            decision = asyncio.run(AgentOrchestrator().decide(
                "src-1",
                "What can you help me with?",
                False,
                "Sales",
                {"tables": {"Sales": {"columns": ["Region"], "measures": ["Total Revenue"]}}},
            ))
    finally:
        agent_run_id_context.reset(token)

    assert decision["action"] == "respond"
    assert decision["message"].startswith("I can build")
    assert captured["available_schema"]["tables"] == [{
        "name": "Sales", "columns": ["Region"], "measures": ["Total Revenue"],
    }]
    assert "rows" not in str(captured["available_schema"]).casefold()
    step_records = [
        record for record in caplog.records
        if record.name == "autobi.agent" and record.event.startswith("agent.step.")
    ]
    assert [record.event for record in step_records] == ["agent.step.started", "agent.step.completed"]
    assert step_records[1].step == "route_request"
    assert step_records[1].outcome == "succeeded"
    assert step_records[1].latency_ms >= 0
    assert all("What can you help me with?" not in record.getMessage() for record in step_records)
    assert audit_events[0]["context"] == {"fallback_used": False}
    assert "request" not in audit_events[0]["context"]


def test_ai_router_failure_is_logged_without_rule_fallback_or_user_content(monkeypatch, caplog) -> None:
    import logging

    audit_events = []

    class Provider:
        model = "test-model"

        async def route_agent(self, request, context):
            raise ValueError("provider output rejected")

    monkeypatch.setattr(
        "backend.agents.orchestrator.get_settings",
        lambda: SimpleNamespace(use_ai=True, ai_provider="mock"),
    )
    monkeypatch.setattr("backend.agents.orchestrator.get_ai_provider", lambda settings: Provider())
    monkeypatch.setattr(
        "backend.agents.orchestrator.write_ai_audit_event",
        lambda **kwargs: audit_events.append(kwargs),
    )

    with caplog.at_level(logging.INFO, logger="autobi.agent"):
        with pytest.raises(ValueError, match="provider output rejected"):
            asyncio.run(AgentOrchestrator().decide(
                "src-1", "Hello, private request content", False,
            ))

    failed_record = next(record for record in caplog.records if record.event == "agent.step.failed")
    assert failed_record.failure_type == "ValueError"
    assert not any(record.event in {"agent.step.fallback", "agent.step.completed"} for record in caplog.records)
    assert all("private request content" not in record.getMessage() for record in caplog.records)
    assert audit_events == []
