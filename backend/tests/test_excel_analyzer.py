import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from openpyxl import Workbook
from openpyxl.worksheet.table import Table

from automation.excel import ExcelAnalyzer, WorkbookAnalysisCancelled
from automation.semantic.workbook_profile import profile_for_ai


def test_analyzer_reports_actual_processing_stages(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Region", "Revenue"])
    sheet.append(["North", 100])
    path = tmp_path / "stages.xlsx"
    workbook.save(path)
    workbook.close()
    stages = []

    profile = ExcelAnalyzer().analyze(path, stages.append)

    assert profile.sheets[0].tables
    assert stages == ["reading_workbook", "detecting_tables", "reading_formula_results", "profiling_columns"]


def test_analyzer_cancellation_is_not_logged_as_failure(tmp_path, caplog) -> None:
    workbook = Workbook()
    workbook.active.append(["Region", "Revenue"])
    workbook.active.append(["North", 100])
    path = tmp_path / "cancelled.xlsx"
    workbook.save(path)
    workbook.close()

    def cancel(stage: str) -> None:
        if stage == "detecting_tables":
            raise WorkbookAnalysisCancelled()

    with caplog.at_level(logging.ERROR), pytest.raises(WorkbookAnalysisCancelled):
        ExcelAnalyzer().analyze(path, cancel)
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_deep_grouped_headers_exclude_report_title(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.merge_cells("A1:D1")
    sheet["A1"] = "Performance report"
    sheet.merge_cells("A2:B2")
    sheet.merge_cells("C2:D2")
    sheet["A2"], sheet["C2"] = "Domestic", "Export"
    sheet.append(["Plan", "Actual", "Plan", "Actual"])
    sheet.append(["Revenue", "Profit", "Revenue", "Profit"])
    sheet.append([100, 20, 150, 30])
    path = tmp_path / "deep.xlsx"
    workbook.save(path)
    workbook.close()
    table = ExcelAnalyzer().analyze(path).sheets[0].tables[0]
    assert table.header_row == 4
    assert table.headers == ["Domestic Plan Revenue", "Domestic Actual Profit",
                             "Export Plan Revenue", "Export Actual Profit"]
    assert len(table.header_hierarchy) == 2
    assert table.row_count == 1


def test_vertical_merged_labels_fill_without_duplicating_numeric_values(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Region", "Product", "Revenue"])
    sheet.append(["North", "A", 100])
    sheet.append([None, "B", 200])
    sheet.merge_cells("A2:A3")
    path = tmp_path / "labels.xlsx"
    workbook.save(path)
    workbook.close()
    table = ExcelAnalyzer().analyze(path).sheets[0].tables[0]
    assert [row["Region"] for row in table.rows] == ["North", "North"]
    assert [row["Revenue"] for row in table.rows] == [100, 200]
    assert table.filled_merged_cell_count == 1
    assert table.review_reasons == []


def test_merged_numeric_data_requires_review_and_is_not_repeated(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Region", "Revenue"])
    sheet.append(["North", 100])
    sheet.append(["South", None])
    sheet.merge_cells("B2:B3")
    path = tmp_path / "numeric-merge.xlsx"
    workbook.save(path)
    workbook.close()
    profile = ExcelAnalyzer().analyze(path)
    table = profile.sheets[0].tables[0]
    assert [row["Revenue"] for row in table.rows] == [100, None]
    assert table.review_reasons
    assert profile.warnings


def test_touching_tables_with_repeated_headers_require_boundary_review(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Region", "Revenue"])
    sheet.append(["North", 100])
    sheet.append(["Region", "Revenue"])
    sheet.append(["South", 200])
    path = tmp_path / "touching.xlsx"
    workbook.save(path)
    workbook.close()
    table = ExcelAnalyzer().analyze(path).sheets[0].tables[0]
    assert table.row_count == 2
    assert any("boundaries" in reason for reason in table.review_reasons)


def test_horizontal_merged_data_is_preserved_and_flagged(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Region", "Product", "Revenue"])
    sheet.append(["North", None, 100])
    sheet.append(["South", "B", 200])
    sheet.merge_cells("A2:B2")
    path = tmp_path / "horizontal.xlsx"
    workbook.save(path)
    workbook.close()
    table = ExcelAnalyzer().analyze(path).sheets[0].tables[0]
    assert table.rows[0] == {"Region": "North", "Product": None, "Revenue": 100}
    assert table.filled_merged_cell_count == 0
    assert table.review_reasons


def test_analyzer_discovers_offset_headers_regions_and_sheet_visibility() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "offset.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Quarterly Summary"
        sheet.merge_cells("A1:C1")
        sheet["A1"] = "Quarterly performance"
        sheet["A3"] = "Region"
        sheet["B3"] = "Revenue"
        sheet["C3"] = "Quarter"
        sheet.append([])
        sheet["A4"] = "North"
        sheet["B4"] = 250
        sheet["C4"] = "Q1"
        sheet["A6"] = "Department"
        sheet["B6"] = "Cost"
        sheet["A7"] = "Retail"
        sheet["B7"] = 50
        sheet.row_dimensions[4].hidden = True
        sheet.column_dimensions["C"].hidden = True
        sheet.freeze_panes = "A4"
        hidden = workbook.create_sheet("Lookup")
        hidden.sheet_state = "veryHidden"
        hidden.append(["Code", "Description"])
        hidden.append(["N", "North"])
        workbook.save(workbook_path)
        workbook.close()

        profile = ExcelAnalyzer().analyze(workbook_path)

    summary = profile.sheets[0]
    assert [table.header_row for table in summary.tables] == [3, 6]
    assert [table.name for table in summary.tables] == [
        "Quarterly Summary_A3_C4",
        "Quarterly Summary_A6_B7",
    ]
    assert summary.merged_regions == ["A1:C1"]
    assert summary.hidden_rows == [4]
    assert summary.hidden_columns == ["C"]
    assert summary.freeze_panes == "A4"
    assert profile.workbook["very_hidden_sheets"] == ["Lookup"]
    assert profile.to_dict()["sheets"][0]["tables"][0].get("rows") is None
    json.loads(profile.to_json())


def test_analyzer_profiles_native_tables_formulas_and_relationship_candidates() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "relationships.xlsx"
        workbook = Workbook()
        sales = workbook.active
        sales.title = "Sales"
        sales.append(["CustomerID", "Amount", "Margin"])
        sales.append(["C1", 100, "=B2/10"])
        sales.append(["C1", 200, "=B3/10"])
        sales.add_table(Table(displayName="SalesTable", ref="A1:C3"))
        customers = workbook.create_sheet("Customers")
        customers.append(["CustomerID", "Name"])
        customers.append(["C1", "North"])
        customers.append(["C2", "South"])
        customers.add_table(Table(displayName="CustomerTable", ref="A1:B3"))
        workbook.save(workbook_path)
        workbook.close()

        profile = ExcelAnalyzer().analyze(workbook_path)

    assert profile.workbook["formula_count"] == 2
    margin = profile.sheets[0].tables[0].columns[2]
    assert margin.data_type == "decimal"
    assert margin.role_candidate == "calculated_measure"
    assert margin.formula_pattern == "=[ROW]/[NUMBER]"
    assert "arithmetic" in margin.formula_kinds
    assert any(
        relation["source_table"] == "SalesTable"
        and relation["source_column"] == "CustomerID"
        and relation["target_table"] == "CustomerTable"
        for relation in profile.relationships
    )
    assert profile.sheets[0].tables[0].rows[0]["Margin"] == "=B2/10"


def test_ai_payload_excludes_samples_but_keeps_profile_and_formula_metadata() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "sensitive.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Data"
        sheet.append(["Email", "Amount"])
        sheet.append(["person@example.com", 100])
        workbook.save(workbook_path)
        workbook.close()

        profile = ExcelAnalyzer().analyze(workbook_path)

    payload = profile_for_ai(profile.to_dict())
    serialized = json.dumps(payload)
    assert "person@example.com" not in serialized
    assert payload["workbook"]["sheet_count"] == 1
    assert payload["sheets"][0]["tables"][0]["columns"][0]["profile"]["non_null_count"] == 1


def test_analyzer_preserves_blank_rows_inside_tables_and_normalizes_multi_level_headers() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "multi-level.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Forecast"
        sheet.merge_cells("A1:B1")
        sheet.merge_cells("C1:D1")
        sheet["A1"] = 2025
        sheet["C1"] = 2026
        sheet.append(["Revenue", "Profit", "Revenue", "Profit"])
        sheet.append([10, 2, 12, 3])
        sheet.append([None, None, None, None])
        sheet.append([11, 2.5, 13, 3.5])
        workbook.save(workbook_path)
        workbook.close()

        profile = ExcelAnalyzer().analyze(workbook_path)

    table = profile.sheets[0].tables[0]
    assert table.headers == ["2025 Revenue", "2025 Profit", "2026 Revenue", "2026 Profit"]
    assert table.row_count == 2
    assert profile.sheets[0].logical_regions[0]["data_start_row"] == 3


def test_grouped_header_drops_a_child_label_that_is_only_parent_initials() -> None:
    """Header paths should retain semantic labels without duplicate abbreviations."""
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "grouped-abbreviation.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Summary"
        sheet.merge_cells("B1:C1")
        sheet["A1"] = "Operating Group"
        sheet["B1"] = "Current"
        sheet["A2"] = "OG"
        sheet["B2"] = "Amount"
        sheet["C2"] = "Rate"
        sheet.append(["North", 100, 0.2])
        sheet.append(["South", 120, 0.25])
        workbook.save(workbook_path)
        workbook.close()

        profile = ExcelAnalyzer().analyze(workbook_path)

    assert profile.sheets[0].tables[0].headers == [
        "Operating Group", "Current Amount", "Current Rate"
    ]


def test_numeric_year_column_is_a_time_dimension_not_a_measure() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "time-dimension.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Fiscal Year", "Scenario", "Rate"])
        sheet.append([2024, "A", 0.1])
        sheet.append([2025, "B", 0.2])
        workbook.save(workbook_path)
        workbook.close()

        profile = ExcelAnalyzer().analyze(workbook_path)

    columns = {column.name: column for column in profile.sheets[0].tables[0].columns}
    assert columns["Fiscal Year"].role_candidate == "time"
    metadata = profile.to_metadata()["tables"]
    table = next(iter(metadata.values()))
    assert "Total Fiscal Year" not in table["measures"]


def test_analyzer_adds_generic_year_scenario_view_without_replacing_source() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "forecast.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Forecast"
        sheet["A1"] = "Regional outlook"
        sheet.merge_cells("A2:A3")
        sheet.merge_cells("B2:C2")
        sheet.merge_cells("D2:E2")
        sheet["A2"] = "Metric"
        sheet["B2"] = "Plan"
        sheet["D2"] = "Stretch"
        for column, value in enumerate([2024, 2025, 2024, 2025], start=2):
            sheet.cell(3, column).value = value
        for column, value in enumerate(["Real output", 100, 110, 105, 120], start=1):
            sheet.cell(4, column).value = value
        workbook.save(workbook_path)
        workbook.close()

        profile = ExcelAnalyzer().analyze(workbook_path)

    tables = profile.sheets[0].tables
    source = next(table for table in tables if table.derived_from is None)
    view = next(table for table in tables if table.derived_from == source.name)
    assert source.row_count == 1
    assert source.headers[1:] == ["Plan 2024", "Plan 2025", "Stretch 2024", "Stretch 2025"]
    assert view.section_title == "Regional outlook"
    assert view.rows == [
        {"Segment": "Regional outlook", "Scenario": "Plan", "ScenarioSegment": "Plan | Regional outlook", "Year": 2024, "Metric": "Real output", "Value": 100},
        {"Segment": "Regional outlook", "Scenario": "Plan", "ScenarioSegment": "Plan | Regional outlook", "Year": 2025, "Metric": "Real output", "Value": 110},
        {"Segment": "Regional outlook", "Scenario": "Stretch", "ScenarioSegment": "Stretch | Regional outlook", "Year": 2024, "Metric": "Real output", "Value": 105},
        {"Segment": "Regional outlook", "Scenario": "Stretch", "ScenarioSegment": "Stretch | Regional outlook", "Year": 2025, "Metric": "Real output", "Value": 120},
    ]


def test_analyzer_records_formula_dependencies_and_external_features() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "formula-links.xlsx"
        workbook = Workbook()
        rates = workbook.active
        rates.title = "Rates"
        rates.append(["Currency", "Rate"])
        rates.append(["USD", 1.0])
        rates.add_table(Table(displayName="RatesTable", ref="A1:B2"))
        sales = workbook.create_sheet("Sales")
        sales.append(["Currency", "Amount", "Converted"])
        sales.append(["USD", 10, "='Rates'!B2*B2"])
        sales.add_table(Table(displayName="SalesTable", ref="A1:C2"))
        external = workbook.create_sheet("External")
        external.append(["Value", "FromExternal"])
        external.append([1, "='[other.xlsx]Sheet1'!A1"])
        workbook.save(workbook_path)
        workbook.close()

        profile = ExcelAnalyzer().analyze(workbook_path)

    assert any(
        dependency["table"] == "SalesTable"
        and dependency["column"] == "Converted"
        and dependency["depends_on_table"] == "RatesTable"
        and dependency["depends_on_column"] == "Rate"
        and dependency["cross_sheet"]
        for dependency in profile.formula_dependencies
    )
    assert any(feature["feature"] == "external_formula_reference" for feature in profile.unsupported_features)


def test_analyzer_infers_geography_identifiers_and_categories() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "roles.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Customer ID", "Country", "Segment"])
        sheet.append(["C-1", "US", "Retail"])
        sheet.append(["C-2", "CA", "Retail"])
        sheet.append(["C-3", "US", "Online"])
        workbook.save(workbook_path)
        workbook.close()

        profile = ExcelAnalyzer().analyze(workbook_path)

    columns = {column.name: column for column in profile.sheets[0].tables[0].columns}
    assert columns["Customer ID"].role_candidate == "identifier"
    assert columns["Country"].role_candidate == "geographic"
    assert columns["Segment"].role_candidate == "category"


def test_analyzer_summarizes_multiple_formula_patterns_and_coverage() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "formula-patterns.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Amount", "Calculated"])
        sheet.append([10, "=A2*2"])
        sheet.append([20, "=A3+1"])
        sheet.add_table(Table(displayName="Metrics", ref="A1:B3"))
        workbook.save(workbook_path)
        workbook.close()

        profile = ExcelAnalyzer().analyze(workbook_path)

    calculated = profile.sheets[0].tables[0].columns[1]
    assert calculated.formula_count == 2
    assert len(calculated.formula_patterns) == 2
    assert all(pattern["coverage"] == 0.5 for pattern in calculated.formula_patterns)
