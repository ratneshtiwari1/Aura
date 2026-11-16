from backend.services.report_planner import build_report_plan


def test_explicit_pages_repair_multi_page_response_with_prior_conversation(monkeypatch) -> None:
    import asyncio
    from copy import deepcopy
    from types import SimpleNamespace

    from backend.agents import tools

    metadata = {"tables": {"Sales": {
        "columns": ["Region"], "measures": ["Revenue"],
    }}, "relationships": []}
    calls = []

    class Provider:
        model = "test"

        async def plan_dashboard(self, goal, profile, understanding, scoped, count, repair_context=None, **kwargs):
            calls.append((goal, deepcopy(repair_context)))
            card = {"type": "card", "value": {"table": "Sales", "field": "Revenue", "fieldType": "measure"}}
            result = {"status": "ready", "page": "Sales", "visuals": [card]}
            if len(calls) == 1:
                result["additionalPages"] = [{"page": "Old report", "visuals": [deepcopy(card)]}]
            return result

    monkeypatch.setattr(tools, "load_source_metadata", lambda _: metadata)
    monkeypatch.setattr(tools, "load_source_payload", lambda _: {
        "workbookProfile": {"sheets": []}, "aiUnderstanding": {"tables": []},
    })
    monkeypatch.setattr(tools, "get_settings", lambda: SimpleNamespace(use_ai=True))
    monkeypatch.setattr(tools, "get_ai_provider", lambda _: Provider())
    goal = (
        "Create a 4-page sales report:\n"
        "Page 1: Executive overview.\nPage 2: Regional performance.\n"
        "Page 3: Product analysis.\nPage 4: Transaction details."
    )
    plan = asyncio.run(tools.execute_tool("plan_dashboard", "source", goal, {
        "conversation": [{"role": "user", "text": "Create a six-page dashboard"}],
    }))

    assert [plan["page"], *(page["page"] for page in plan["additionalPages"])] == [
        "Page 1", "Page 2", "Page 3", "Page 4",
    ]
    assert len(calls) == 5
    assert all(call[1]["multi_page"] is False for call in calls)
    assert "single-page request" in calls[1][1]["validation_error"]
    assert calls[0][0] == calls[1][0]


def test_unsupported_cross_page_features_fail_without_retry(monkeypatch, tmp_path) -> None:
    import asyncio
    from types import SimpleNamespace

    from backend.services import run_service

    monkeypatch.setattr(run_service, "get_settings", lambda: SimpleNamespace(app_data_dir=tmp_path))
    goal = (
        "Create a 4-page sales report: Page 1: Overview. Page 2: margin heatmap. "
        "Page 3: Product analysis. Page 4: Transaction detail. "
        "Add synchronized Region and Quarter slicers across relevant pages and navigation buttons on every page."
    )
    run = run_service.create_run(
        "src-12345678", goal, [{"action": "plan_dashboard", "reason": "Plan the dashboard"}], retry_limit=2,
    )
    completed = asyncio.run(run_service.execute_run(run, {}))
    assert completed["status"] == "failed"
    assert completed["steps"][0]["attempts"] == 1
    assert all(feature in completed["help"]["message"] for feature in (
        "heatmaps", "Power BI Desktop",
    ))


def test_ordinary_or_explicitly_excluded_features_are_allowed() -> None:
    from backend.services.report_planner import validate_requested_report_features

    validate_requested_report_features("Add Region slicers to every page.")
    validate_requested_report_features("Create a report without synchronized slicers, navigation buttons, or heatmaps.")


def test_multi_page_dashboard_recovers_mislabelled_kpi_table(monkeypatch, tmp_path) -> None:
    import asyncio
    import json
    from types import SimpleNamespace

    from automation.report_automation import generate_pbir
    from automation.report_pages import report_pages
    from backend.agents import tools
    from backend.services.pbir_service import ensure_report_layout

    metadata = {
        "tables": {
            "KPI Summary": {
                "columns": ["KPI", "Value"],
                "columnTypes": {"KPI": "string", "Value": "string"},
                "measures": [],
            },
            "Sales": {
                "columns": ["Region"],
                "measures": ["Revenue"],
            },
        },
        "relationships": [],
    }

    class Provider:
        model = "test"

        async def plan_dashboard(self, *args, **kwargs):
            return {
                "status": "ready",
                "page": "Sales",
                "visuals": [{"type": "card", "value": {
                    "table": "Sales", "field": "Revenue", "fieldType": "measure",
                }}],
                "additionalPages": [{
                    "page": "KPI Summary",
                    "visuals": [{
                        "type": "table",
                        "rows": [{"table": "KPI Summary", "field": "KPI", "fieldType": "column"}],
                        "values": [{"table": "KPI Summary", "field": "Value", "fieldType": "measure"}],
                    }],
                }],
            }

    monkeypatch.setattr(tools, "load_source_metadata", lambda _: metadata)
    monkeypatch.setattr(tools, "load_source_payload", lambda _: {
        "workbookProfile": {"sheets": []}, "aiUnderstanding": {"tables": []},
    })
    monkeypatch.setattr(tools, "get_settings", lambda: SimpleNamespace(use_ai=True))
    monkeypatch.setattr(tools, "get_ai_provider", lambda _: Provider())
    plan = asyncio.run(tools.execute_tool(
        "plan_dashboard", "source", "Create a multi-page dashboard covering the entire workbook", {},
    ))
    table = plan["additionalPages"][0]["visuals"][0]
    assert table["rows"] == [
        {"table": "KPI Summary", "field": "KPI", "fieldType": "column"},
        {"table": "KPI Summary", "field": "Value", "fieldType": "column"},
    ]
    assert table["values"] == []

    (tmp_path / "Sales.SemanticModel").mkdir()
    ensure_report_layout(tmp_path / "Report", "../Sales.SemanticModel")
    for page in report_pages(plan):
        generate_pbir(tmp_path / "Report", page, metadata, root=tmp_path)
    charts = [json.loads(path.read_text()) for path in (tmp_path / "Report").rglob("visual.json")]
    generated_table = next(chart for chart in charts if chart.get("visual", {}).get("visualType") == "tableEx")
    projections = generated_table["visual"]["query"]["queryState"]["Values"]["projections"]
    assert [item["field"]["Column"]["Property"] for item in projections] == ["KPI", "Value"]


def test_multi_page_planning_repairs_invalid_visual_on_the_correct_page(monkeypatch) -> None:
    import asyncio
    from copy import deepcopy
    from types import SimpleNamespace

    import pytest

    from automation.report_automation import create_report_spec
    from backend.agents import tools

    metadata = {
        "tables": {
            "SalesTransactions": {
                "columns": ["Order Date", "Region", "Customer Segment", "Net Revenue"],
                "columnTypes": {"Order Date": "date", "Region": "string", "Customer Segment": "string", "Net Revenue": "decimal"},
                "columnRoles": {"Order Date": "time", "Region": "category", "Customer Segment": "category", "Net Revenue": "measure"},
                "measures": ["Total Net Revenue"],
                "measureDefinitions": {"Total Net Revenue": {"column": "Net Revenue", "aggregation": "SUM"}},
            },
        },
        "relationships": [],
    }
    request = (
        "Create a two-page report. Page 1 - Regional Performance: KPI card for Total Net Revenue; "
        "bar chart of Total Net Revenue by Region; line chart of Total Net Revenue by Order Date; "
        "slicer for Customer Segment. Page 2 called Customer Mix with a bar chart of Total Net "
        "Revenue by Customer Segment and a slicer for Region."
    )
    calls = []

    class Provider:
        model = "test"

        def __init__(self, always_invalid=False):
            self.always_invalid = always_invalid

        async def plan_dashboard(self, goal, profile, understanding, scoped, count, repair_context=None, **kwargs):
            calls.append((goal, deepcopy(repair_context)))
            plan = create_report_spec(goal, scoped)
            if "KPI card" in goal and (self.always_invalid or repair_context["validation_error"] is None):
                plan["visuals"][0]["type"] = "kpi"
            return {"status": "ready", **plan}

    monkeypatch.setattr(tools, "load_source_metadata", lambda _: metadata)
    monkeypatch.setattr(tools, "load_source_payload", lambda _: {
        "workbookProfile": {"sheets": []}, "aiUnderstanding": {"tables": []},
    })
    monkeypatch.setattr(tools, "get_settings", lambda: SimpleNamespace(use_ai=True))
    monkeypatch.setattr(tools, "get_ai_provider", lambda _: Provider())
    plan = asyncio.run(tools._plan_dashboard_tool("source", request, {}))

    assert len(calls) == 3
    assert calls[0][0] == calls[1][0]
    assert '"type": "card"' in calls[1][1]["validation_error"]
    assert calls[2][1]["validation_error"] is None
    assert [visual["type"] for visual in plan["visuals"]] == ["card", "clusteredBarChart", "lineChart", "slicer"]
    assert plan["page"] == "Regional Performance"
    assert plan["additionalPages"][0]["page"] == "Customer Mix"
    assert [visual["type"] for visual in plan["additionalPages"][0]["visuals"]] == ["clusteredBarChart", "slicer"]
    assert plan["visuals"][0]["value"]["field"] == "Total Net Revenue"
    assert [visual.get("category", visual.get("field"))["field"] for visual in plan["visuals"][1:]] == [
        "Region", "Order Date", "Customer Segment",
    ]
    assert [visual.get("category", visual.get("field"))["field"] for visual in plan["additionalPages"][0]["visuals"]] == [
        "Customer Segment", "Region",
    ]

    class SecondPageProvider:
        model = "test"

        async def plan_dashboard(self, goal, profile, understanding, scoped, count, repair_context=None, **kwargs):
            calls.append((goal, deepcopy(repair_context)))
            plan = create_report_spec(goal, scoped)
            if "KPI card" not in goal and repair_context["validation_error"] is None:
                plan["visuals"][0]["category"]["field"] = "Region"
            return {"status": "ready", **plan}

    calls.clear()
    monkeypatch.setattr(tools, "get_ai_provider", lambda _: SecondPageProvider())
    second_page_plan = asyncio.run(tools._plan_dashboard_tool("source", request, {}))
    assert len(calls) == 3
    assert calls[1][0] == calls[2][0]
    assert '"field": "Customer Segment"' in calls[2][1]["validation_error"]
    assert second_page_plan["additionalPages"][0]["visuals"][0]["category"]["field"] == "Customer Segment"

    calls.clear()
    monkeypatch.setattr(tools, "get_ai_provider", lambda _: Provider(always_invalid=True))
    with pytest.raises(ValueError, match="Page Regional Performance: The proposed visuals differ"):
        asyncio.run(tools._plan_dashboard_tool("source", request, {}))
    assert len(calls) == 2


def test_explicit_visual_list_does_not_fall_back_to_unrequested_charts() -> None:
    metadata = {
        "preferredTable": "Sales",
        "tables": {
            "Sales": {
                "columns": ["Year", "Region", "Revenue", "Units"],
                "columnTypes": {"Year": "integer", "Region": "string", "Revenue": "decimal", "Units": "integer"},
                "columnRoles": {"Year": "time", "Region": "geographic", "Revenue": "measure", "Units": "measure"},
                "columnProfiles": {},
                "columnFormats": {},
                "measures": ["Total Revenue", "Total Units"],
                "measureDefinitions": {
                    "Total Revenue": {"column": "Revenue", "aggregation": "SUM"},
                    "Total Units": {"column": "Units", "aggregation": "SUM"},
                },
            }
        },
        "relationships": [],
    }

    request = (
        "Create a visual dashboard 1) KPI card for Total revenue, "
        "2) KPI card for total units 3) Slicer for year "
        "4) Slicer for Scenario 5) Slicer for region"
    )

    try:
        build_report_plan(request, metadata)
    except ValueError as exc:
        assert "Could not resolve slicer field" in str(exc)
    else:
        raise AssertionError("An unavailable requested slicer must not be replaced with other visuals")


def test_qualified_clarification_resolves_ambiguous_slicer_field() -> None:
    metadata = {
        "tables": {
            "Regional Sales": {
                "columns": ["Region"],
                "measures": ["Total Net Revenue"],
                "measureDefinitions": {"Total Net Revenue": {"aggregation": "SUM"}},
            },
            "Sales Transactions": {
                "columns": ["Region"],
                "measures": [],
            },
        },
        "relationships": [],
    }

    plan = build_report_plan(
        "Create a report with a slicer for Region.",
        metadata,
        "Use Regional Sales[Region]",
    )

    assert plan["visuals"] == [{
        "type": "slicer",
        "field": {"table": "Regional Sales", "field": "Region", "fieldType": "column"},
    }]


def test_unrelated_qualified_clarification_does_not_resolve_slicer_field() -> None:
    metadata = {
        "tables": {
            "Regional Sales": {"columns": ["Region"], "measures": []},
            "Sales Transactions": {"columns": ["Region"], "measures": []},
            "Calendar": {"columns": ["Order Date"], "measures": []},
        },
        "relationships": [],
    }

    try:
        build_report_plan(
            "Create a report with a slicer for Region.",
            metadata,
            "Use Calendar[Order Date]",
        )
    except ValueError as exc:
        assert "Ambiguous slicer field" in str(exc)
    else:
        raise AssertionError("A clarification for a different field must not resolve the slicer")


def test_schema_recovery_can_move_from_an_incompatible_preferred_table() -> None:
    metadata = {
        "preferredTable": "Unrelated",
        "tables": {
            "Unrelated": {
                "columns": ["Area", "Amount"],
                "columnTypes": {"Area": "string", "Amount": "decimal"},
                "columnRoles": {"Area": "category", "Amount": "measure"},
                "columnProfiles": {},
                "columnFormats": {},
                "measures": ["Total Amount"],
                "measureDefinitions": {"Total Amount": {"column": "Amount", "aggregation": "SUM"}},
            },
            "Outlook": {
                "columns": ["Period", "Scenario", "Alpha Growth %", "Beta Rate %"],
                "columnTypes": {"Period": "integer", "Scenario": "string", "Alpha Growth %": "decimal", "Beta Rate %": "decimal"},
                "columnRoles": {"Period": "time", "Scenario": "category", "Alpha Growth %": "measure", "Beta Rate %": "measure"},
                "columnProfiles": {"Period": {"min": 2024, "max": 2028}},
                "columnFormats": {"Alpha Growth %": "0.0%", "Beta Rate %": "0.0%"},
                "measures": ["Average Alpha Growth %", "Average Beta Rate %"],
                "measureDefinitions": {
                    "Average Alpha Growth %": {"column": "Alpha Growth %", "aggregation": "AVERAGE"},
                    "Average Beta Rate %": {"column": "Beta Rate %", "aggregation": "AVERAGE"},
                },
            },
        },
        "relationships": [],
    }

    plan = build_report_plan(
        "Create an outlook dashboard comparing alpha growth and beta rate across scenarios from 2024 to 2028.",
        metadata,
    )

    assert all(visual["category"] == {"table": "Outlook", "field": "Period", "fieldType": "column"} for visual in plan["visuals"])
    assert all(visual["legend"] == {"table": "Outlook", "field": "Scenario", "fieldType": "column"} for visual in plan["visuals"])
    assert {visual["value"]["field"] for visual in plan["visuals"]} == {
        "Average Alpha Growth %", "Average Beta Rate %",
    }


def test_ai_forecast_plan_passes_explicit_visual_validation(monkeypatch, tmp_path) -> None:
    import asyncio
    from types import SimpleNamespace

    from backend.agents import tools
    from backend.services import run_service

    metadata = {
        "preferredTable": "Forecast",
        "tables": {
            "Forecast": {
                "columns": ["Year", "Scenario", "GDP Growth %"],
                "columnTypes": {"Year": "integer", "Scenario": "string", "GDP Growth %": "decimal"},
                "columnRoles": {"Year": "time", "Scenario": "category", "GDP Growth %": "measure"},
                "measures": ["Average GDP Growth %"],
                "measureDefinitions": {"Average GDP Growth %": {"column": "GDP Growth %", "aggregation": "AVERAGE"}},
            },
        },
        "relationships": [],
    }
    chart = {
        "type": "lineChart",
        "category": {"table": "Forecast", "field": "Year", "fieldType": "column"},
        "values": [{"table": "Forecast", "field": "Average GDP Growth %", "fieldType": "measure"}],
    }
    slicer = {"type": "slicer", "field": {"table": "Forecast", "field": "Scenario", "fieldType": "column"}}

    class Provider:
        calls = 0

        async def plan_dashboard(self, *args, **kwargs):
            self.calls += 1
            return {"status": "ready", "page": "Forecast", "visuals": [chart, slicer]}

    provider = Provider()
    monkeypatch.setattr(tools, "load_source_metadata", lambda _: metadata)
    monkeypatch.setattr(tools, "load_source_payload", lambda _: {
        "workbookProfile": {"sheets": []}, "aiUnderstanding": {"tables": []},
    })
    monkeypatch.setattr(tools, "get_settings", lambda: SimpleNamespace(use_ai=True))
    monkeypatch.setattr(tools, "get_ai_provider", lambda _: provider)
    result = asyncio.run(tools._plan_one_page(
        "source",
        "\u201cCreate a forecast dashboard with a line chart of Average GDP Growth % by Year and a slicer for Scenario.",
        {},
    ))

    assert result["visuals"] == [chart, slicer]
    assert result["planningEvidence"]["planner"] == "ai"
    assert provider.calls == 1

    repairs = []

    class RepairingProvider:
        async def plan_dashboard(self, *args, repair_context=None, **kwargs):
            repairs.append(repair_context)
            if len(repairs) == 1:
                wrong_chart = {**chart, "category": slicer["field"]}
                return {"status": "ready", "page": "Forecast", "visuals": [wrong_chart, slicer]}
            return {"status": "ready", "page": "Forecast", "visuals": [chart, slicer]}

    monkeypatch.setattr(tools, "get_ai_provider", lambda _: RepairingProvider())
    monkeypatch.setattr(run_service, "get_settings", lambda: SimpleNamespace(app_data_dir=tmp_path))
    monkeypatch.setattr(run_service, "execute_tool", tools.execute_tool)
    run = run_service.create_run(
        "src-12345678",
        "Create a forecast dashboard with a line chart of Average GDP Growth % by Year and a slicer for Scenario.",
        [{"action": "plan_dashboard", "reason": "Plan the dashboard"}],
        retry_limit=1,
    )
    completed = asyncio.run(run_service.execute_run(run, {}))
    assert completed["status"] == "completed"
    assert completed["steps"][0]["attempts"] == 2
    assert '"category": {"table": "Forecast", "field": "Year"' in repairs[1]["validation_error"]
