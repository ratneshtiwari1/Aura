from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from backend.policy import MAX_PREVIEW_POINTS, MAX_RANKING_COUNT, MAX_VISUALS_PER_PAGE


def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold()))


def _numeric(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def understand(metadata: dict[str, Any], ai_understanding: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return reviewable business roles; preserve an existing AI classification."""
    ai_columns = {
        (table.get("name"), column.get("name")): column
        for table in (ai_understanding or {}).get("tables", [])
        if isinstance(table, dict)
        for column in table.get("columns", [])
        if isinstance(column, dict)
    }
    tables = []
    for table_name, table in metadata.get("tables", {}).items():
        fields = []
        for column in table.get("columns", []):
            profile = table.get("columnProfiles", {}).get(column, {})
            field_type = table.get("columnTypes", {}).get(column, "string")
            name_words = _words(column)
            ai = ai_columns.get((table_name, column), {})
            role = ai.get("semantic_role")
            if not role:
                role = ("time" if {"date", "year", "month", "quarter", "period"} & name_words
                        else "identifier" if {"id", "key", "code"} & name_words
                        else "measure" if field_type in {"decimal", "integer", "number"}
                        else "dimension")
            scenario = bool({"actual", "budget", "plan", "forecast", "target", "scenario"} & name_words)
            fields.append({"field": column, "role": role, "dataType": field_type,
                           "distinctValues": profile.get("distinctCount"), "scenarioCandidate": scenario,
                           "explanation": ai.get("description", "Inferred from the field name and type.")})
        tables.append({"table": table_name, "fields": fields, "measures": table.get("measures", [])})
    return {"tables": tables, "requiresConfirmation": True,
            "message": "Review the inferred roles before applying model changes."}


_NUMERIC_TYPES = {"decimal", "integer", "number", "float", "double", "currency"}
_TIME_WORDS = {"date", "day", "week", "month", "quarter", "year", "period", "time", "timestamp"}
_SCHEMA_WORDS = {
    "table", "tables", "column", "columns", "field", "fields", "schema", "measure", "measures",
}
_HIGH_WORDS = {"highest", "largest", "most", "top", "best", "maximum", "max"}
_LOW_WORDS = {"lowest", "smallest", "least", "bottom", "worst", "minimum", "min"}
_COMPARISON_WORDS = {"compare", "comparison", "versus", "vs", "difference", "variance", "against"}
_GENERIC_MEASURE_WORDS = {"total", "sum", "average", "avg", "count", "minimum", "maximum", "min", "max"}


def _field_score(name: str, question_words: set[str]) -> int:
    words = _words(name)
    meaningful = words - _GENERIC_MEASURE_WORDS
    return len(words & question_words) + (2 * len(meaningful & question_words))


def _field_match_coverage(name: str, question_words: set[str]) -> float:
    meaningful = _words(name) - _GENERIC_MEASURE_WORDS
    return len(meaningful & question_words) / len(meaningful) if meaningful else 0.0


def _normalized_words(value: str) -> set[str]:
    words = _words(value)
    return {
        word[:-3] + "y" if word.endswith("ies")
        else word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss")
        else word
        for word in words
    }


def _count_distinct_question(
    metadata: dict[str, Any],
    raw_tables: dict[str, Any],
    question: str,
) -> dict[str, Any] | None:
    """Answer generic distinct-value count questions from an identified field."""
    words = _normalized_words(question)
    count_request = re.search(
        r"\bhow\s+many\b|\b(?:number|count)\s+of\b|\bcount\s+(?:distinct|unique)\b|\b(?:distinct|unique)\s+(?:count|number)\b",
        question,
        re.IGNORECASE,
    )
    if not count_request:
        return None

    intent_words = words - {
        "how", "many", "number", "count", "of", "the", "in", "on", "for", "from",
        "available", "data", "details", "detail", "do", "we", "have", "is", "are",
        "there", "what", "all", "distinct", "unique",
    }
    time_intent = bool(intent_words & {"year", "month", "quarter", "date", "period", "time"})
    row_count_intent = bool(intent_words & {"row", "record", "entry"})
    selected_table = metadata.get("preferredTable")
    tables = metadata.get("tables", {})

    named = [name for name in tables if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", question, re.I)]
    if len(named) == 1:
        selected_table = named[0]
        intent_words -= _normalized_words(named[0])
    record_terms = {"row", "record", "entry", "transaction"}
    if intent_words & record_terms and not (time_intent or intent_words - record_terms):
        table_candidates = [
            (name, table, raw_tables.get(name, {}).get("rows", []))
            for name, table in tables.items()
            if raw_tables.get(name, {}).get("rows")
        ]
        if isinstance(selected_table, str):
            table_candidates = [item for item in table_candidates if item[0] == selected_table] or table_candidates
        if len(table_candidates) == 1:
            name, _, rows = table_candidates[0]
            count = len(rows)
            return {
                "answer": f"The selected data contains {count:,} records.",
                "question": question,
                "method": "Counted source rows without aggregating or changing the data.",
                "sourceFields": [f"{name}.*"],
                "findings": [{"table": name, "recordCount": count}],
                "filters": [],
            }

    candidates: list[tuple[int, int, str, str, list[Any]]] = []
    for table_name, table in tables.items():
        rows = raw_tables.get(table_name, {}).get("rows", [])
        if not rows:
            continue
        for column in _dimensions(table):
            normalized_name = _normalized_words(column)
            overlap = len(normalized_name & intent_words)
            role = str(table.get("columnRoles", {}).get(column, "")).casefold()
            is_time = role == "time" or bool(normalized_name & _TIME_WORDS)
            if time_intent and not is_time:
                continue
            if overlap == 0 and not (time_intent and is_time and len([
                field for field in table.get("columns", [])
                if str(table.get("columnRoles", {}).get(field, "")).casefold() == "time"
                or _normalized_words(field) & _TIME_WORDS
            ]) == 1):
                continue
            values = list(dict.fromkeys(row.get(column) for row in rows if row.get(column) is not None))
            if not values:
                continue
            table_preference = int(table_name == selected_table)
            candidates.append((overlap, table_preference, table_name, column, values))

    if not candidates:
        return None
    best_score = max((overlap, preferred) for overlap, preferred, *_ in candidates)
    best = [item for item in candidates if item[:2] == best_score]
    if len(best) != 1:
        options = ", ".join(f"{table}[{column}]" for _, _, table, column, _ in best[:MAX_PREVIEW_POINTS])
        return {
            "clarificationOptions": options,
            "answer": "I couldn't identify one field to count safely.",
            "question": question,
            "method": "No count was calculated because multiple fields match the question.",
            "sourceFields": [],
            "findings": [],
            "filters": [],
            "nextStep": f"Which field should I count distinct values for: {options}?",
        }

    _, _, table_name, column, values = best[0]
    values.sort(key=lambda value: (0, float(value)) if _numeric(value) is not None else (1, str(value).casefold()))
    count = len(values)
    sample_values = [str(value) for value in values[:MAX_PREVIEW_POINTS]]
    if time_intent and count:
        value_range = f" ({sample_values[0]}–{sample_values[-1]})" if count > 1 else f" ({sample_values[0]})"
        answer = f"There are {count} distinct {column} values{value_range} in {table_name}."
    else:
        answer = f"There are {count} distinct {column} values in {table_name}."
    return {
        "answer": answer,
        "question": question,
        "method": f"Counted distinct non-empty values in {column}.",
        "sourceFields": [f"{table_name}.{column}"],
        "findings": [{"distinctCount": count, "values": ", ".join(sample_values)}],
        "filters": [],
    }


def _answer_aggregate_question(
    metadata: dict[str, Any],
    raw_tables: dict[str, Any],
    question: str,
) -> dict[str, Any] | None:
    if re.search(r"\b(?:by|per|across|for each|per each|trend|over time|timeline)\b", question, re.IGNORECASE):
        return None
    if not re.search(
        r"\b(?:total|sum|overall|in total|altogether|average|avg|mean|how much)\b",
        question,
        re.IGNORECASE,
    ):
        return None

    question_words = _normalized_words(question)
    preferred_table = metadata.get("preferredTable")
    candidates: list[tuple[int, int, str, dict[str, Any], str, list[dict[str, Any]]]] = []
    for table_name, table in metadata.get("tables", {}).items():
        rows = raw_tables.get(table_name, {}).get("rows", [])
        if not rows:
            continue
        ranked = _ranked_names(table.get("measures", []), question_words)
        if not ranked or ranked[0][0] <= 0:
            continue
        score, measure = ranked[0]
        candidates.append((score, int(table_name == preferred_table), table_name, table, measure, rows))

    if not candidates:
        return None
    best_score = max((score, preferred) for score, preferred, *_ in candidates)
    best = [item for item in candidates if item[:2] == best_score]
    best_measures = {(item[2], item[4]) for item in best}
    if len(best_measures) != 1:
        options = ", ".join(f"{table}[{measure}]" for table, measure in sorted(best_measures))
        return {
            "clarificationOptions": options,
            "answer": "I couldn't identify one measure to summarize safely.",
            "question": question,
            "method": "No aggregation was calculated because multiple measures match the question.",
            "sourceFields": [],
            "findings": [],
            "filters": [],
            "nextStep": f"Which measure should I use: {options}?",
        }

    _, _, table_name, table, measure, rows = best[0]
    resolved_filters = _aggregate_filters(table, rows, question)
    if isinstance(resolved_filters, str):
        return {
            "answer": resolved_filters,
            "question": question,
            "method": "No aggregation was calculated because a requested filter could not be resolved safely.",
            "sourceFields": [],
            "findings": [],
            "filters": [],
            "nextStep": resolved_filters,
        }
    filters, filtered_rows = resolved_filters
    value = _aggregate_measure(table, measure, filtered_rows)
    filter_description = " and ".join(f"{item['field']} = {item['value']}" for item in filters)
    fields = [f"{table_name}.{measure}", *(f"{table_name}.{item['field']}" for item in filters)]
    finding: dict[str, Any] = {"measure": measure, "value": value}
    if filters:
        finding["filters"] = filters
    return {
        "answer": f"{measure}: {value:,.2f}",
        "question": question,
        "method": (
            f"Applied the model-defined aggregation for {measure} after applying {filter_description}."
            if filters else f"Applied the model-defined aggregation for {measure} across all available rows."
        ),
        "sourceFields": fields,
        "findings": [finding],
        "filters": filters,
    }


def _answer_distinct_values_question(
    metadata: dict[str, Any],
    raw_tables: dict[str, Any],
    question: str,
) -> dict[str, Any] | None:
    if re.search(
        r"\b(?:highest|largest|most|top|best|maximum|max|lowest|smallest|least|bottom|worst|minimum|min|trend|over time|timeline|compare|comparison|versus|vs|difference|variance|average|avg|mean|total|sum)\b",
        question,
        re.IGNORECASE,
    ):
        return None
    if re.search(r"\bhow\s+many\b|\b(?:number|count)\s+of\b", question, re.IGNORECASE):
        return None
    if not re.search(r"\b(?:what|which|list|show|available)\b", question, re.IGNORECASE):
        return None
    question_words = _normalized_words(question)
    ignored_words = {
        "what", "which", "list", "show", "available", "are", "is", "the", "all",
        "value", "values", "category", "categories", "option", "options", "in",
        "on", "for", "from", "data", "dataset", "workbook", "table", "do", "we",
        "have", "there", "and",
    }
    intent_words = question_words - ignored_words
    if not intent_words:
        return None

    selected_table = metadata.get("preferredTable")
    candidates: list[tuple[int, int, str, str, list[Any]]] = []
    for table_name, table in metadata.get("tables", {}).items():
        rows = raw_tables.get(table_name, {}).get("rows", [])
        if not rows:
            continue
        for column in _dimensions(table):
            column_words = _normalized_words(column)
            overlap = len(column_words & intent_words)
            if not overlap:
                continue
            values = list(dict.fromkeys(row.get(column) for row in rows if row.get(column) is not None))
            if values:
                candidates.append((overlap, int(table_name == selected_table), table_name, column, values))

    if not candidates:
        return None
    best_score = max(item[:2] for item in candidates)
    best = [item for item in candidates if item[:2] == best_score]
    if len(best) != 1:
        options = ", ".join(f"{table}[{column}]" for _, _, table, column, _ in best[:MAX_PREVIEW_POINTS])
        return {
            "clarificationOptions": options,
            "answer": "I couldn't identify one field to list safely.",
            "question": question,
            "method": "No values were listed because multiple fields match the question.",
            "sourceFields": [],
            "findings": [],
            "filters": [],
            "nextStep": f"Which field should I list: {options}?",
        }

    _, _, table_name, column, values = best[0]
    values.sort(key=lambda value: (0, float(value)) if _numeric(value) is not None else (1, str(value).casefold()))
    shown_values = [str(value) for value in values[:MAX_PREVIEW_POINTS]]
    return {
        "answer": f"{column} has {len(values)} distinct values in {table_name}: {', '.join(shown_values)}"
        + ("." if len(values) <= MAX_PREVIEW_POINTS else f", and {len(values) - MAX_PREVIEW_POINTS} more."),
        "question": question,
        "method": f"Listed distinct non-empty values from {column}.",
        "sourceFields": [f"{table_name}.{column}"],
        "findings": [{"field": column, "distinctCount": len(values), "values": ", ".join(shown_values)}],
        "filters": [],
    }


def _measure_column(measure: str, definition: dict[str, Any]) -> str:
    configured = definition.get("column")
    if isinstance(configured, str) and configured:
        return configured
    return re.sub(r"^(?:total|sum|average|avg|count|minimum|maximum|min|max)\s+", "", measure, flags=re.I)


def _aggregate_measure(table: dict[str, Any], measure: str, rows: list[dict[str, Any]]) -> float:
    definition = table.get("measureDefinitions", {}).get(measure, {})
    column = _measure_column(measure, definition)
    filter_column = definition.get("filterColumn")
    filter_value = definition.get("filterValue")
    values = [
        numeric
        for row in rows
        if not filter_column or row.get(filter_column) == filter_value
        if (numeric := _numeric(row.get(column))) is not None
    ]
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


def _quarter_number(question: str) -> int | None:
    ordinal = re.search(r"\b(first|1st|one|second|2nd|two|third|3rd|three|fourth|4th|four)\s+quarter\b", question, re.I)
    if ordinal:
        return {
            "first": 1, "1st": 1, "one": 1,
            "second": 2, "2nd": 2, "two": 2,
            "third": 3, "3rd": 3, "three": 3,
            "fourth": 4, "4th": 4, "four": 4,
        }[ordinal.group(1).casefold()]
    numeric = re.search(r"\b(?:q|quarter\s*)([1-4])\b|\bquarter\s+(one|two|three|four)\b", question, re.I)
    if not numeric:
        return None
    value = numeric.group(1) or numeric.group(2).casefold()
    return int(value) if value.isdigit() else {"one": 1, "two": 2, "three": 3, "four": 4}[value]


def _date_value(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None


def _is_quarter_value(value: object, quarter: int) -> bool:
    text = str(value).strip().casefold()
    ordinal = {1: "first", 2: "second", 3: "third", 4: "fourth"}[quarter]
    return bool(
        re.search(rf"\bq?0?{quarter}\b", text)
        or re.search(rf"\bquarter\s+{quarter}\b", text)
        or text == ordinal
        or text == f"{ordinal} quarter"
    )


def _aggregate_filters(
    table: dict[str, Any],
    rows: list[dict[str, Any]],
    question: str,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]] | str:
    filters: list[dict[str, str]] = []
    filtered_rows = rows
    quarter = _quarter_number(question)
    columns = table.get("columns", [])
    if quarter is not None:
        quarter_columns = [column for column in columns if "quarter" in _words(column)]
        preferred_time_field = table.get("preferredTimeField")
        date_columns = [
            column for column in columns
            if str(table.get("columnTypes", {}).get(column, "")).casefold() in {"date", "datetime"}
        ]
        if preferred_time_field in date_columns:
            column = preferred_time_field
            matching_rows = [
                row for row in rows
                if (value := _date_value(row.get(column))) is not None
                and (value.month - 1) // 3 + 1 == quarter
            ]
            filters.append({"field": column, "value": f"Q{quarter}"})
        elif len(quarter_columns) == 1:
            column = quarter_columns[0]
            matching_rows = [
                row for row in rows if _is_quarter_value(row.get(column), quarter)
            ]
            filters.append({"field": column, "value": f"Q{quarter}"})
        else:
            if len(date_columns) != 1:
                options = ", ".join(date_columns) if date_columns else "a date or quarter field"
                return f"I couldn't safely identify one field for Q{quarter}. Which date/quarter field should I use? Available: {options}."
            column = date_columns[0]
            matching_rows = [
                row for row in rows
                if (value := _date_value(row.get(column))) is not None
                and (value.month - 1) // 3 + 1 == quarter
            ]
            filters.append({"field": column, "value": f"Q{quarter}"})
        filtered_rows = matching_rows

    question_words = _normalized_words(question)
    unqualified_matches: list[tuple[str, object]] = []
    matched_dimension = False
    for column in _dimensions(table):
        if quarter is not None and "quarter" in _words(column):
            continue
        values = list(dict.fromkeys(row.get(column) for row in rows if row.get(column) is not None))
        matches = [
            value for value in values
            if isinstance(value, str)
            and value.strip()
            and re.search(rf"(?<!\w){re.escape(value.strip())}(?!\w)", question, re.I)
        ]
        if not matches:
            continue
        matches.sort(key=lambda value: len(_words(value)), reverse=True)
        field_score = _field_score(column, question_words)
        if field_score:
            matched_dimension = True
            filters.append({"field": column, "value": str(matches[0])})
        else:
            unqualified_matches.extend((column, value) for value in matches)

    if unqualified_matches and not matched_dimension:
        best_score = max(_field_score(column, question_words) for column, _ in unqualified_matches)
        best = [(column, value) for column, value in unqualified_matches
                if _field_score(column, question_words) == best_score]
        if best_score == 0 and len({column for column, _ in best}) != 1:
            options = ", ".join(sorted({column for column, _ in best}))
            return f"I found the requested value in multiple fields. Which field should I filter: {options}?"
        column, value = best[0]
        filters.append({"field": column, "value": str(value)})

    for filter_item in filters:
        column, value = filter_item["field"], filter_item["value"]
        if value.startswith("Q") and value[1:].isdigit():
            quarter_number = int(value[1:])
            if column in [item for item in columns if "quarter" in _words(item)]:
                filtered_rows = [
                    row for row in filtered_rows
                    if _is_quarter_value(row.get(column), quarter_number)
                ]
            else:
                filtered_rows = [
                    row for row in filtered_rows
                    if (parsed := _date_value(row.get(column))) is not None
                    and (parsed.month - 1) // 3 + 1 == quarter_number
                ]
        else:
            filtered_rows = [row for row in filtered_rows if str(row.get(column, "" )).casefold() == value.casefold()]
    return filters, filtered_rows


def _dimensions(table: dict[str, Any]) -> list[str]:
    definitions = table.get("measureDefinitions", {})
    measure_columns = {
        definition.get("column")
        for definition in definitions.values()
        if isinstance(definition, dict) and definition.get("column")
    }
    for column in table.get("columns", []):
        column_words = _words(column)
        if column_words and any(column_words <= _words(measure) for measure in table.get("measures", [])):
            measure_columns.add(column)
    result = []
    for column in table.get("columns", []):
        column_type = str(table.get("columnTypes", {}).get(column, "string")).casefold()
        if column_type not in _NUMERIC_TYPES or _words(column) & _TIME_WORDS or column not in measure_columns:
            result.append(column)
    return result


def _ranked_names(names: list[str], question_words: set[str]) -> list[tuple[int, str]]:
    return sorted(
        ((_field_score(name, question_words), name) for name in names),
        key=lambda item: (item[0], item[1].casefold()),
        reverse=True,
    )


def kpi_suggestions(metadata: dict[str, Any]) -> list[dict[str, str]]:
    """Suggest KPI cards from validated measures without assuming a business domain."""
    suggestions: list[dict[str, str]] = []
    for table_name, table in metadata.get("tables", {}).items():
        for measure in table.get("measures", []):
            suggestions.append({
                "name": measure,
                "table": table_name,
                "formula": f"[{measure}]",
                "dax": "",
                "explanation": "Use the existing validated model measure as a KPI card.",
            })
            if len(suggestions) >= MAX_VISUALS_PER_PAGE:
                return suggestions
    return suggestions


def investigate(metadata: dict[str, Any], raw_tables: dict[str, Any], question: str) -> dict[str, Any]:
    """Resolve fields from metadata and compute an evidence-backed answer.

    The resolver is domain-neutral: it matches the user's words to available
    measures and dimensions, then applies only aggregations declared by the
    semantic model. Ambiguous questions return a next step instead of silently
    choosing an unrelated metric.
    """
    from backend.services.quality_analysis import quality_analysis
    quality = quality_analysis(metadata, raw_tables, question)
    if quality is not None:
        return quality
    count_result = _count_distinct_question(metadata, raw_tables, question)
    if count_result is not None:
        return count_result
    aggregate_result = _answer_aggregate_question(metadata, raw_tables, question)
    if aggregate_result is not None:
        return aggregate_result
    distinct_result = _answer_distinct_values_question(metadata, raw_tables, question)
    if distinct_result is not None:
        return distinct_result

    question_words = _words(question)
    if question_words & _SCHEMA_WORDS:
        summaries = [
            {"table": name, "fields": len(table.get("columns", [])), "measures": len(table.get("measures", []))}
            for name, table in metadata.get("tables", {}).items()
        ]
        requested_table = next((name for name in metadata.get("tables", {}) if _words(name) & question_words), None)
        if question_words & {"measure", "measures"}:
            if requested_table:
                measures = metadata["tables"][requested_table].get("measures", [])
                return {
                    "answer": f"Measures are aggregated or calculated numeric values used in visuals. {requested_table} has {len(measures)} measures.",
                    "question": question,
                    "method": "Read the validated uploaded-workbook schema.",
                    "sourceFields": [f"{requested_table}.{measure}" for measure in measures],
                    "findings": [{"measure": measure} for measure in measures],
                    "filters": [],
                }
            measures = [
                {"table": name, "measure": measure}
                for name, table in metadata.get("tables", {}).items()
                for measure in table.get("measures", [])
            ]
            return {
                "answer": f"Measures are aggregated or calculated numeric values used in visuals. The workbook has {len(measures)} available measures.",
                "question": question,
                "method": "Read the validated uploaded-workbook schema.",
                "sourceFields": [], "findings": measures, "filters": [],
            }
        if requested_table:
            table = metadata["tables"][requested_table]
            return {
                "answer": f"{requested_table} contains {len(table.get('columns', []))} fields and {len(table.get('measures', []))} measures.",
                "question": question,
                "method": "Read the validated uploaded-workbook schema.",
                "sourceFields": [f"{requested_table}.{field}" for field in table.get("columns", [])],
                "findings": [
                    {"field": field, "type": table.get("columnTypes", {}).get(field, "unknown")}
                    for field in table.get("columns", [])
                ],
                "filters": [],
            }
        return {
            "answer": f"The workbook has {len(summaries)} available tables.",
            "question": question,
            "method": "Read the validated uploaded-workbook schema.",
            "sourceFields": [], "findings": summaries, "filters": [],
        }

    candidates: list[dict[str, Any]] = []
    for table_name, table in metadata.get("tables", {}).items():
        rows = raw_tables.get(table_name, {}).get("rows", [])
        measures = table.get("measures", [])
        dimensions = _dimensions(table)
        if not rows or not measures or not dimensions:
            continue
        ranked_measures = _ranked_names(measures, question_words)
        ranked_dimensions = _ranked_names(dimensions, question_words)
        measure_score, measure = ranked_measures[0]
        dimension_score, dimension = ranked_dimensions[0]
        if measure_score == 0 and len(measures) != 1:
            continue
        candidates.append({
            "score": measure_score + dimension_score + _field_score(table_name, question_words),
            "tableName": table_name,
            "table": table,
            "rows": rows,
            "measure": measure,
            "dimension": dimension,
            "rankedMeasures": ranked_measures,
            "rankedDimensions": ranked_dimensions,
        })

    if not candidates:
        available = [
            f"{table_name}.{measure}"
            for table_name, table in metadata.get("tables", {}).items()
            for measure in table.get("measures", [])
        ]
        return {
            "answer": "I could not safely resolve that question to one available measure and dimension.",
            "question": question,
            "method": "No calculation was run.",
            "sourceFields": [], "findings": [], "filters": [],
            "nextStep": "Name the measure and grouping field you want to analyze. Available measures: "
                        + (", ".join(available[:MAX_PREVIEW_POINTS]) if available else "none"),
        }

    selected = max(candidates, key=lambda candidate: candidate["score"])
    table_name = selected["tableName"]
    table = selected["table"]
    rows = selected["rows"]
    measure = selected["measure"]
    dimension = selected["dimension"]
    ranked_measures = selected["rankedMeasures"]
    ranked_dimensions = selected["rankedDimensions"]

    if question_words & (_HIGH_WORDS | _LOW_WORDS):
        measure_score = ranked_measures[0][0]
        measure_matches = [name for score, name in ranked_measures if score == measure_score and score > 0]
        dimension_matches = [
            name for score, name in ranked_dimensions
            if score == dimension_score and score > 0
        ]
        measure_is_ambiguous = (
            measure_score == 0
            or _field_match_coverage(measure, question_words) <= 0.5
            or len(measure_matches) > 1
        )
        dimension_is_ambiguous = (
            _field_match_coverage(dimension, question_words) < 1.0
            or len(dimension_matches) > 1
        )
        if measure_is_ambiguous or dimension_is_ambiguous:
            requested_fields = []
            if measure_is_ambiguous:
                options = [name for score, name in ranked_measures if score > 0]
                requested_fields.append(
                    "metric: " + (", ".join(options[:MAX_PREVIEW_POINTS]) if options else "name the measure")
                )
            if dimension_is_ambiguous:
                options = [name for score, name in ranked_dimensions if score > 0]
                requested_fields.append(
                    "grouping: " + (", ".join(options[:MAX_PREVIEW_POINTS]) if options else "name the dimension")
                )
            return {
                "answer": "I couldn't safely identify a unique measure and grouping field for that ranking.",
                "question": question,
                "method": "No ranking was calculated because the available field names did not match the request clearly enough.",
                "sourceFields": [],
                "findings": [],
                "filters": [],
                "nextStep": "Please specify the " + " and ".join(requested_fields) + ".",
            }

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get(dimension) is not None:
            groups[str(row[dimension])].append(row)
    if not groups:
        return {
            "answer": f"{dimension} has no populated values to group.",
            "question": question,
            "method": "No calculation was run.",
            "sourceFields": [f"{table_name}.{dimension}"], "findings": [], "filters": [],
            "nextStep": "Choose another grouping field.",
        }

    comparison_requested = bool(question_words & _COMPARISON_WORDS)
    matched_measures = [name for score, name in ranked_measures if score > 0]
    if comparison_requested and len(matched_measures) >= 2:
        first, second = matched_measures[:2]
        findings = []
        for category, grouped_rows in groups.items():
            first_value = _aggregate_measure(table, first, grouped_rows)
            second_value = _aggregate_measure(table, second, grouped_rows)
            findings.append({
                "category": category,
                "firstMeasure": first,
                "firstValue": first_value,
                "secondMeasure": second,
                "secondValue": second_value,
                "difference": first_value - second_value,
            })
        findings.sort(key=lambda item: abs(item["difference"]), reverse=True)
        overall_difference = _aggregate_measure(table, first, rows) - _aggregate_measure(table, second, rows)
        return {
            "answer": f"{first} minus {second} is {overall_difference:,.2f} overall.",
            "question": question,
            "method": f"Applied each model-defined aggregation by {dimension}, then calculated the difference.",
            "sourceFields": [f"{table_name}.{dimension}", f"{table_name}.{first}", f"{table_name}.{second}"],
            "findings": findings[:MAX_PREVIEW_POINTS], "filters": [],
        }

    values = [
        {"category": category, "value": _aggregate_measure(table, measure, grouped_rows)}
        for category, grouped_rows in groups.items()
    ]
    is_trend = bool(question_words & {"trend", "over", "timeline", "change"}) and bool(_words(dimension) & _TIME_WORDS)
    if is_trend:
        def chronological(item: dict[str, Any]) -> tuple[int, float | str]:
            numeric = _numeric(item["category"])
            return (0, numeric) if numeric is not None else (1, item["category"].casefold())

        values.sort(key=chronological)
        return {
            "answer": f"Calculated {measure} over {dimension}; see the ordered findings for the trend.",
            "question": question,
            "method": f"Applied the model-defined aggregation for {measure}, grouped by {dimension}.",
            "sourceFields": [f"{table_name}.{dimension}", f"{table_name}.{measure}"],
            "findings": values[:MAX_PREVIEW_POINTS], "filters": [],
        }

    descending = not bool(question_words & _LOW_WORDS)
    values.sort(key=lambda item: item["value"], reverse=descending)
    count_match = re.search(r"\b(?:top|bottom|first|last)\s+(\d+)\b", question.casefold())
    requested_count = int(count_match.group(1)) if count_match else min(5, MAX_PREVIEW_POINTS)
    requested_count = max(1, min(requested_count, MAX_RANKING_COUNT, MAX_PREVIEW_POINTS))
    selected_value = values[0]
    direction = "highest" if descending else "lowest"
    return {
        "answer": f"{selected_value['category']} is {direction} for {measure} at {selected_value['value']:,.2f}.",
        "question": question,
        "method": f"Applied the model-defined aggregation for {measure} by {dimension}, then ranked the results.",
        "sourceFields": [f"{table_name}.{dimension}", f"{table_name}.{measure}"],
        "findings": values[:requested_count], "filters": [],
    }
