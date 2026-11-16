from datetime import datetime

from backend.services.analysis_service import investigate, kpi_suggestions


def test_kpi_suggestions_use_existing_measures_without_domain_assumptions() -> None:
    metadata = {
        "tables": {
            "Clinical": {
                "measures": ["Total Admissions", "Average Wait Time"],
            }
        }
    }

    suggestions = kpi_suggestions(metadata)

    assert [item["name"] for item in suggestions] == ["Total Admissions", "Average Wait Time"]
    assert all(item["table"] == "Clinical" for item in suggestions)
    assert all(item["dax"] == "" for item in suggestions)


def test_investigate_resolves_generic_measure_and_dimension() -> None:
    metadata = {
        "tables": {
            "Operations": {
                "columns": ["Facility", "Incidents"],
                "columnTypes": {"Facility": "string", "Incidents": "integer"},
                "measures": ["Total Incidents"],
                "measureDefinitions": {"Total Incidents": {"column": "Incidents", "aggregation": "SUM"}},
            }
        }
    }
    raw_tables = {
        "Operations": {
            "rows": [
                {"Facility": "North", "Incidents": 3},
                {"Facility": "South", "Incidents": 8},
                {"Facility": "North", "Incidents": 2},
            ]
        }
    }

    result = investigate(metadata, raw_tables, "Which facility has the highest incidents?")

    assert result["findings"][0] == {"category": "South", "value": 8.0}
    assert result["sourceFields"] == ["Operations.Facility", "Operations.Total Incidents"]


def test_investigate_honors_average_aggregation_and_orders_time() -> None:
    metadata = {
        "tables": {
            "Sensors": {
                "columns": ["Year", "Temperature"],
                "columnTypes": {"Year": "integer", "Temperature": "decimal"},
                "measures": ["Average Temperature"],
                "measureDefinitions": {
                    "Average Temperature": {"column": "Temperature", "aggregation": "AVERAGE"}
                },
            }
        }
    }
    raw_tables = {
        "Sensors": {
            "rows": [
                {"Year": 2025, "Temperature": 20},
                {"Year": 2024, "Temperature": 10},
                {"Year": 2024, "Temperature": 14},
            ]
        }
    }

    result = investigate(metadata, raw_tables, "Show the average temperature trend over year")

    assert result["findings"] == [
        {"category": "2024", "value": 12.0},
        {"category": "2025", "value": 20.0},
    ]


def test_investigate_compares_any_two_named_measures() -> None:
    metadata = {
        "tables": {
            "Delivery": {
                "columns": ["Team", "Completed Tasks", "Planned Tasks"],
                "columnTypes": {"Team": "string", "Completed Tasks": "integer", "Planned Tasks": "integer"},
                "measures": ["Total Completed Tasks", "Total Planned Tasks"],
                "measureDefinitions": {
                    "Total Completed Tasks": {"column": "Completed Tasks", "aggregation": "SUM"},
                    "Total Planned Tasks": {"column": "Planned Tasks", "aggregation": "SUM"},
                },
            }
        }
    }
    raw_tables = {
        "Delivery": {
            "rows": [
                {"Team": "A", "Completed Tasks": 9, "Planned Tasks": 10},
                {"Team": "B", "Completed Tasks": 14, "Planned Tasks": 12},
            ]
        }
    }

    result = investigate(metadata, raw_tables, "Compare completed tasks versus planned tasks by team")

    assert len(result["sourceFields"]) == 3
    assert {result["findings"][0]["firstMeasure"], result["findings"][0]["secondMeasure"]} == {
        "Total Completed Tasks", "Total Planned Tasks",
    }
    assert {item["category"] for item in result["findings"]} == {"A", "B"}


def test_investigate_asks_for_fields_when_multiple_measures_are_ambiguous() -> None:
    metadata = {
        "tables": {
            "Mixed": {
                "columns": ["Group", "Alpha", "Beta"],
                "columnTypes": {"Group": "string", "Alpha": "integer", "Beta": "integer"},
                "measures": ["Total Alpha", "Total Beta"],
            }
        }
    }

    result = investigate(metadata, {"Mixed": {"rows": [{"Group": "A", "Alpha": 1, "Beta": 2}]}}, "Tell me something")

    assert result["findings"] == []
    assert "Name the measure" in result["nextStep"]


def test_ranking_does_not_confuse_selling_price_with_sales_or_product_category_with_product() -> None:
    metadata = {
        "tables": {
            "Sales": {
                "columns": ["Product Category", "Region", "Sales Amount", "Average Selling Price"],
                "columnTypes": {
                    "Product Category": "string",
                    "Region": "string",
                    "Sales Amount": "decimal",
                    "Average Selling Price": "decimal",
                },
                "measures": ["Total Sales Amount", "Average Selling Price"],
                "measureDefinitions": {
                    "Total Sales Amount": {"column": "Sales Amount", "aggregation": "SUM"},
                    "Average Selling Price": {"column": "Average Selling Price", "aggregation": "AVERAGE"},
                },
            }
        }
    }
    raw_tables = {
        "Sales": {
            "rows": [
                {"Product Category": "Services", "Region": "East", "Sales Amount": 100, "Average Selling Price": 2400},
                {"Product Category": "Furniture", "Region": "West", "Sales Amount": 900, "Average Selling Price": 1750},
            ]
        }
    }

    result = investigate(metadata, raw_tables, "Which product has the highest selling?")

    assert result["findings"] == []
    assert result["sourceFields"] == []
    assert "metric:" in result["nextStep"]
    assert "grouping:" in result["nextStep"]


def test_investigate_counts_available_years_from_the_relevant_time_field() -> None:
    metadata = {
        "preferredTable": "Forecast",
        "tables": {
            "Forecast": {
                "columns": ["Year", "Scenario", "Revenue"],
                "columnTypes": {"Year": "integer", "Scenario": "string", "Revenue": "decimal"},
                "columnRoles": {"Year": "time", "Scenario": "dimension", "Revenue": "measure"},
                "measures": ["Total Revenue"],
                "measureDefinitions": {"Total Revenue": {"column": "Revenue", "aggregation": "SUM"}},
            }
        },
    }
    raw_tables = {
        "Forecast": {
            "rows": [
                {"Year": 2024, "Scenario": "Base", "Revenue": 100},
                {"Year": 2025, "Scenario": "Base", "Revenue": 110},
                {"Year": 2025, "Scenario": "Optimistic", "Revenue": 120},
                {"Year": 2026, "Scenario": "Downside", "Revenue": 90},
            ]
        }
    }

    result = investigate(
        metadata,
        raw_tables,
        "How many years of revenue details available in data?",
    )

    assert result["answer"] == "There are 3 distinct Year values (2024–2026) in Forecast."
    assert result["sourceFields"] == ["Forecast.Year"]
    assert result["findings"] == [{"distinctCount": 3, "values": "2024, 2025, 2026"}]


def test_investigate_answers_unsegmented_measure_questions_using_model_aggregation() -> None:
    metadata = {
        "tables": {
            "Sales": {
                "columns": ["Revenue"],
                "columnTypes": {"Revenue": "decimal"},
                "measures": ["Total Revenue"],
                "measureDefinitions": {
                    "Total Revenue": {"column": "Revenue", "aggregation": "SUM"},
                },
            }
        }
    }
    raw_tables = {
        "Sales": {"rows": [{"Revenue": 100}, {"Revenue": 250}, {"Revenue": None}]}
    }

    result = investigate(metadata, raw_tables, "What is the total revenue?")

    assert result["answer"] == "Total Revenue: 350.00"
    assert result["sourceFields"] == ["Sales.Total Revenue"]
    assert result["findings"] == [{"measure": "Total Revenue", "value": 350.0}]


def test_investigate_filters_aggregate_by_explicit_dimension_value() -> None:
    metadata = {
        "tables": {
            "Sales": {
                "columns": ["Region", "Revenue"],
                "columnTypes": {"Region": "string", "Revenue": "decimal"},
                "measures": ["Total Revenue"],
                "measureDefinitions": {"Total Revenue": {"column": "Revenue", "aggregation": "SUM"}},
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

    result = investigate(metadata, raw_tables, "What is total sales revenue in North region?")

    assert result["findings"][0]["value"] == 125
    assert result["filters"] == [{"field": "Region", "value": "North"}]
    assert result["answer"] == "Total Revenue: 125.00"
    assert result["sourceFields"] == ["Sales.Total Revenue", "Sales.Region"]


def test_investigate_filters_aggregate_by_calendar_quarter() -> None:
    metadata = {
        "tables": {
            "Sales": {
                "columns": ["Order Date", "Revenue"],
                "columnTypes": {"Order Date": "dateTime", "Revenue": "decimal"},
                "measures": ["Total Revenue"],
                "measureDefinitions": {"Total Revenue": {"column": "Revenue", "aggregation": "SUM"}},
            }
        }
    }
    raw_tables = {
        "Sales": {"rows": [
            {"Order Date": datetime(2025, 1, 15), "Revenue": 100},
            {"Order Date": datetime(2025, 3, 31), "Revenue": 25},
            {"Order Date": datetime(2025, 4, 1), "Revenue": 300},
        ]}
    }

    result = investigate(metadata, raw_tables, "What is total revenue in the first quarter?")

    assert result["findings"][0]["value"] == 125
    assert result["filters"] == [{"field": "Order Date", "value": "Q1"}]


def test_investigate_does_not_ignore_unresolvable_quarter_filter() -> None:
    metadata = {
        "tables": {
            "Sales": {
                "columns": ["Revenue"],
                "columnTypes": {"Revenue": "decimal"},
                "measures": ["Total Revenue"],
                "measureDefinitions": {"Total Revenue": {"column": "Revenue", "aggregation": "SUM"}},
            }
        }
    }
    result = investigate(metadata, {"Sales": {"rows": [{"Revenue": 100}]}}, "What is total revenue in Q1?")

    assert result["findings"] == []
    assert "Which date/quarter field" in result["nextStep"]


def test_investigate_lists_distinct_dimension_values() -> None:
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
            }
        },
    }
    raw_tables = {
        "Sales": {
            "rows": [
                {"Region": "West", "Revenue": 100},
                {"Region": "East", "Revenue": 200},
                {"Region": "West", "Revenue": 300},
            ]
        }
    }

    result = investigate(metadata, raw_tables, "Which regions are available?")

    assert result["answer"] == "Region has 2 distinct values in Sales: East, West."
    assert result["sourceFields"] == ["Sales.Region"]
