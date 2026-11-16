import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from openpyxl import load_workbook

from automation.excel import ExcelAnalyzer
from automation.semantic_model_inspector import inspect_semantic_model
from backend.config import get_settings
from backend.policy import MIN_RELATIONSHIP_CONFIDENCE

# Kept as a module-level alias so tests and embedders can redirect storage.
APP_DATA_DIR = get_settings().app_data_dir


def ensure_source_structure() -> Path:
    path = APP_DATA_DIR / "sources"
    path.mkdir(parents=True, exist_ok=True)
    return path


def excel_to_metadata(file_path: str | Path) -> dict[str, Any]:
    profile = ExcelAnalyzer().analyze(file_path)
    tables = profile.to_metadata()["tables"]
    names: dict[str, int] = {}
    unique_tables = {}
    for table_name, table in tables.items():
        names[table_name] = names.get(table_name, 0) + 1
        unique_name = table_name if names[table_name] == 1 else f"{table_name}_{names[table_name]}"
        table["name"] = unique_name
        unique_tables[unique_name] = table
    tables = unique_tables
    if not tables:
        raise ValueError("No usable worksheets or report blocks were found in the Excel file")
    return {"tables": tables, "relationships": profile.relationships}


def _tmdl_identifier(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _m_identifier(value: str) -> str:
    return "#\"" + value.replace('"', '""') + "\""


def _dax_column_reference(table_name: str, column_name: str) -> str:
    escaped_table = table_name.replace("'", "''")
    escaped_column = column_name.replace("]", "]]" )
    return f"'{escaped_table}'[{escaped_column}]"


def _tmdl_column_reference(table_name: str, column_name: str) -> str:
    return f"{_tmdl_identifier(table_name)}.{_tmdl_identifier(column_name)}"


def _m_literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return f"#datetime({value.year}, {value.month}, {value.day}, {value.hour}, {value.minute}, {value.second})"
    if isinstance(value, date):
        return f"#date({value.year}, {value.month}, {value.day})"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return "null"
        return repr(value)
    return '"' + str(value).replace('"', '""') + '"'


def _dax_table_literal(value: Any) -> str:
    """Return a DAX constant suitable for a DATATABLE row."""
    if value is None:
        return "BLANK()"
    if isinstance(value, bool):
        return "TRUE()" if value else "FALSE()"
    if isinstance(value, datetime):
        if value.utcoffset() is not None:
            raise ValueError("Timezone-aware datetimes cannot be imported into a Power BI DATATABLE")
        # DATATABLE accepts DAX datetime literals, not DATE/TIME function calls.
        return f'dt"{value.isoformat(timespec="milliseconds")}"'
    if isinstance(value, date):
        return f'dt"{value.isoformat()}"'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else "BLANK()"
    return '"' + str(value).replace('"', '""') + '"'


def _power_bi_format_string(column: dict[str, Any]) -> str:
    """Provide a stable display format for generated numeric model fields."""
    configured = str(column.get("formatString") or "").strip()
    data_type = column.get("dataType")
    if configured and configured.casefold() != "general":
        return configured
    if data_type not in {"integer", "decimal"}:
        return configured or "General"

    name = str(column.get("name") or "").casefold()
    if "%" in name or "percent" in name or "margin" in name or "share" in name:
        maximum = (column.get("profile") or {}).get("max")
        # Excel percentage columns sometimes store 60.5 for 60.5%, and
        # sometimes 0.605. Preserve the intended display in both cases.
        if isinstance(maximum, (int, float)) and abs(maximum) > 1:
            return "0.0\\%;(0.0\\%);-"
        return "0.0%;(0.0%);-"
    if any(keyword in name for keyword in ("revenue", "cost", "profit", "price", "value", "impact", "target")):
        return "$#,0;($#,0);-"
    return "#,0.##"


def excel_to_semantic_model(file_path: str | Path, metadata: dict[str, Any], target_dir: Path) -> Path:
    needs_workbook_fallback = any(
        not isinstance(table.get("rows"), list)
        for table in metadata.get("tables", {}).values()
    )
    workbook = load_workbook(file_path, read_only=True, data_only=False) if needs_workbook_fallback else None
    definition_dir = target_dir / "definition"
    tables_dir = definition_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "definition.pbism").write_text(
        json.dumps({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
            "version": "4.2",
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    (target_dir / ".platform").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {
            "type": "SemanticModel",
            "displayName": target_dir.name.removesuffix(".SemanticModel"),
        },
        "config": {"version": "2.0", "logicalId": str(uuid4())},
    }, indent=2) + "\n", encoding="utf-8")
    (definition_dir / "database.tmdl").write_text(
        f"database {_tmdl_identifier(target_dir.name.removesuffix('.SemanticModel'))}\n"
        "\tcompatibilityLevel: 1606\n",
        encoding="utf-8",
    )
    table_references = "".join(
        f"\nref table {_tmdl_identifier(table_name)}\n"
        for table_name in metadata.get("tables", {})
    )
    relationship_definitions = []
    for relationship_index, relationship in enumerate(_active_relationships(metadata), start=1):
        source_table = relationship.get("source_table")
        source_column = relationship.get("source_column")
        target_table = relationship.get("target_table")
        target_column = relationship.get("target_column")
        if (
            source_table not in metadata.get("tables", {})
            or target_table not in metadata.get("tables", {})
            or source_column not in {
                column.get("name") for column in metadata["tables"][source_table].get("columns", [])
            }
            or target_column not in {
                column.get("name") for column in metadata["tables"][target_table].get("columns", [])
            }
            or relationship.get("confidence", 0) < MIN_RELATIONSHIP_CONFIDENCE
        ):
            continue
        relationship_definitions.extend([
            f"relationship {_tmdl_identifier(f'AURA_Relationship_{relationship_index}')}",
            f"\tfromColumn: {_tmdl_column_reference(source_table, source_column)}",
            f"\ttoColumn: {_tmdl_column_reference(target_table, target_column)}",
            "\tcrossFilteringBehavior: oneDirection",
            "\tisActive: true",
            "",
        ])
    (definition_dir / "model.tmdl").write_text(
        "model Model\n"
        "\tculture: en-US\n"
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
        "\tsourceQueryCulture: en-US\n"
        "\tvalueFilterBehavior: independent\n"
        "\tdataAccessOptions\n"
        "\t\tlegacyRedirects\n"
        "\t\treturnErrorValuesAsNull\n"
        + table_references
        + "\n".join(relationship_definitions),
        encoding="utf-8",
    )
    used_file_names: set[str] = set()
    try:
        for table_index, (table_name, table) in enumerate(metadata.get("tables", {}).items()):
            headers = [str(column.get("name", "")) for column in table.get("columns", [])]
            normalized_rows = table.get("rows")
            if isinstance(normalized_rows, list):
                data_rows = [[row.get(header) for header in headers] for row in normalized_rows if isinstance(row, dict)]
            else:
                if workbook is None:
                    raise ValueError(f"Source rows are missing for table '{table_name}'. Re-upload the workbook.")
                source_sheet = table.get("sourceSheet", table_name)
                if source_sheet not in workbook.sheetnames:
                    raise ValueError(f"Source rows are missing for table '{table_name}'. Re-upload the workbook.")
                sheet = workbook[source_sheet]
                data_rows = list(sheet.iter_rows(min_row=2, values_only=True))
            lines = [
                f"table {_tmdl_identifier(table_name)}",
                f"\tlineageTag: {uuid4()}",
            ]
            for column in table.get("columns", []):
                column_name = str(column["name"])
                data_type = {"integer": "int64", "decimal": "double", "boolean": "boolean", "dateTime": "dateTime"}.get(column.get("dataType"), "string")
                summarize_by = "sum" if data_type in {"int64", "double"} else "none"
                # Calculated-partition output columns are addressed as DAX
                # fields, unlike M partition columns which use bare names.
                calculated_source_column = "[" + column_name.replace("]", "]]" ) + "]"
                lines.extend([
                    f"\tcolumn {_tmdl_identifier(column_name)}",
                    f"\t\tdataType: {data_type}",
                    f"\t\tlineageTag: {uuid4()}",
                    f"\t\tsummarizeBy: {summarize_by}",
                    f"\t\tsourceColumn: {calculated_source_column}",
                ])
                column_format = _power_bi_format_string(column)
                if column_format:
                    lines.append(f"\t\tformatString: {column_format}")
                formula_expressions = column.get("formulaExpressions", [])
                if formula_expressions:
                    lines.extend([
                        f"\t\tannotation AURA_ExcelFormulaCount = {column.get('formulaCount', len(formula_expressions))}",
                        f"\t\tannotation AURA_ExcelFormulaResultCount = {column.get('formulaResultCount', 0)}",
                        f"\t\tannotation AURA_ExcelFormulaExpressions = {json.dumps(json.dumps(formula_expressions, ensure_ascii=True))}",
                    ])
            for measure in table.get("measures", []):
                measure_definition = table.get("measureDefinitions", {}).get(measure)
                if measure_definition:
                    column_name = measure_definition["column"]
                    aggregation = measure_definition.get("aggregation", "SUM")
                    if aggregation not in {"SUM", "AVERAGE", "MIN", "MAX", "COUNT"}:
                        raise ValueError(f"Unsupported aggregation '{aggregation}' for measure '{measure}'")
                    aggregate_expression = f"{aggregation}({_dax_column_reference(table_name, column_name)})"
                    if measure_definition.get("filterColumn"):
                        filter_column = measure_definition["filterColumn"]
                        filter_value = measure_definition["filterValue"].replace('"', '""')
                        expression = (
                            f"CALCULATE({aggregate_expression}, "
                            f"KEEPFILTERS({_dax_column_reference(table_name, filter_column)} = \"{filter_value}\"))"
                        )
                    else:
                        expression = aggregate_expression
                else:
                    column_name = measure.removeprefix("Total ")
                    expression = f"SUM({_dax_column_reference(table_name, column_name)})"
                column_metadata = next(
                    (
                        column
                        for column in table.get("columns", [])
                        if column.get("name") == column_name
                    ),
                    {},
                )
                measure_format = _power_bi_format_string(column_metadata)
                lines.extend([
                    f"\tmeasure {_tmdl_identifier(measure)} = {expression}",
                    f"\t\tformatString: {measure_format}",
                    f"\t\tlineageTag: {uuid4()}",
                ])

            dax_type_names = {"integer": "INTEGER", "decimal": "DOUBLE", "boolean": "BOOLEAN", "dateTime": "DATETIME", "string": "STRING"}
            dax_columns = ",\n".join(
                f'\t\t\t\t"{str(column["name"]).replace("\\\"", "\\\"\\\"")}", '
                f'{dax_type_names.get(column.get("dataType"), "STRING")}'
                for column in table.get("columns", [])
            )
            row_literals = []
            for row in data_rows:
                values = [row[index] if index < len(row) else None for index in range(len(headers))]
                if not any(value is not None for value in values):
                    continue
                row_literals.append("\t\t\t\t{" + ", ".join(_dax_table_literal(value) for value in values) + "}")
            rows_literal = ",\n".join(row_literals)
            lines.extend([
                f"\tpartition {_tmdl_identifier(table_name)} = calculated",
                "\t\tmode: import",
                "\t\tsource =",
                "\t\t\tDATATABLE(",
                dax_columns + ",",
                "\t\t\t\t{",
                rows_literal,
                "\t\t\t\t}",
                "\t\t\t)",
                "\tannotation PBI_ResultType = Table",
            ])
            safe_file_base = re.sub(r"[^A-Za-z0-9_-]+", "_", table_name).strip("_") or f"Table{table_index + 1}"
            safe_file_name = safe_file_base
            suffix = 2
            while safe_file_name.casefold() in used_file_names:
                safe_file_name = f"{safe_file_base}_{suffix}"
                suffix += 1
            used_file_names.add(safe_file_name.casefold())
            (tables_dir / f"{safe_file_name}.tmdl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    finally:
        if workbook is not None:
            workbook.close()
    return target_dir


def _active_relationships(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Choose a high-confidence relationship forest for Power BI.

    Power BI permits only one active filter path between two tables.  Workbook
    inference can find several plausible shared columns, so retaining every
    candidate can create cycles and prevent a PBIP project from opening.
    Kruskal-style selection preserves the strongest links while eliminating
    cycles and duplicate paths.
    """
    tables = set(metadata.get("tables", {}))
    parent = {table: table for table in tables}

    def find(table: str) -> str:
        while parent[table] != table:
            parent[table] = parent[parent[table]]
            table = parent[table]
        return table

    selected: list[dict[str, Any]] = []
    candidates = sorted(
        (relationship for relationship in metadata.get("relationships", []) if isinstance(relationship, dict)),
        key=lambda relationship: (
            -float(relationship.get("confidence", 0)),
            str(relationship.get("source_table", "")),
            str(relationship.get("source_column", "")),
            str(relationship.get("target_table", "")),
            str(relationship.get("target_column", "")),
        ),
    )
    for relationship in candidates:
        source = relationship.get("source_table")
        target = relationship.get("target_table")
        if (
            relationship.get("confidence", 0) < MIN_RELATIONSHIP_CONFIDENCE
            or source not in tables
            or target not in tables
            or source == target
            or find(source) == find(target)
        ):
            continue
        parent[find(source)] = find(target)
        selected.append(relationship)
    return selected


def metadata_to_semantic_model(metadata: dict[str, Any], target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    definition_dir = target_dir / "definition"
    tables_dir = definition_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    for table_name, table in metadata.get("tables", {}).items():
        lines = [f"table '{table_name}'"]
        for column in table.get("columns", []):
            column_name = str(column.get("name") if isinstance(column, dict) else column)
            lines.append(f"\tcolumn '{column_name}'")
            lines.append("\t\tdataType: string")
        for measure in table.get("measures", []):
            measure_name = measure if isinstance(measure, str) else measure.get("name")
            definition = table.get("measureDefinitions", {}).get(measure_name)
            if not isinstance(definition, dict) or not definition.get("column"):
                raise ValueError(f"Measure '{measure_name}' requires an executable definition")
            aggregation = str(definition.get("aggregation", "SUM")).upper()
            if aggregation not in {"SUM", "AVERAGE", "MIN", "MAX", "COUNT"}:
                raise ValueError(f"Unsupported aggregation '{aggregation}' for measure '{measure_name}'")
            expression = f"{aggregation}({_dax_column_reference(table_name, definition['column'])})"
            lines.append(f"\tmeasure {_tmdl_identifier(str(measure_name))} = {expression}")
        (tables_dir / f"{table_name}.tmdl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target_dir


def normalize_metadata(raw_metadata: dict[str, Any]) -> dict[str, Any]:
    tables: dict[str, dict[str, Any]] = {}
    for table_name, table in raw_metadata.get("tables", {}).items():
        columns = [
            (column.get("name") if isinstance(column, dict) else str(column))
            for column in table.get("columns", [])
        ]
        measures = [
            (measure.get("name") if isinstance(measure, dict) else str(measure))
            for measure in table.get("measures", [])
        ]
        tables[table_name] = {
            "name": table.get("name", table_name),
            "derivedFrom": table.get("derivedFrom"),
            "semanticFields": table.get("semanticFields", {}),
            "columns": columns,
            "columnTypes": {
                column.get("name"): column.get("dataType", "string")
                for column in table.get("columns", [])
                if isinstance(column, dict)
            },
            "columnRoles": {
                column.get("name"): column.get("role", "unknown")
                for column in table.get("columns", [])
                if isinstance(column, dict)
            },
            "columnFormats": {
                column.get("name"): column.get("formatString")
                for column in table.get("columns", [])
                if isinstance(column, dict)
            },
            "columnProfiles": {
                column.get("name"): column.get("profile", {})
                for column in table.get("columns", [])
                if isinstance(column, dict)
            },
            "measures": measures,
            "measureDefinitions": table.get("measureDefinitions", {}),
        }
    return {"tables": tables, "relationships": raw_metadata.get("relationships", [])}


def load_source_metadata(source_id: str) -> dict[str, Any]:
    payload = load_source_payload(source_id)
    raw_metadata = payload.get("metadata", payload)
    normalized = normalize_metadata(raw_metadata)
    normalized["parsingReview"] = payload.get("parsingReview", {})
    normalized["formulaCellCount"] = payload.get("workbookProfile", {}).get("workbook", {}).get("formula_count")
    for sheet in payload.get("workbookProfile", {}).get("sheets", []):
        for table in sheet.get("tables", []):
            table_name = table.get("name")
            if table_name in normalized["tables"] and table.get("derived_from"):
                normalized["tables"][table_name]["derivedFrom"] = table["derived_from"]
    return normalized


def load_source_payload(source_id: str) -> dict[str, Any]:
    if not isinstance(source_id, str) or not re.fullmatch(r"src-[a-f0-9]{8}", source_id):
        raise ValueError("Source ID is invalid")
    source_file = ensure_source_structure() / f"{source_id}.json"
    if not source_file.exists():
        raise ValueError(f"Source '{source_id}' could not be found")
    payload = json.loads(source_file.read_text(encoding="utf-8"), object_hook=_source_json_object_hook)
    from backend.services.identity_service import assert_owner
    assert_owner(payload.get("ownerId"), "Source")
    return payload


def save_source_metadata(source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(source_id, str) or not re.fullmatch(r"src-[a-f0-9]{8}", source_id):
        raise ValueError("Source ID is invalid")
    from backend.services.identity_service import current_user_id
    payload.setdefault("ownerId", current_user_id())
    source_file = ensure_source_structure() / f"{source_id}.json"
    source_file.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, default=_source_json_default),
        encoding="utf-8",
    )
    return payload


def validate_parsing_review(source: dict[str, Any], table_names: set[str]) -> None:
    reviews = source.get("parsingReview", {})
    tables = source.get("metadata", {}).get("tables", {})
    for table_name in table_names:
        original = tables.get(table_name, {}).get("derivedFrom") or table_name
        review = reviews.get(original)
        if review and not review.get("confirmed"):
            raise ValueError(f"Review and confirm the detected range and header for table '{original}' before planning or generation.")


def _source_json_default(value: Any) -> dict[str, str]:
    if isinstance(value, datetime):
        return {"__autobi_datetime__": value.isoformat()}
    if isinstance(value, date):
        return {"__autobi_date__": value.isoformat()}
    raise TypeError(f"Unsupported source metadata value: {type(value).__name__}")


def _source_json_object_hook(value: dict[str, Any]) -> Any:
    if set(value) == {"__autobi_datetime__"}:
        return datetime.fromisoformat(value["__autobi_datetime__"])
    if set(value) == {"__autobi_date__"}:
        return date.fromisoformat(value["__autobi_date__"])
    return value


def inspect_model_folder(model_path: str | Path) -> dict[str, Any]:
    return inspect_semantic_model(model_path)
