import asyncio
from unittest.mock import patch

import pytest
import httpx

from automation.ai.ai_service import AIServiceError, AzureOpenAIProvider, MockProvider, get_ai_provider, validate_workbook_understanding
from automation.semantic.semantic_validator import validate_ai_plan
from automation.semantic.workbook_profile import profile_for_ai
from automation.report_automation import create_report_spec, requested_visual_count_mode
from backend.config import get_settings


def test_ai_configuration_defaults_to_required(monkeypatch) -> None:
    monkeypatch.delenv("USE_AI", raising=False)
    settings = get_settings()
    assert settings.use_ai is True


def test_disabled_ai_ignores_optional_ai_settings(monkeypatch) -> None:
    monkeypatch.setenv("USE_AI", "false")
    monkeypatch.setenv("AI_PROVIDER", "invalid")
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "invalid")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "")

    assert get_settings().use_ai is False


@pytest.mark.parametrize(("value", "expected"), [
    ("true", True), ("1", True), ("yes", True),
    ("false", False), ("0", False), ("no", False),
])
def test_ai_boolean_configuration_values(monkeypatch, value: str, expected: bool) -> None:
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setenv("USE_AI", value)
    assert get_settings().use_ai is expected


def test_invalid_ai_boolean_configuration_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("USE_AI", "sometimes")
    with pytest.raises(ValueError, match="USE_AI"):
        get_settings()


@pytest.mark.parametrize("timeout", ["nan", "inf", "-inf", "0", "-1"])
def test_nonfinite_or_nonpositive_ai_timeout_is_rejected(monkeypatch, timeout) -> None:
    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", timeout)
    with pytest.raises(ValueError, match="AI_TIMEOUT_SECONDS"):
        get_settings()


def test_disabled_ai_does_not_initialize_a_provider(monkeypatch) -> None:
    monkeypatch.setenv("USE_AI", "false")
    with patch("automation.ai.ai_service.AzureOpenAIProvider") as azure_provider:
        with pytest.raises(AIServiceError, match="disabled"):
            get_ai_provider(get_settings())
    azure_provider.assert_not_called()


def test_enabled_azure_openai_reports_missing_server_side_key(monkeypatch) -> None:
    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "azure_openai")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    with pytest.raises(AIServiceError, match="configuration is incomplete"):
        get_ai_provider(get_settings())


def test_enabled_azure_openai_reports_missing_deployment_name(monkeypatch) -> None:
    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "secret")
    monkeypatch.setenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/default")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "")

    with pytest.raises(AIServiceError, match="configuration is incomplete"):
        get_ai_provider(get_settings())


def test_mock_provider_does_not_need_an_api_key(monkeypatch) -> None:
    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    assert isinstance(get_ai_provider(get_settings()), MockProvider)


def test_ai_workbook_profile_redacts_sample_cell_values() -> None:
    profile = {
        "workbook": "sensitive.xlsx",
        "sheets": [{
            "name": "Customers",
            "tables": [{
                "name": "Customers",
                "columns": [{"name": "Email", "sample_values": ["person@example.com"], "data_type": "string"}],
            }],
        }],
    }

    ai_profile = profile_for_ai(profile)

    assert "sample_values" not in ai_profile["sheets"][0]["tables"][0]["columns"][0]
    assert "person@example.com" not in str(ai_profile)


def test_workbook_understanding_rejects_unknown_field() -> None:
    profile = {
        "sheets": [{
            "tables": [{"name": "Sales", "columns": [{"name": "Revenue"}]}],
        }],
    }
    with pytest.raises(AIServiceError, match="unknown column"):
        validate_workbook_understanding(
            {"tables": [{"name": "Sales", "columns": [{"name": "Secret", "semantic_role": "measure"}]}]},
            profile,
        )


def test_ai_plan_rejects_unknown_visual_and_non_numeric_measures() -> None:
    metadata = {"tables": {"Sales": {
        "columns": ["Region", "Customer"],
        "columnTypes": {"Region": "string", "Customer": "string"},
        "measures": ["Total Revenue"],
    }}}
    base_visual = {
        "type": "clusteredColumnChart",
        "category": {"table": "Sales", "field": "Region", "fieldType": "column"},
        "value": {"table": "Sales", "field": "Total Revenue", "fieldType": "measure"},
    }
    with pytest.raises(ValueError, match="Unsupported visual type"):
        validate_ai_plan({"page": "Sales", "visuals": [{**base_visual, "type": "unknown"}]}, metadata, 1)

    non_numeric_metadata = {
        "tables": {"Sales": {
            "columns": ["Region", "Customer"],
            "columnTypes": {"Region": "string", "Customer": "string"},
            "measures": ["Customer"],
        }},
    }
    with pytest.raises(ValueError, match="not numeric"):
        validate_ai_plan(
            {"page": "Sales", "visuals": [{**base_visual, "value": {**base_visual["value"], "field": "Customer"}}]},
            non_numeric_metadata,
            1,
        )


def test_ai_plan_requires_exact_count_and_existing_fields() -> None:
    metadata = {"tables": {"Sales": {
        "columns": ["Region"],
        "columnTypes": {"Region": "string", "Revenue": "integer"},
        "measures": ["Total Revenue"],
    }}}
    visual = {
        "type": "clusteredColumnChart",
        "category": {"table": "Sales", "field": "Region", "fieldType": "column"},
        "value": {"table": "Sales", "field": "Total Revenue", "fieldType": "measure"},
    }
    with pytest.raises(ValueError, match="exactly 2"):
        validate_ai_plan({"page": "Sales", "visuals": [visual]}, metadata, 2)

    unknown_field = {**visual, "category": {**visual["category"], "field": "Unknown"}}
    with pytest.raises(ValueError, match="Unknown category"):
        validate_ai_plan({"page": "Sales", "visuals": [unknown_field]}, metadata, 1)


def test_numbered_visual_list_dynamically_counts_its_slicer() -> None:
    request = (
        "Create a finance margin dashboard with 4 visuals: "
        "1. KPI card for total Gross Profit "
        "2. Average Gross Margin % by Fiscal Year as a line chart "
        "3. Total Gross Profit by Business Unit as a bar chart "
        "4. Add a slicer for Scenario"
    )
    metadata = {"tables": {"Finance": {
        "columns": ["Gross Profit", "Gross Margin %", "Fiscal Year", "Business Unit", "Scenario"],
        "columnTypes": {"Gross Profit": "decimal", "Gross Margin %": "decimal", "Fiscal Year": "integer", "Business Unit": "string", "Scenario": "string"},
        "measures": ["Total Gross Profit", "Average Gross Margin %"],
        "measureDefinitions": {
            "Total Gross Profit": {"column": "Gross Profit", "aggregation": "SUM"},
            "Average Gross Margin %": {"column": "Gross Margin %", "aggregation": "AVERAGE"},
        },
    }}}
    plan = create_report_spec(request, metadata)

    assert requested_visual_count_mode(request) == "total"
    assert [visual["type"] for visual in plan["visuals"]] == [
        "card", "lineChart", "clusteredBarChart", "slicer",
    ]
    validate_ai_plan(plan, metadata, 4, "total")


def test_separately_added_slicer_keeps_chart_count_analytical() -> None:
    request = "Create 4 charts plus a slicer for Scenario"
    assert requested_visual_count_mode(request) == "analytical"


def test_ai_plan_chooses_visual_count_when_request_does_not_specify_one() -> None:
    metadata = {"tables": {"Sales": {
        "columns": ["Region"],
        "columnTypes": {"Region": "string", "Revenue": "integer"},
        "measures": ["Total Revenue"],
    }}}
    visual = {
        "type": "clusteredColumnChart",
        "category": {"table": "Sales", "field": "Region", "fieldType": "column"},
        "value": {"table": "Sales", "field": "Total Revenue", "fieldType": "measure"},
    }

    validate_ai_plan(
        {"page": "Sales", "visuals": [visual, visual.copy()]},
        metadata,
        None,
    )

    with pytest.raises(ValueError, match="at least one visual"):
        validate_ai_plan({"page": "Sales", "visuals": []}, metadata, None)


def test_ai_plan_canonicalizes_case_and_whitespace_in_field_bindings() -> None:
    metadata = {
        "tables": {
            "Sheet_A1_I7_ChartData": {
                "columns": ["Scenario", "Year"],
                "columnTypes": {"Year": "integer"},
                "measures": ["Real GDP Growth"],
                "measureDefinitions": {
                    "Real GDP Growth": {
                        "column": "Value",
                        "filterColumn": "Metric",
                        "filterValue": "Real GDP Growth",
                    },
                },
            },
        },
    }
    plan = {
        "page": "Economic Outlook",
        "visuals": [{
            "type": "slicer",
            "field": {
                "table": "sheet_a1_i7_chartdata",
                "field": " Scenario ",
                "fieldType": "column",
            },
        }],
    }

    validate_ai_plan(plan, metadata, 1)

    assert plan["visuals"][0]["field"] == {
        "table": "Sheet_A1_I7_ChartData",
        "field": "Scenario",
        "fieldType": "column",
    }


def test_mock_provider_uses_related_tables_from_the_selected_table_scope() -> None:
    metadata = {
        "preferredTable": "Orders",
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

    plan = asyncio.run(MockProvider().plan_dashboard(
        "Show total amount by region as a column chart",
        {},
        {},
        metadata,
        1,
    ))

    assert plan["visuals"][0]["value"]["table"] == "Orders"
    assert plan["visuals"][0]["category"]["table"] == "Regions"


def test_workbook_understanding_requires_all_known_fields() -> None:
    profile = {
        "sheets": [{
            "tables": [{
                "name": "Sales",
                "columns": [{"name": "Revenue"}, {"name": "Region"}],
            }],
        }],
    }
    with pytest.raises(AIServiceError, match="omitted"):
        validate_workbook_understanding(
            {"tables": [{"name": "Sales", "columns": [{"name": "Revenue", "semantic_role": "measure"}]}]},
            profile,
        )


def test_azure_provider_uses_configured_deployment(monkeypatch) -> None:
    monkeypatch.setenv("USE_AI", "true")
    monkeypatch.setenv("AI_PROVIDER", "azure_openai")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "secret-key")
    monkeypatch.setenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/default")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "report-planner")
    provider = get_ai_provider(get_settings())
    assert isinstance(provider, AzureOpenAIProvider)
    assert provider.model == "report-planner"
