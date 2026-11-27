from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from automation.policy import CROSS_TAB_METRIC_FIELD, CROSS_TAB_VALUE_FIELD


class ColumnProfile(BaseModel):
    name: str
    original_headers: list[str] = Field(default_factory=list)
    data_type: str
    role_candidate: str
    confidence: float
    formula_count: int = 0
    formula_pattern: str | None = None
    formula_patterns: list[dict[str, Any]] = Field(default_factory=list)
    formula_kinds: list[str] = Field(default_factory=list)
    number_format: str | None = None
    profile: dict[str, Any] = Field(default_factory=dict)
    sample_values: list[str] = Field(default_factory=list)


class TableProfile(BaseModel):
    name: str
    sheet: str
    section_title: str | None = None
    derived_from: str | None = None
    semantic_fields: dict[str, Any] = Field(default_factory=dict)
    range: str
    start_row: int
    end_row: int
    start_column: int
    end_column: int
    header_row: int
    data_start_row: int
    data_end_row: int
    headers: list[str]
    header_hierarchy: list[list[str]] = Field(default_factory=list)
    row_count: int
    column_count: int
    confidence: float
    detection_method: str
    excel_table: bool = False
    totals_row: bool = False
    calculated_columns: list[str] = Field(default_factory=list)
    totals_columns: list[str] = Field(default_factory=list)
    table_style: str | None = None
    merged_data_ranges: list[str] = Field(default_factory=list)
    filled_merged_cell_count: int = 0
    review_reasons: list[str] = Field(default_factory=list)
    formula_cells: list[dict[str, Any]] = Field(default_factory=list, exclude=True)
    columns: list[ColumnProfile] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list, exclude=True)


class SheetProfile(BaseModel):
    name: str
    order: int
    visibility: str
    classification: str
    classification_confidence: float
    used_range: str
    min_row: int
    max_row: int
    min_column: int
    max_column: int
    row_count: int
    column_count: int
    merged_regions: list[str] = Field(default_factory=list)
    hidden_rows: list[int] = Field(default_factory=list)
    hidden_columns: list[str] = Field(default_factory=list)
    freeze_panes: str | None = None
    tables: list[TableProfile] = Field(default_factory=list)
    logical_regions: list[dict[str, Any]] = Field(default_factory=list)
    formula_count: int = 0
    formula_columns: int = 0
    formulas: list[dict[str, Any]] = Field(default_factory=list)
    formatting_clues: list[dict[str, Any]] = Field(default_factory=list)
    likely_title_rows: list[int] = Field(default_factory=list)
    likely_header_rows: list[int] = Field(default_factory=list)


class WorkbookProfile(BaseModel):
    workbook: dict[str, Any]
    sheets: list[SheetProfile]
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    formula_dependencies: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_features: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: str = "complete"

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = super().model_dump(*args, **kwargs)
        for sheet in result.get("sheets", []):
            for table in sheet.get("tables", []):
                table.pop("rows", None)
        return result

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=True, indent=2)

    def to_metadata(self) -> dict[str, Any]:
        tables: dict[str, dict[str, Any]] = {}
        for sheet in self.sheets:
            for table in sheet.tables:
                rows = table.rows
                if not rows:
                    continue
                import_rows = [dict(row) for row in rows]
                formulas_by_column: dict[str, list[str]] = {}
                formula_result_counts: dict[str, int] = {}
                missing_formula_results = 0
                for formula_cell in table.formula_cells:
                    column_name = formula_cell["column"]
                    formulas_by_column.setdefault(column_name, []).append(formula_cell["formula"])
                    cached_value = formula_cell.get("cached_value")
                    if cached_value is not None:
                        formula_result_counts[column_name] = formula_result_counts.get(column_name, 0) + 1
                    else:
                        missing_formula_results += 1
                    row_index = formula_cell["row_index"]
                    if row_index < len(import_rows):
                        import_rows[row_index][column_name] = cached_value
                measures: list[str] = []
                measure_definitions: dict[str, dict[str, str]] = {}
                for column in table.columns:
                    if column.data_type not in {"integer", "decimal", "currency", "percentage"}:
                        continue
                    if column.role_candidate not in {"measure", "calculated_measure"}:
                        continue
                    if column.formula_count and not formula_result_counts.get(column.name, 0):
                        continue
                    non_additive = (
                        column.data_type == "percentage" or "%" in (column.number_format or "")
                        or bool(re.search(r"\b(rate|ratio|margin|average|avg|percent|percentage)\b"
                                          r"|\b(?:unit|selling)\s+price\b", column.name, re.I))
                    )
                    aggregation = "AVERAGE" if non_additive else "SUM"
                    prefix = "Average" if non_additive else "Total"
                    measure_name = f"{prefix} {column.name}"
                    measures.append(measure_name)
                    measure_definitions[measure_name] = {"column": column.name, "aggregation": aggregation}
                metric_field = table.semantic_fields.get("metric", CROSS_TAB_METRIC_FIELD)
                value_field = table.semantic_fields.get("value", CROSS_TAB_VALUE_FIELD)
                if {metric_field, value_field}.issubset(table.headers):
                    for metric in dict.fromkeys(
                        str(row[metric_field]).strip()
                        for row in import_rows
                        if row.get(metric_field) is not None
                    ):
                        if metric and metric not in measures:
                            measures.append(metric)
                            measure_definitions[metric] = {
                                "column": value_field,
                                "filterColumn": metric_field,
                                "filterValue": metric,
                                "aggregation": "SUM",
                            }
                tables[table.name] = {
                    "name": table.name,
                    "sourceSheet": table.sheet,
                    "sourceRange": table.range,
                    "sourceTable": table.name if table.excel_table else None,
                    "derivedFrom": table.derived_from,
                    "semanticFields": table.semantic_fields,
                    "columns": [
                        {
                            "name": column.name,
                            "dataType": _autobi_data_type(column.data_type),
                            "formatString": column.number_format,
                            "role": column.role_candidate,
                            "formulaCount": column.formula_count,
                            "formulaResultCount": formula_result_counts.get(column.name, 0),
                            "formulaExpressions": formulas_by_column.get(column.name, []),
                            "profile": column.profile,
                        }
                        for column in table.columns
                    ],
                    "measures": measures,
                    "measureDefinitions": measure_definitions,
                    "metricValues": list(measure_definitions),
                    "sampleRows": import_rows[:5],
                    "rows": import_rows,
                    "formulaCount": sum(column.formula_count for column in table.columns),
                    "missingFormulaResultCount": missing_formula_results,
                }
        used_measure_names: set[str] = set()
        for table_name, table in tables.items():
            renamed_measures = []
            renamed_definitions = {}
            renamed_metric_values = []
            for measure_name in table["measures"]:
                unique_name = measure_name
                if unique_name.casefold() in used_measure_names:
                    unique_name = f"{measure_name} ({table_name})"
                    suffix = 2
                    while unique_name.casefold() in used_measure_names:
                        unique_name = f"{measure_name} ({table_name} {suffix})"
                        suffix += 1
                used_measure_names.add(unique_name.casefold())
                renamed_measures.append(unique_name)
                definition = table["measureDefinitions"].get(measure_name)
                if definition is not None:
                    renamed_definitions[unique_name] = definition
                    if measure_name in table["metricValues"]:
                        renamed_metric_values.append(unique_name)
                elif unique_name != measure_name and measure_name.startswith("Total "):
                    renamed_definitions[unique_name] = {
                        "column": measure_name.removeprefix("Total "),
                        "aggregation": "SUM",
                    }
            table["measures"] = renamed_measures
            table["measureDefinitions"] = renamed_definitions
            table["metricValues"] = renamed_metric_values
        return {"tables": tables, "relationships": self.relationships}


def _autobi_data_type(data_type: str) -> str:
    return {
        "text": "string",
        "currency": "decimal",
        "percentage": "decimal",
        "date": "dateTime",
        "datetime": "dateTime",
        "mixed": "string",
        "blank": "string",
        "formula": "string",
        "boolean": "boolean",
    }.get(data_type, data_type)
