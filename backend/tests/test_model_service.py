from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile
from xml.etree import ElementTree

from openpyxl import Workbook
from openpyxl.worksheet.table import Table, TableStyleInfo
import pytest

from backend.services import model_service
from backend.services.model_service import _active_relationships, excel_to_metadata, excel_to_semantic_model


def test_datatable_datetime_literals_preserve_excel_milliseconds() -> None:
    literal = model_service._dax_table_literal
    assert literal(date(1899, 12, 29)) == 'dt"1899-12-29"'
    assert literal(datetime(2026, 1, 8, 12, 34, 56, 123000)) == 'dt"2026-01-08T12:34:56.123"'
    assert literal(datetime(2026, 1, 8)) == 'dt"2026-01-08T00:00:00.000"'
    with pytest.raises(ValueError, match="Timezone-aware"):
        literal(datetime(2026, 1, 8, tzinfo=timezone.utc))


def test_fixture_sales_transactions_uses_valid_datetime_literals(tmp_path) -> None:
    workbook_path = Path(__file__).parent / "fixtures" / "Test_data.xlsx"
    metadata = excel_to_metadata(workbook_path)
    model_path = tmp_path / "Sales.SemanticModel"
    excel_to_semantic_model(workbook_path, metadata, model_path)

    table = metadata["tables"]["SalesTransactions"]
    definition = (model_path / "definition" / "tables" / "SalesTransactions.tmdl").read_text(encoding="utf-8")
    dates = [row["Order Date"] for row in table["rows"] if row["Order Date"] is not None]
    assert len(dates) == 120
    assert all(model_service._dax_table_literal(value) in definition for value in dates)
    assert '"Order Date", DATETIME' in definition
    assert "DATE(" not in definition
    assert "TIME(" not in definition


def test_active_relationships_remove_ambiguous_cycle() -> None:
    metadata = {
        "tables": {"A": {}, "B": {}, "C": {}},
        "relationships": [
            {"source_table": "A", "source_column": "Key", "target_table": "B", "target_column": "Key", "confidence": 0.95},
            {"source_table": "B", "source_column": "Key", "target_table": "C", "target_column": "Key", "confidence": 0.90},
            {"source_table": "A", "source_column": "Key", "target_table": "C", "target_column": "Key", "confidence": 0.85},
        ],
    }

    active = _active_relationships(metadata)

    assert len(active) == 2
    assert active == metadata["relationships"][:2]
from automation.semantic_model_inspector import inspect_semantic_model


def test_excel_metadata_infers_column_types_across_populated_rows() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "typed.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Facts"
        sheet.append(["Amount", "Enabled", "Created", "Mixed"])
        sheet.append([10, True, datetime(2025, 1, 2), 4])
        sheet.append([10.5, False, datetime(2025, 1, 3), "unknown"])
        workbook.save(workbook_path)
        workbook.close()

        metadata = excel_to_metadata(workbook_path)

    columns = {column["name"]: column["dataType"] for column in metadata["tables"]["Facts"]["columns"]}
    assert columns == {
        "Amount": "decimal",
        "Enabled": "boolean",
        "Created": "dateTime",
        "Mixed": "string",
    }
    assert metadata["tables"]["Facts"]["sampleRows"][0]["Amount"] == 10


def test_percentage_measure_uses_average_aggregation() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "margins.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Region", "Margin"])
        sheet.append(["North", 0.2])
        sheet.append(["South", 0.3])
        sheet["B2"].number_format = "0%"
        sheet["B3"].number_format = "0%"
        workbook.save(workbook_path)
        workbook.close()

        metadata = excel_to_metadata(workbook_path)
        model_path = Path(temporary_directory) / "Margins.SemanticModel"
        excel_to_semantic_model(workbook_path, metadata, model_path)
        table = metadata["tables"]["Sheet"]
        definition = (model_path / "definition" / "tables" / "Sheet.tmdl").read_text(encoding="utf-8")

    assert "Average Margin" in table["measures"]
    assert table["measureDefinitions"]["Average Margin"] == {
        "column": "Margin",
        "aggregation": "AVERAGE",
    }
    assert "measure 'Average Margin' = AVERAGE('Sheet'[Margin])" in definition


def test_excel_tables_are_imported_as_individual_tables() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "tables.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Data"
        sheet.append(["Region", "Revenue"])
        sheet.append(["North", 100])
        sheet.append(["South", 200])
        sheet["D1"] = "Department"
        sheet["E1"] = "Cost"
        sheet["D2"] = "Retail"
        sheet["E2"] = 20
        sheet.add_table(Table(displayName="RevenueTable", ref="A1:B3"))
        sheet.add_table(Table(displayName="CostTable", ref="D1:E2"))
        workbook.save(workbook_path)
        workbook.close()

        metadata = excel_to_metadata(workbook_path)
        model_path = Path(temporary_directory) / "TableModel.SemanticModel"
        excel_to_semantic_model(workbook_path, metadata, model_path)
        table_files = sorted((model_path / "definition" / "tables").glob("*.tmdl"))

    assert set(metadata["tables"]) == {"RevenueTable", "CostTable"}
    assert metadata["tables"]["RevenueTable"]["sourceSheet"] == "Data"
    assert metadata["tables"]["CostTable"]["sampleRows"][0] == {"Department": "Retail", "Cost": 20}
    assert [path.stem for path in table_files] == ["CostTable", "RevenueTable"]


def test_duplicate_measure_names_are_qualified_across_tables() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "duplicate-measures.xlsx"
        workbook = Workbook()
        first = workbook.active
        first.title = "First"
        first.append(["Amount"])
        first.append([10])
        first.add_table(Table(displayName="FirstTable", ref="A1:A2"))
        second = workbook.create_sheet("Second")
        second.append(["Amount"])
        second.append([20])
        second.add_table(Table(displayName="SecondTable", ref="A1:A2"))
        workbook.save(workbook_path)
        workbook.close()

        metadata = excel_to_metadata(workbook_path)
        model_path = Path(temporary_directory) / "DuplicateMeasures.SemanticModel"
        excel_to_semantic_model(workbook_path, metadata, model_path)
        definitions = [
            path.read_text(encoding="utf-8")
            for path in (model_path / "definition" / "tables").glob("*.tmdl")
        ]

    assert metadata["tables"]["FirstTable"]["measures"] == ["Total Amount"]
    assert metadata["tables"]["SecondTable"]["measures"] == ["Total Amount (SecondTable)"]
    assert len({
        measure.casefold()
        for table in metadata["tables"].values()
        for measure in table["measures"]
    }) == 2
    assert any("measure 'Total Amount' = SUM('FirstTable'[Amount])" in text for text in definitions)
    assert any(
        "measure 'Total Amount (SecondTable)' = SUM('SecondTable'[Amount])" in text
        for text in definitions
    )


def test_inferred_relationship_is_serialized_into_model_tmdl() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "relationships.xlsx"
        workbook = Workbook()
        orders = workbook.active
        orders.title = "Orders"
        orders.append(["CustomerID", "Amount"])
        orders.append(["C1", 100])
        orders.append(["C1", 200])
        orders.append(["C2", 300])
        orders.add_table(Table(displayName="OrdersTable", ref="A1:B4"))
        customers = workbook.create_sheet("Customers")
        customers.append(["CustomerID", "Name"])
        customers.append(["C1", "North"])
        customers.append(["C2", "South"])
        customers.add_table(Table(displayName="CustomersTable", ref="A1:B3"))
        workbook.save(workbook_path)
        workbook.close()

        metadata = excel_to_metadata(workbook_path)
        model_path = Path(temporary_directory) / "Relationships.SemanticModel"
        excel_to_semantic_model(workbook_path, metadata, model_path)
        inspected = inspect_semantic_model(model_path)
        model_definition = (model_path / "definition" / "model.tmdl").read_text(encoding="utf-8")

    assert metadata["relationships"]
    assert any(
        item["fromColumn"] == "OrdersTable.CustomerID"
        and item["toColumn"] == "CustomersTable.CustomerID"
        for item in inspected["relationships"]
    )
    assert "cardinality:" not in model_definition
    assert "fromColumn: 'OrdersTable'.'CustomerID'" in model_definition
    assert "toColumn: 'CustomersTable'.'CustomerID'" in model_definition
    assert "crossFilteringBehavior: oneDirection" in model_definition


def test_excel_import_discovers_multiple_ranges_and_keeps_data_beside_named_tables() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "multi-range.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Data"
        sheet.append(["Region", "Revenue", "Double Revenue"])
        sheet.append(["North", 100, "=B2*2"])
        sheet.append(["South", 200, "=B3*2"])
        sheet.add_table(Table(displayName="SalesTable", ref="A1:C3"))
        sheet["E1"] = "Department"
        sheet["F1"] = "Cost"
        sheet["E2"] = "Retail"
        sheet["F2"] = 20
        sheet["E4"] = "Channel"
        sheet["F4"] = "Orders"
        sheet["E5"] = "Online"
        sheet["F5"] = 12
        second_sheet = workbook.create_sheet("Targets")
        second_sheet["B2"] = "Region"
        second_sheet["C2"] = "Target"
        second_sheet["B3"] = "North"
        second_sheet["C3"] = 120
        workbook.save(workbook_path)
        workbook.close()

        metadata = excel_to_metadata(workbook_path)

    assert set(metadata["tables"]) == {
        "SalesTable",
        "Data_E1_F2",
        "Data_E4_F5",
        "Targets",
    }
    sales = metadata["tables"]["SalesTable"]
    assert sales["rows"][0] == {"Region": "North", "Revenue": 100, "Double Revenue": None}
    assert sales["formulaCount"] == 2
    assert metadata["tables"]["SalesTable"]["columns"][2]["dataType"] == "decimal"
    assert metadata["tables"]["SalesTable"]["columns"][2]["formulaExpressions"] == ["=B2*2", "=B3*2"]
    assert metadata["tables"]["SalesTable"]["missingFormulaResultCount"] == 2
    assert metadata["tables"]["Data_E1_F2"]["rows"] == [{"Department": "Retail", "Cost": 20}]
    assert metadata["tables"]["Targets"]["sourceRange"] == "B2:C3"


def test_excel_import_uses_saved_formula_results_and_retains_expressions() -> None:
    with TemporaryDirectory() as temporary_directory:
        workbook_path = Path(temporary_directory) / "formula-results.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Amount", "Doubled"])
        sheet.append([10, "=A2*2"])
        workbook.save(workbook_path)
        workbook.close()

        temporary_path = workbook_path.with_name("formula-results-updated.xlsx")
        with ZipFile(workbook_path) as source, ZipFile(temporary_path, "w", ZIP_DEFLATED) as target:
            for item in source.infolist():
                content = source.read(item.filename)
                if item.filename == "xl/worksheets/sheet1.xml":
                    root = ElementTree.fromstring(content)
                    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
                    formula_cell = root.find(f".//{namespace}c[@r='B2']")
                    assert formula_cell is not None
                    cached_value = formula_cell.find(f"{namespace}v")
                    assert cached_value is not None
                    cached_value.text = "20"
                    content = ElementTree.tostring(root, encoding="utf-8")
                target.writestr(item, content)
        temporary_path.replace(workbook_path)

        metadata = excel_to_metadata(workbook_path)
        model_path = Path(temporary_directory) / "FormulaModel.SemanticModel"
        excel_to_semantic_model(workbook_path, metadata, model_path)
        definition = (model_path / "definition" / "tables" / "Sheet.tmdl").read_text(encoding="utf-8")

    table = metadata["tables"]["Sheet"]
    assert table["rows"] == [{"Amount": 10, "Doubled": 20}]
    assert table["formulaCount"] == 1
    formula_column = next(column for column in table["columns"] if column["name"] == "Doubled")
    assert formula_column["dataType"] == "integer"
    assert formula_column["formulaExpressions"] == ["=A2*2"]
    assert formula_column["formulaResultCount"] == 1
    assert "annotation AURA_ExcelFormulaCount = 1" in definition
    assert "measure 'Total Doubled' = SUM('Sheet'[Doubled])" in definition


def test_source_metadata_round_trips_excel_dates(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(model_service, "APP_DATA_DIR", tmp_path)
    created = datetime(2025, 4, 5, 13, 30)

    model_service.save_source_metadata(
        "src-1234abcd",
        {"metadata": {"tables": {"Dates": {"rows": [{"Created": created}]}}}},
    )

    restored = model_service.load_source_payload("src-1234abcd")
    assert restored["metadata"]["tables"]["Dates"]["rows"][0]["Created"] == created


def test_source_metadata_restores_derived_table_links_from_saved_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(model_service, "APP_DATA_DIR", tmp_path)
    source_id = "src-1234abcd"
    model_service.save_source_metadata(source_id, {
        "metadata": {
            "tables": {
                "Wide": {"columns": ["Metric"], "measures": []},
                "Wide_ChartData": {"columns": ["Scenario"], "measures": []},
            },
        },
        "workbookProfile": {
            "sheets": [{
                "tables": [
                    {"name": "Wide", "derived_from": None},
                    {"name": "Wide_ChartData", "derived_from": "Wide"},
                ],
            }],
        },
    })

    metadata = model_service.load_source_metadata(source_id)

    assert metadata["tables"]["Wide_ChartData"]["derivedFrom"] == "Wide"
