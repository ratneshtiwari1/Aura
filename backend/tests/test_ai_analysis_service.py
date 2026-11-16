import asyncio

import pytest

from backend.services import ai_analysis_service


def _sales_data():
    metadata = {
        "tables": {
            "Sales Transactions": {
                "columns": ["Order Date", "Net Revenue"],
                "columnTypes": {"Order Date": "date", "Net Revenue": "decimal"},
                "columnRoles": {"Order Date": "time", "Net Revenue": "measure"},
                "measures": ["Total Net Revenue"],
                "measureDefinitions": {
                    "Total Net Revenue": {"column": "Net Revenue", "aggregation": "SUM"},
                },
            }
        }
    }
    raw_tables = {
        "Sales Transactions": {
            "rows": [
                {"Order Date": "2025-01-05", "Net Revenue": 100},
                {"Order Date": "2025-03-30", "Net Revenue": 200},
                {"Order Date": "2025-04-01", "Net Revenue": 300},
            ]
        }
    }
    return metadata, raw_tables


def test_ai_plan_interprets_quarter_request_and_deterministic_executor_uses_date_field(monkeypatch):
    metadata, raw_tables = _sales_data()
    captured = {}

    class Provider:
        model = "test-model"

        async def plan_data_question(self, question, schema, context):
            captured.update({"question": question, "schema": schema, "context": context})
            return {
                "status": "ready",
                "intent": "aggregate",
                "table": "Sales Transactions",
                "measure": "Total Net Revenue",
                "time_field": "Order Date",
                "normalized_question": "What is the total Total Net Revenue in Q1 using Order Date?",
            }

    monkeypatch.setattr(ai_analysis_service, "get_settings", lambda: type("Settings", (), {"use_ai": True, "ai_provider": "azure_openai"})())
    monkeypatch.setattr(ai_analysis_service, "get_ai_provider", lambda settings: Provider())

    result = asyncio.run(ai_analysis_service.analyze_data_question(
        metadata, raw_tables, "What was Net Revenue in Q1, using Order Date?",
        conversation=[{"role": "user", "text": "What is total net revenue?"}],
    ))

    assert result["findings"][0]["value"] == 300
    assert result["filters"] == [{"field": "Order Date", "value": "Q1"}]
    assert result["analysisPlan"]["intent"] == "aggregate"
    assert captured["question"] == "What was Net Revenue in Q1, using Order Date?"
    assert captured["context"]["conversation"] == [
        {"role": "user", "text": "What is total net revenue?"},
    ]
    assert "rows" not in str(captured["schema"]).casefold()


def test_ai_clarification_does_not_run_a_calculation(monkeypatch):
    metadata, raw_tables = _sales_data()

    class Provider:
        model = "test-model"

        async def plan_data_question(self, question, schema, context):
            return {
                "status": "needs_clarification",
                "question": "Which date field should I use?",
                "options": ["Sales Transactions[Order Date]"],
            }

    monkeypatch.setattr(ai_analysis_service, "get_settings", lambda: type("Settings", (), {"use_ai": True, "ai_provider": "azure_openai"})())
    monkeypatch.setattr(ai_analysis_service, "get_ai_provider", lambda settings: Provider())

    with pytest.raises(ai_analysis_service.AnalysisClarificationRequired) as raised:
        asyncio.run(ai_analysis_service.analyze_data_question(
            metadata, raw_tables, "What was Net Revenue in Q1?"
        ))

    result = raised.value.response("What was Net Revenue in Q1?")
    assert result["answer"] == "Which date field should I use?"
    assert "nextStep" not in result
    assert result["findings"] == []
    assert result["sourceFields"] == []
    assert result["analysisPlan"]["status"] == "needs_clarification"
    assert result["analysisPlan"]["clarificationOptions"] == ["Sales Transactions[Order Date]"]


def test_availability_answers_from_schema_without_calculating_values(monkeypatch):
    metadata, raw_tables = _sales_data()

    class Provider:
        model = "test-model"

        async def plan_data_question(self, question, schema, context):
            return {
                "status": "ready",
                "intent": "availability",
                "table": "Sales Transactions",
                "measure": "Total Net Revenue",
                "time_field": None,
                "time_grain": None,
                "group_by": ["Order Date"],
                "field": None,
                "direction": "descending",
                "limit": 5,
                "normalized_question": "Check whether total net revenue by order date is available",
            }

    monkeypatch.setattr(ai_analysis_service, "get_settings", lambda: type("Settings", (), {"use_ai": True, "ai_provider": "azure_openai"})())
    monkeypatch.setattr(ai_analysis_service, "get_ai_provider", lambda settings: Provider())

    result = asyncio.run(ai_analysis_service.analyze_data_question(
        metadata, raw_tables, "Does the data have sales by date?"
    ))

    assert result["answer"] == (
        "Yes. Sales Transactions contains measure 'Total Net Revenue' and field 'Order Date' "
        "that support this question."
    )
    assert result["method"] == "Checked the validated uploaded-workbook schema; no values were calculated."
    assert result["sourceFields"] == ["Sales Transactions.Total Net Revenue", "Sales Transactions.Order Date"]
    assert result["findings"] == []


def test_availability_uses_selected_table_schema_when_ai_unnecessarily_clarifies(monkeypatch):
    metadata, raw_tables = _sales_data()

    class Provider:
        model = "test-model"

        async def plan_data_question(self, question, schema, context):
            return {
                "status": "needs_clarification",
                "question": "Does the data include a monthly breakdown?",
                "options": ["Yes", "No"],
            }

    monkeypatch.setattr(ai_analysis_service, "get_settings", lambda: type("Settings", (), {"use_ai": True, "ai_provider": "azure_openai"})())
    monkeypatch.setattr(ai_analysis_service, "get_ai_provider", lambda settings: Provider())

    result = asyncio.run(ai_analysis_service.analyze_data_question(
        metadata,
        raw_tables,
        "Is revenue available by month?",
        selected_table="Sales Transactions",
    ))

    assert result["answer"] == (
        "Yes. Sales Transactions contains measure 'Total Net Revenue' and field 'Order Date' "
        "that support this question."
    )
    assert result["analysisPlan"]["intent"] == "availability"


def test_ai_plan_clarifies_when_ai_selects_a_column_instead_of_a_measure(monkeypatch):
    metadata, raw_tables = _sales_data()

    class Provider:
        model = "test-model"

        async def plan_data_question(self, question, schema, context):
            return {
                "status": "ready",
                "intent": "aggregate",
                "table": "Sales Transactions",
                "measure": "Invented Revenue",
                "time_field": None,
                "normalized_question": "What is total Invented Revenue?",
            }

    monkeypatch.setattr(ai_analysis_service, "get_settings", lambda: type("Settings", (), {"use_ai": True, "ai_provider": "azure_openai"})())
    monkeypatch.setattr(ai_analysis_service, "get_ai_provider", lambda settings: Provider())

    try:
        asyncio.run(ai_analysis_service.analyze_data_question(metadata, raw_tables, "What is revenue?"))
    except ai_analysis_service.AnalysisClarificationRequired as error:
        assert "executable measure" in str(error)
        assert error.options == ["Total Net Revenue"]
    else:
        raise AssertionError("AI-selected columns must be clarified, not treated as measures")


def test_ai_plan_executes_grouped_aggregation_from_structured_fields(monkeypatch):
    metadata = {
        "tables": {
            "Sales Transactions": {
                "columns": ["Region", "Net Revenue"],
                "columnTypes": {"Region": "string", "Net Revenue": "decimal"},
                "columnRoles": {"Region": "dimension", "Net Revenue": "measure"},
                "measures": ["Total Net Revenue"],
                "measureDefinitions": {
                    "Total Net Revenue": {"column": "Net Revenue", "aggregation": "SUM"},
                },
            }
        }
    }
    raw_tables = {
        "Sales Transactions": {
            "rows": [
                {"Region": "North", "Net Revenue": 100},
                {"Region": "North", "Net Revenue": 50},
                {"Region": "South", "Net Revenue": 200},
            ]
        }
    }

    class Provider:
        model = "test-model"

        async def plan_data_question(self, question, schema, context):
            return {
                "status": "ready",
                "intent": "aggregate",
                "table": "Sales Transactions",
                "measure": "Total Net Revenue",
                "time_field": None,
                "group_by": ["Region"],
                "field": None,
                "normalized_question": "Total Net Revenue grouped by Region",
            }

    monkeypatch.setattr(ai_analysis_service, "get_settings", lambda: type("Settings", (), {"use_ai": True, "ai_provider": "azure_openai"})())
    monkeypatch.setattr(ai_analysis_service, "get_ai_provider", lambda settings: Provider())

    result = asyncio.run(ai_analysis_service.analyze_data_question(
        metadata, raw_tables, "Show Total Net Revenue by Region"
    ))

    assert result["findings"] == [
        {"category": "North", "group": {"Region": "North"}, "value": 150.0},
        {"category": "South", "group": {"Region": "South"}, "value": 200.0},
    ]
    assert result["sourceFields"] == ["Sales Transactions.Total Net Revenue", "Sales Transactions.Region"]


@pytest.mark.parametrize(("region", "expected"), [
    ("North", "Total Revenue: 125.00"),
    ("South", "Total Revenue: 300.00"),
])
def test_ai_plan_returns_structured_evidence_for_filtered_total(monkeypatch, region, expected):
    metadata = {
        "tables": {
            "Sales": {
                "columns": ["Region", "Revenue"],
                "columnTypes": {"Region": "string", "Revenue": "decimal"},
                "measures": ["Total Revenue"],
                "measureDefinitions": {
                    "Total Revenue": {"column": "Revenue", "aggregation": "SUM"},
                },
            }
        }
    }
    raw_tables = {
        "Sales": {"rows": [
            {"Region": "North", "Revenue": 100},
            {"Region": "North", "Revenue": 25},
            {"Region": "South", "Revenue": 300},
        ]}
    }

    class Provider:
        model = "test-model"

        async def plan_data_question(self, question, schema, context):
            return {
                "status": "ready",
                "intent": "aggregate",
                "table": "Sales",
                "measure": "Total Revenue",
                "normalized_question": f"What is total revenue for {region} region?",
            }

    monkeypatch.setattr(ai_analysis_service, "get_settings", lambda: type("Settings", (), {"use_ai": True, "ai_provider": "azure_openai"})())
    monkeypatch.setattr(ai_analysis_service, "get_ai_provider", lambda settings: Provider())

    result = asyncio.run(ai_analysis_service.analyze_data_question(
        metadata, raw_tables, f"What is total revenue in {region} region?"
    ))

    assert result["answer"] == expected


def test_ai_ranking_executes_selected_direction_and_grouping(monkeypatch):
    metadata = {
        "tables": {
            "Sales Transactions": {
                "columns": ["Region", "Net Revenue"],
                "columnTypes": {"Region": "string", "Net Revenue": "decimal"},
                "columnRoles": {"Region": "dimension", "Net Revenue": "measure"},
                "measures": ["Total Net Revenue"],
                "measureDefinitions": {
                    "Total Net Revenue": {"column": "Net Revenue", "aggregation": "SUM"},
                },
            }
        }
    }
    raw_tables = {
        "Sales Transactions": {
            "rows": [
                {"Region": "North", "Net Revenue": 100},
                {"Region": "South", "Net Revenue": 50},
            ]
        }
    }

    class Provider:
        model = "test-model"

        async def plan_data_question(self, question, schema, context):
            return {
                "status": "ready",
                "intent": "ranking",
                "table": "Sales Transactions",
                "measure": "Total Net Revenue",
                "time_field": None,
                "group_by": ["Region"],
                "direction": "ascending",
                "limit": 1,
                "normalized_question": "Lowest Total Net Revenue by Region",
            }

    monkeypatch.setattr(ai_analysis_service, "get_settings", lambda: type("Settings", (), {"use_ai": True, "ai_provider": "azure_openai"})())
    monkeypatch.setattr(ai_analysis_service, "get_ai_provider", lambda settings: Provider())

    result = asyncio.run(ai_analysis_service.analyze_data_question(
        metadata, raw_tables, "Which region has the lowest Total Net Revenue?"
    ))

    assert result["findings"] == [
        {"category": "South", "group": {"Region": "South"}, "value": 50.0},
    ]
    assert result["answer"] == "South is lowest for Total Net Revenue at 50.00."


def test_monthly_grouping_uses_canonical_measure_even_when_question_uses_a_synonym(monkeypatch):
    metadata = {
        "tables": {
            "SalesTransactions": {
                "columns": ["Order Date", "Units"],
                "columnTypes": {"Order Date": "dateTime", "Units": "integer"},
                "columnRoles": {"Order Date": "time", "Units": "measure"},
                "measures": ["Total Units"],
            }
        }
    }
    raw_tables = {
        "SalesTransactions": {
            "rows": [
                {"Order Date": "2025-01-06 00:00:00", "Units": 4},
                {"Order Date": "2025-01-15 00:00:00", "Units": 7},
                {"Order Date": "2025-02-05 00:00:00", "Units": 10},
            ]
        }
    }

    class Provider:
        model = "test-model"

        async def plan_data_question(self, question, schema, context):
            return {
                "status": "ready",
                "intent": "aggregate",
                "table": "SalesTransactions",
                "measure": "Total Units",
                "time_field": "Order Date",
                "time_grain": "month",
                "group_by": [],
                "field": None,
                "normalized_question": "Units sold by month using Order Date",
            }

    monkeypatch.setattr(ai_analysis_service, "get_settings", lambda: type("Settings", (), {"use_ai": True, "ai_provider": "azure_openai"})())
    monkeypatch.setattr(ai_analysis_service, "get_ai_provider", lambda settings: Provider())

    result = asyncio.run(ai_analysis_service.analyze_data_question(
        metadata, raw_tables, "Show units sold by month using Order Date"
    ))

    assert result["findings"] == [
        {"category": "2025-01", "group": {"Order Date": "2025-01"}, "value": 11.0},
        {"category": "2025-02", "group": {"Order Date": "2025-02"}, "value": 10.0},
    ]
    assert result["analysisPlan"]["timeGrain"] == "month"
    assert "Order Date (month)" in result["method"]


def test_trend_accepts_a_text_quarter_dimension(monkeypatch):
    metadata = {
        "tables": {
            "Regional Sales": {
                "columns": ["Region", "Quarter", "Revenue"],
                "columnTypes": {"Region": "string", "Quarter": "string", "Revenue": "decimal"},
                "columnRoles": {"Region": "category", "Quarter": "dimension", "Revenue": "measure"},
                "measures": ["Total Revenue"],
                "measureDefinitions": {"Total Revenue": {"column": "Revenue", "aggregation": "SUM"}},
            }
        }
    }
    raw_tables = {"Regional Sales": {"rows": [
        {"Region": "North", "Quarter": "Q1", "Revenue": 100},
        {"Region": "North", "Quarter": "Q2", "Revenue": 120},
    ]}}

    class Provider:
        model = "test-model"

        async def plan_data_question(self, question, schema, context):
            return {
                "status": "ready", "intent": "trend", "table": "Regional Sales",
                "measure": "Total Revenue", "time_field": "Quarter", "time_grain": "quarter",
                "group_by": ["Region"], "field": None, "direction": "descending", "limit": 5,
                "normalized_question": "Show revenue by quarter and region",
            }

    monkeypatch.setattr(ai_analysis_service, "get_settings", lambda: type("Settings", (), {"use_ai": True, "ai_provider": "azure_openai"})())
    monkeypatch.setattr(ai_analysis_service, "get_ai_provider", lambda settings: Provider())

    result = asyncio.run(ai_analysis_service.analyze_data_question(
        metadata, raw_tables, "Show revenue by quarter for all regions"
    ))

    assert [item["category"] for item in result["findings"]] == ["Q1 / North", "Q2 / North"]
    assert result["analysisPlan"]["timeField"] == "Quarter"


def test_count_distinct_orders_by_customer_segment_does_not_sum_units(monkeypatch):
    metadata = {
        "tables": {
            "SalesTransactions": {
                "columns": ["Order ID", "Customer Segment", "Units"],
                "columnTypes": {
                    "Order ID": "string",
                    "Customer Segment": "string",
                    "Units": "integer",
                },
                "columnRoles": {
                    "Order ID": "identifier",
                    "Customer Segment": "category",
                    "Units": "measure",
                },
                "measures": ["Total Units"],
            }
        }
    }
    raw_tables = {
        "SalesTransactions": {
            "rows": [
                {"Order ID": "A", "Customer Segment": "SMB", "Units": 2},
                {"Order ID": "A", "Customer Segment": "SMB", "Units": 3},
                {"Order ID": "B", "Customer Segment": "SMB", "Units": 4},
                {"Order ID": "C", "Customer Segment": "Retail", "Units": 6},
            ]
        }
    }

    class Provider:
        model = "test-model"

        async def plan_data_question(self, question, schema, context):
            return {
                "status": "ready",
                "intent": "count_distinct",
                "table": "SalesTransactions",
                "measure": None,
                "time_field": None,
                "group_by": ["Customer Segment"],
                "field": "Order ID",
                "normalized_question": "Count orders by customer segment",
            }

    monkeypatch.setattr(ai_analysis_service, "get_settings", lambda: type("Settings", (), {"use_ai": True, "ai_provider": "azure_openai"})())
    monkeypatch.setattr(ai_analysis_service, "get_ai_provider", lambda settings: Provider())

    result = asyncio.run(ai_analysis_service.analyze_data_question(
        metadata, raw_tables, "How many orders came from each Customer Segment?"
    ))

    assert result["findings"] == [
        {
            "category": "SMB",
            "group": {"Customer Segment": "SMB"},
            "value": 2,
        },
        {
            "category": "Retail",
            "group": {"Customer Segment": "Retail"},
            "value": 1,
        },
    ]
    assert result["sourceFields"] == [
        "SalesTransactions.Order ID",
        "SalesTransactions.Customer Segment",
    ]


def test_count_distinct_does_not_group_a_year_field_by_itself(monkeypatch):
    metadata = {
        "tables": {
            "Forecast": {
                "columns": ["Year", "Revenue"],
                "columnTypes": {"Year": "integer", "Revenue": "decimal"},
                "columnRoles": {"Year": "time", "Revenue": "measure"},
                "measures": ["Total Revenue"],
            }
        }
    }
    raw_tables = {
        "Forecast": {
            "rows": [
                {"Year": 2024, "Revenue": 100},
                {"Year": 2025, "Revenue": 110},
                {"Year": 2025, "Revenue": 120},
                {"Year": 2026, "Revenue": 90},
            ]
        }
    }

    class Provider:
        model = "test-model"

        async def plan_data_question(self, question, schema, context):
            return {
                "status": "ready",
                "intent": "count_distinct",
                "table": "Forecast",
                "measure": None,
                "time_field": "Year",
                "time_grain": "year",
                "group_by": [],
                "field": "Year",
                "normalized_question": "Count the available years",
            }

    monkeypatch.setattr(ai_analysis_service, "get_settings", lambda: type("Settings", (), {"use_ai": True, "ai_provider": "azure_openai"})())
    monkeypatch.setattr(ai_analysis_service, "get_ai_provider", lambda settings: Provider())

    result = asyncio.run(ai_analysis_service.analyze_data_question(
        metadata, raw_tables, "How many years of data are available?"
    ))

    assert result["answer"] == "There are 3 distinct Year values."
    assert result["findings"] == [{"field": "Year", "distinctCount": 3}]
    assert result["analysisPlan"]["timeField"] is None
    assert result["analysisPlan"]["timeGrain"] is None


def test_result_number_format_preserves_fractional_precision() -> None:
    from backend.services.ai_analysis_service import _format_result_value

    assert _format_result_value(0.075) == '0.075'
    assert _format_result_value(1605185.0) == '1,605,185.00'
    assert _format_result_value(1e-07) == '0.0000001'
