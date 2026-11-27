"""Calculation-level quality gate for generated Power BI semantic models."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from backend.services.model_service import _active_relationships


def _dax_column(table: str, column: str) -> str:
    return f"'{table.replace(chr(39), chr(39) * 2)}'[{column.replace(']', ']]')}]"


def _expected_measure_expression(table: str, measure: str, metadata: dict[str, Any]) -> str:
    definition = metadata["tables"][table].get("measureDefinitions", {}).get(measure, {})
    column = definition.get("column", measure.removeprefix("Total "))
    aggregation = str(definition.get("aggregation", "SUM")).upper()
    aggregate = f"{aggregation}({_dax_column(table, column)})"
    if definition.get("filterColumn"):
        value = str(definition.get("filterValue", "")).replace('"', '""')
        return (
            f"CALCULATE({aggregate}, KEEPFILTERS("
            f"{_dax_column(table, definition['filterColumn'])} = \"{value}\"))"
        )
    return aggregate


def _measure_total(rows: list[dict[str, Any]], table: str, measure: str, metadata: dict[str, Any]) -> float:
    definition = metadata["tables"][table].get("measureDefinitions", {}).get(measure, {})
    column = definition.get("column", measure.removeprefix("Total "))
    if definition.get("filterColumn"):
        rows = [row for row in rows if row.get(definition["filterColumn"]) == definition.get("filterValue")]
    values: list[float] = []
    for row in rows:
        try:
            values.append(float(row[column]))
        except (KeyError, TypeError, ValueError):
            continue
    if not values:
        return 0.0
    aggregation = str(definition.get("aggregation", "SUM")).upper()
    if aggregation == "AVERAGE":
        return sum(values) / len(values)
    if aggregation == "MIN":
        return min(values)
    if aggregation == "MAX":
        return max(values)
    if aggregation == "COUNT":
        return float(len(values))
    return sum(values)


def _bindings(visual: dict[str, Any]) -> list[dict[str, Any]]:
    values = []
    for key in ("category", "value", "field", "legend"):
        if isinstance(visual.get(key), dict):
            values.append(visual[key])
    for key in ("values", "rows", "categoryHierarchy", "legendBreakdown"):
        values.extend(item for item in visual.get(key, []) if isinstance(item, dict))
    comparison = visual.get("comparison")
    if isinstance(comparison, dict) and isinstance(comparison.get("value"), dict):
        values.append(comparison["value"])
    return values


def _path_exists(graph: dict[str, set[str]], start: str, target: str) -> bool:
    pending, visited = [start], set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(graph.get(current, set()) - visited)
    return False


def _relationship_graph(metadata: dict[str, Any]) -> dict[str, set[str]]:
    graph = {table: set() for table in metadata.get("tables", {})}
    for relationship in _active_relationships(metadata):
        source, target = relationship["source_table"], relationship["target_table"]
        graph[source].add(target)
        graph[target].add(source)
    return graph


def incompatible_slicer_indexes(report_spec: dict[str, Any], metadata: dict[str, Any]) -> list[int]:
    """Find page slicers that cannot reach every measure or column-only table."""
    graph = _relationship_graph(metadata)
    measure_tables = {
        binding["table"]
        for visual in report_spec.get("visuals", [])
        for binding in _bindings(visual)
        if binding.get("fieldType") == "measure"
    }
    measure_tables.update(
        field["table"]
        for visual in report_spec.get("visuals", [])
        if visual.get("type") == "table" and not visual.get("values")
        for field in visual.get("rows", [])
    )
    return [
        index for index, visual in enumerate(report_spec.get("visuals", []))
        if visual.get("type") == "slicer"
        and any(not _path_exists(graph, visual["field"]["table"], table) for table in measure_tables)
    ]


def validate_calculation_accuracy(
    report_spec: dict[str, Any],
    metadata: dict[str, Any],
    source_payload: dict[str, Any],
    semantic_model_dir: Path,
    preview: dict[str, Any],
) -> dict[str, Any]:
    """Validate DAX, raw totals, slicers, and active join paths.

    This validates the exact model artifacts consumed by Power BI. A live
    Power BI engine comparison can be layered on later, but is not falsely
    claimed when Desktop/XMLA is unavailable.
    """
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    raw_tables = source_payload.get("metadata", {}).get("tables", {})
    tmdl_files = list((semantic_model_dir / "definition").rglob("*.tmdl"))
    tmdl_text = "\n".join(path.read_text(encoding="utf-8") for path in tmdl_files)

    measure_bindings = {
        (binding["table"], binding["field"])
        for visual in report_spec.get("visuals", [])
        for binding in _bindings(visual)
        if binding.get("fieldType") == "measure"
    }
    for table, measure in sorted(measure_bindings):
        expected = _expected_measure_expression(table, measure, metadata)
        passed = expected in tmdl_text
        checks.append({"type": "measure", "table": table, "measure": measure, "passed": passed, "expectedDax": expected})
        if not passed:
            errors.append(f"Generated DAX for {table}[{measure}] does not match its validated measure definition")

    active = _active_relationships(metadata)
    graph = _relationship_graph(metadata)
    for relationship in active:
        source, target = relationship["source_table"], relationship["target_table"]
        source_ref = f"fromColumn: '{source}'.'{relationship['source_column']}'"
        target_ref = f"toColumn: '{target}'.'{relationship['target_column']}'"
        passed = source_ref in tmdl_text and target_ref in tmdl_text
        source_rows = raw_tables.get(source, {}).get("rows", [])
        target_rows = raw_tables.get(target, {}).get("rows", [])
        source_keys = {str(row.get(relationship["source_column"])) for row in source_rows if row.get(relationship["source_column"]) is not None}
        target_keys = {str(row.get(relationship["target_column"])) for row in target_rows if row.get(relationship["target_column"]) is not None}
        overlap = len(source_keys & target_keys)
        passed = passed and (overlap > 0 or not source_rows or not target_rows)
        checks.append({"type": "join", "source": source, "target": target, "matchedKeys": overlap, "passed": passed})
        if not passed:
            errors.append(f"Relationship {source} to {target} is missing from the model or has no matching keys")

    preview_visuals = preview.get("visuals", [])
    for index, visual in enumerate(report_spec.get("visuals", [])):
        observed = preview_visuals[index] if index < len(preview_visuals) else {}
        visual_type = visual.get("type")
        if visual_type == "slicer":
            field = visual["field"]
            rows = raw_tables.get(field["table"], {}).get("rows", [])
            expected_options = sorted({str(row[field["field"]]) for row in rows if row.get(field["field"]) is not None})
            passed = expected_options == observed.get("options", [])
            checks.append({"type": "filter", "visual": index, "field": f"{field['table']}[{field['field']}]", "passed": passed, "optionCount": len(expected_options)})
            if not passed:
                errors.append(f"Slicer values for {field['table']}[{field['field']}] do not match source values")
            continue

        value = visual.get("value") or next(iter(visual.get("values", [])), None)
        category = visual.get("category") or next(iter(visual.get("rows", [])), None)
        if not isinstance(value, dict):
            continue
        rows = raw_tables.get(value["table"], {}).get("rows", [])
        if visual_type in {"card", "multiRowCard", "kpi", "gauge"}:
            expected_total = _measure_total(rows, value["table"], value["field"], metadata)
            actual = (observed.get("data") or [{}])[0].get("value")
            passed = isinstance(actual, (int, float)) and math.isclose(float(actual), expected_total, rel_tol=1e-9, abs_tol=1e-6)
            checks.append({"type": "total", "visual": index, "measure": f"{value['table']}[{value['field']}]", "expected": expected_total, "actual": actual, "passed": passed})
            if not passed:
                errors.append(f"Preview total for {value['table']}[{value['field']}] does not match source calculation")
        if isinstance(category, dict) and category["table"] != value["table"]:
            passed = _path_exists(graph, category["table"], value["table"])
            checks.append({"type": "joinPath", "visual": index, "from": category["table"], "to": value["table"], "passed": passed})
            if not passed:
                errors.append(f"Visual {index + 1} has no active Power BI relationship path between its category and measure")

    slicers = [visual["field"] for visual in report_spec.get("visuals", []) if visual.get("type") == "slicer"]
    for slicer in slicers:
        for table, measure in sorted(measure_bindings):
            passed = slicer["table"] == table or _path_exists(graph, slicer["table"], table)
            checks.append({"type": "filterPropagation", "field": f"{slicer['table']}[{slicer['field']}]", "measure": f"{table}[{measure}]", "passed": passed})
            if not passed:
                errors.append(f"Slicer {slicer['table']}[{slicer['field']}] cannot filter {table}[{measure}] through an active relationship")
        column_tables = {
            field["table"]
            for visual in report_spec.get("visuals", [])
            if visual.get("type") == "table" and not visual.get("values")
            for field in visual.get("rows", [])
        }
        for table in sorted(column_tables):
            passed = _path_exists(graph, slicer["table"], table)
            checks.append({"type": "filterPropagation", "field": f"{slicer['table']}[{slicer['field']}]", "table": table, "passed": passed})
            if not passed:
                errors.append(f"Slicer {slicer['table']}[{slicer['field']}] cannot filter table {table} through an active relationship")

    return {
        "passed": not errors,
        "engine": "generated Power BI TMDL plus independent source-data evaluation",
        "checks": checks,
        "errors": errors,
    }
