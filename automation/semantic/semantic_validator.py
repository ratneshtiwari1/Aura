from __future__ import annotations

from typing import Any

from automation.policy import MAX_VISUALS_PER_PAGE
from automation.report_automation import validate_report_spec
from automation.visual_registry import CATEGORY_VALUE_TYPES, MULTI_MEASURE_TYPES


def normalize_ai_table_bindings(plan: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, str]]:
    """Translate unambiguous table roles without choosing or inventing fields."""
    corrections = []

    def valid(binding: object, field_type: str) -> bool:
        if not isinstance(binding, dict) or binding.get("fieldType") != field_type:
            return False
        table_name, field_name = binding.get("table"), binding.get("field")
        if not isinstance(table_name, str) or not isinstance(field_name, str):
            return False
        table = metadata.get("tables", {}).get(table_name)
        names = table.get("columns" if field_type == "column" else "measures", []) if isinstance(table, dict) else []
        return field_name in names

    for visual in plan.get("visuals", []):
        if not isinstance(visual, dict) or visual.get("type") != "table":
            continue
        bindings = []
        for role in ("rows", "columns", "values"):
            if isinstance(visual.get(role), list):
                bindings.extend(visual[role])
        bindings.extend(visual.get(role) for role in ("category", "value"))
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            table_name = binding.get("table")
            field = binding.get("field")
            if not isinstance(table_name, str) or not isinstance(field, str):
                continue
            table = metadata.get("tables", {}).get(table_name)
            if not isinstance(table, dict):
                continue
            if valid(binding, "column") or valid(binding, "measure"):
                continue
            is_column = field in table.get("columns", [])
            is_measure = field in table.get("measures", [])
            if is_column == is_measure:
                continue
            old_type = binding.get("fieldType")
            new_type = "column" if is_column else "measure"
            binding["fieldType"] = new_type
            corrections.append({
                "type": "table", "from": str(old_type), "to": new_type,
            })
        columns = visual.get("columns")
        if (isinstance(columns, list) and columns and not visual.get("rows")
            and all(valid(field, "column") or valid(field, "measure") for field in columns)):
            rows = [field for field in columns if valid(field, "column")]
            measures = [field for field in columns if valid(field, "measure")]
            if rows and (not measures or (not visual.get("values") and not visual.get("value"))):
                visual["rows"] = rows
                if measures:
                    visual["values"] = measures
                del visual["columns"]
                corrections.append({"type": "table", "from": "columns", "to": "rows and values" if measures else "rows"})
        category = visual.get("category")
        if not visual.get("rows") and valid(category, "column"):
            visual["rows"] = [visual.pop("category")]
            corrections.append({"type": "table", "from": "category", "to": "rows"})
        value = visual.get("value")
        if not visual.get("values") and valid(value, "measure"):
            visual["values"] = [visual.pop("value")]
            corrections.append({"type": "table", "from": "value", "to": "values"})
        elif valid(value, "column") and (visual.get("rows") is None or isinstance(visual["rows"], list)):
            visual["rows"] = (visual.get("rows") or []) + [visual.pop("value")]
            corrections.append({"type": "table", "from": "value", "to": "rows"})
        values = visual.get("values")
        rows = visual.get("rows")
        if isinstance(values, list) and (rows is None or isinstance(rows, list)) and all(
            valid(field, "column") or valid(field, "measure") for field in values
        ):
            columns_in_values = [field for field in values if valid(field, "column")]
            if columns_in_values:
                visual["rows"] = (rows or []) + columns_in_values
                visual["values"] = [field for field in values if valid(field, "measure")]
                corrections.append({"type": "table", "from": "values", "to": "rows"})
    return corrections


def normalize_ai_measure_bindings(plan: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, str]]:
    """Resolve AI references to numeric columns into their generated measures.

    Business requests commonly use a base column name (for example, "Net
    Revenue") while the Power BI model exposes an aggregate measure ("Total
    Net Revenue"). This is a safe schema-based correction, not a guessed
    calculation: a binding changes only when exactly one existing measure is
    associated with the referenced numeric column.
    """
    corrections: list[dict[str, str]] = []
    if plan.get("status") == "needs_clarification":
        return corrections

    for visual in plan.get("visuals", []):
        if not isinstance(visual, dict):
            continue
        bindings = []
        if isinstance(visual.get("value"), dict):
            bindings.append(visual["value"])
        bindings.extend(field for field in visual.get("values", []) if isinstance(field, dict))
        comparison = visual.get("comparison")
        if isinstance(comparison, dict) and isinstance(comparison.get("value"), dict):
            bindings.append(comparison["value"])
        for binding in bindings:
            table = metadata.get("tables", {}).get(binding.get("table"))
            field = binding.get("field")
            # Broad AI plans occasionally preserve the right measure name but
            # attach it to a stale/display table name.  Correct that only when
            # the measure is globally unique in the uploaded schema.
            if isinstance(field, str) and (
                not table or field not in table.get("measures", [])
            ):
                matches = [
                    (name, measure)
                    for name, candidate in metadata.get("tables", {}).items()
                    for measure in candidate.get("measures", [])
                    if str(measure).casefold() == field.casefold()
                ]
                if len(matches) == 1:
                    binding["table"], binding["field"], binding["fieldType"] = matches[0][0], matches[0][1], "measure"
                    table = metadata["tables"][binding["table"]]
                    field = binding["field"]
            if not table or not isinstance(field, str) or field in table.get("measures", []):
                continue
            column = next((name for name in table.get("columns", []) if name.casefold() == field.strip().casefold()), None)
            numeric_types = {"integer", "decimal", "numeric", "int64", "double", "float"}
            if not column or str(table.get("columnTypes", {}).get(column, "")).casefold() not in numeric_types:
                continue
            candidates = [
                measure for measure, definition in table.get("measureDefinitions", {}).items()
                if isinstance(definition, dict) and str(definition.get("column", "")).casefold() == column.casefold()
            ]
            if not candidates:
                candidates = [
                    measure for measure in table.get("measures", [])
                    if measure.casefold() in {f"total {column}".casefold(), f"average {column}".casefold()}
                ]
            if len(candidates) != 1:
                continue
            original = field
            binding["field"] = candidates[0]
            binding["fieldType"] = "measure"
            corrections.append({"table": binding["table"], "from": original, "to": candidates[0]})
    return corrections


def validate_ai_plan(
    plan: dict[str, Any],
    metadata: dict[str, Any],
    requested_count: int | None,
    requested_count_mode: str = "analytical",
) -> None:
    if plan.get("status", "ready") == "needs_clarification":
        question = plan.get("question")
        options = plan.get("options")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("AI clarification response must include a question")
        if options is not None and (
            not isinstance(options, list) or any(not isinstance(option, str) for option in options)
        ):
            raise ValueError("AI clarification options must be a list of strings")
        return

    visuals = plan.get("visuals")
    if not isinstance(visuals, list):
        raise ValueError("AI dashboard plan must include a visuals list")
    if len(visuals) > MAX_VISUALS_PER_PAGE:
        raise ValueError(f"AI plan exceeds the {MAX_VISUALS_PER_PAGE}-visual page limit")
    analytical_visual_count = sum(visual.get("type") != "slicer" for visual in visuals if isinstance(visual, dict))
    accepted_counts = {
        "total": {len(visuals)},
        "analytical": {analytical_visual_count if analytical_visual_count else len(visuals)},
        "either": {len(visuals), analytical_visual_count if analytical_visual_count else len(visuals)},
    }
    if requested_count_mode not in accepted_counts:
        raise ValueError("Requested visual count mode is invalid")
    if requested_count is not None and requested_count not in accepted_counts[requested_count_mode]:
        expected = "total items" if requested_count_mode == "total" else "analytical visuals" if requested_count_mode == "analytical" else "total or analytical visuals"
        raise ValueError(
            f"AI returned {analytical_visual_count} analytical visuals and "
            f"{len(visuals) - analytical_visual_count} slicers; exactly {requested_count} {expected} were requested"
        )
    if not visuals:
        raise ValueError("AI dashboard plan must include at least one visual")

    page_name = plan.get("page")
    if not isinstance(page_name, str) or not page_name.strip():
        raise ValueError("AI dashboard plan must include a page name")
    rationale = plan.get("rationale")
    if rationale is not None and (not isinstance(rationale, str) or len(rationale) > 500):
        raise ValueError("AI dashboard plan rationale must be a short string")
    for visual in visuals:
        if not isinstance(visual, dict):
            raise ValueError("AI dashboard visuals must be JSON objects")
        visual_type = visual.get("type")
        required_roles = {
            "card": ("value",),
            "multiRowCard": ("value",),
            "kpi": ("value",),
            "slicer": ("field",),
            "pivotTable": ("rows", "values"),
            "clusteredColumnChart": ("category",),
            "clusteredBarChart": ("category",),
            "stackedColumnChart": ("category",),
            "stackedBarChart": ("category",),
            "hundredPercentStackedColumnChart": ("category",),
            "hundredPercentStackedBarChart": ("category",),
            "lineChart": ("category",),
            "areaChart": ("category",),
            "stackedAreaChart": ("category",),
            "pieChart": ("category",),
            "donutChart": ("category",),
            "lineClusteredColumnComboChart": ("category", "values"),
            "lineStackedColumnComboChart": ("category", "values"),
            "scatterChart": ("category", "values"),
            "waterfallChart": ("category",),
            "funnel": ("category",),
            "treemap": ("category",),
            "map": ("category",),
            "filledMap": ("category",),
            "ribbonChart": ("category",),
            "decompositionTree": ("category",),
            "gauge": ("value",),
            "table": ("rows",),
        }
        if visual_type not in required_roles:
            raise ValueError(f"Unsupported visual type from AI: {visual_type}")
        for role in required_roles[visual_type]:
            binding = visual.get(role)
            if role in {"rows", "values"}:
                if not isinstance(binding, list) or not binding:
                    raise ValueError(f"AI {visual_type} visual must include {role}")
                expected_type = "measure" if role == "values" else "column"
                if any(not _valid_binding(field, expected_type) for field in binding):
                    raise ValueError(f"AI {visual_type} {role} must use {expected_type} field bindings")
            elif not isinstance(binding, dict):
                raise ValueError(f"AI {visual_type} visual must include {role}")
            elif not _valid_binding(binding, "measure" if role == "value" else "column"):
                raise ValueError(f"AI {visual_type} {role} has an invalid field binding")
        if visual_type == "table" and "values" in visual and (
            not isinstance(visual["values"], list)
            or any(not _valid_binding(field, "measure") for field in visual["values"])
        ):
            raise ValueError("AI table values must use measure field bindings")
        if visual_type in CATEGORY_VALUE_TYPES:
            if not visual.get("value") and not visual.get("values"):
                raise ValueError(f"AI {visual_type} visual must include a measure")
            if visual.get("values") and (
                not isinstance(visual["values"], list)
                or any(not _valid_binding(field, "measure") for field in visual["values"])
            ):
                raise ValueError(f"AI {visual_type} values must use measure field bindings")
        if visual_type in MULTI_MEASURE_TYPES and len(visual.get("values", [])) < 2:
            raise ValueError(f"AI {visual_type} visual must include two measures")
        if visual.get("legend") and not _valid_binding(visual["legend"], "column"):
            raise ValueError("AI chart legend must use a column field binding")
        if visual.get("legendBreakdown") and (
            not isinstance(visual["legendBreakdown"], list)
            or any(not _valid_binding(field, "column") for field in visual["legendBreakdown"])
        ):
            raise ValueError("AI chart legend breakdown must use column field bindings")

    normalized = {
        "page": page_name,
        "visuals": visuals,
        "design": plan.get("design", {}),
    }
    validate_report_spec(normalized, metadata)

    for visual in visuals:
        visual_type = visual.get("type")
        measure_fields = ([visual["value"]] if visual.get("value") else []) + visual.get("values", [])
        for measure in measure_fields:
            table = metadata["tables"][measure["table"]]
            field_name = measure["field"]
            definition = table.get("measureDefinitions", {}).get(field_name)
            if definition:
                column_name = definition["column"]
            elif field_name.startswith("Total "):
                column_name = field_name.removeprefix("Total ")
            elif field_name in table.get("columnTypes", {}):
                column_name = field_name
            else:
                continue
            column_type = table.get("columnTypes", {}).get(column_name)
            if column_type not in {"integer", "decimal", "numeric", "int64", "double", "float"}:
                raise ValueError(f"Measure '{field_name}' is not numeric")


def _valid_binding(binding: Any, expected_type: str) -> bool:
    return (
        isinstance(binding, dict)
        and isinstance(binding.get("table"), str)
        and isinstance(binding.get("field"), str)
        and binding.get("fieldType") == expected_type
    )
