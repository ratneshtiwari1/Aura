from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from automation.ai.ai_service import AIServiceError, get_ai_provider
from backend.config import get_settings
from backend.policy import MAX_PREVIEW_POINTS, MAX_RANKING_COUNT
from backend.services.analysis_service import (
    _aggregate_filters,
    _aggregate_measure,
    _date_value,
)


_ANALYSIS_INTENTS = {
    "availability", "aggregate", "count_distinct", "ranking", "trend", "distinct_values",
    "schema", "quality", "other", "analytical_query",
}

_AVAILABILITY_WORDS = re.compile(
    r"\b(?:does|do|is|are|can)\b.{0,80}\b(?:have|has|include|contain|available|show|see)\b",
    re.IGNORECASE,
)
_MATCH_STOP_WORDS = {
    "a", "an", "and", "are", "available", "by", "can", "contain", "data", "do", "does",
    "for", "from", "has", "have", "i", "include", "in", "is", "monthly", "of", "see",
    "show", "the", "this", "to", "with", "workbook",
}


def _schema_availability_fallback(
    metadata: dict[str, Any], question: str, selected_table: str | None,
) -> dict[str, Any] | None:
    """Resolve an unambiguous availability question without asking the user to inspect schema."""
    if not isinstance(selected_table, str) or selected_table not in metadata.get("tables", {}):
        return None
    if not _AVAILABILITY_WORDS.search(question):
        return None
    table = metadata["tables"][selected_table]
    question_words = {
        word for word in re.findall(r"[a-z0-9]+", question.casefold())
        if word not in _MATCH_STOP_WORDS
    }

    def best_match(names: list[str]) -> str | None:
        scored = []
        for name in names:
            score = len(question_words & set(re.findall(r"[a-z0-9]+", name.casefold())))
            if score:
                scored.append((score, name))
        if not scored:
            return names[0] if len(names) == 1 else None
        top_score = max(score for score, _ in scored)
        matches = [name for score, name in scored if score == top_score]
        return matches[0] if len(matches) == 1 else None

    measure = best_match(list(table.get("measures", [])))
    columns = list(table.get("columns", []))
    requests_time = bool(re.search(r"\b(?:day|week|month|quarter|year|date|time)\b", question, re.IGNORECASE))
    time_field = None
    if requests_time:
        time_candidates = [
            name for name in columns
            if re.search(r"\b(?:day|week|month|quarter|year|date|time)\b", name, re.IGNORECASE)
            or str(table.get("columnRoles", {}).get(name, "")).casefold() == "time"
            or str(table.get("columnTypes", {}).get(name, "")).casefold() in {"date", "datetime"}
        ]
        time_field = best_match(time_candidates)
        if time_field is None:
            return None
    field = best_match(columns) if measure is None and time_field is None else None
    if not any((measure, time_field, field)):
        return None
    return {
        "status": "ready", "intent": "availability", "table": selected_table,
        "measure": measure, "time_field": time_field, "time_grain": None,
        "group_by": [], "field": field, "direction": "descending", "limit": 5,
        "normalized_question": question,
    }


def _format_result_value(value: int | float) -> str:
    formatted = format(Decimal(str(value)), ',f')
    whole, dot, fraction = formatted.partition('.')
    return whole + '.' + (fraction if dot else '').ljust(2, '0')


def _time_period(value: object, grain: str) -> str | None:
    # Forecast and budget workbooks often store a calendar year as an integer
    # rather than a full date.  It is still a valid time axis when the schema
    # declares it as such.
    if grain == "year" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value))
    parsed = _date_value(value)
    if parsed is None:
        # A validated Quarter/Period column can already contain labels such as
        # Q1 or 2025-Q1 rather than a full calendar date.
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None
    if grain == "year":
        return f"{parsed.year:04d}"
    if grain == "quarter":
        quarter = (parsed.month - 1) // 3 + 1
        return f"{parsed.year:04d}-Q{quarter}"
    if grain == "month":
        return f"{parsed.year:04d}-{parsed.month:02d}"
    return parsed.isoformat()


def _comparison_answer(metadata: dict[str, Any], raw_tables: dict[str, Any], question: str) -> dict[str, Any] | None:
    """Calculate an explicit same-table ``A versus B by dimension`` request.

    The ordinary aggregate plan intentionally has one measure.  Comparisons
    are different: asking for Actual versus Plan is an unambiguous request for
    two named model measures, not a reason to ask the user to choose one.
    """
    import re
    match = re.search(r"\b(.+?)\s+(?:versus|vs\.?|compared\s+with)\s+(.+?)\s+by\s+(.+?)(?:[?.!]|$)", question, re.I)
    if not match:
        return None
    left, right, grouping_phrase = (value.strip() for value in match.groups())

    def words(value: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", value.casefold())) - {"total", "average", "the", "actual", "plan"}

    # Keep Actual/Plan as distinguishing words even though both appear in
    # common business phrasing.
    ignored = {"total", "the", "what", "is", "are", "show", "me", "give", "of", "for"}
    left_words = set(re.findall(r"[a-z0-9]+", left.casefold())) - ignored
    right_words = set(re.findall(r"[a-z0-9]+", right.casefold())) - ignored
    group_words = words(grouping_phrase)
    candidates = []
    for table_name, table in metadata.get("tables", {}).items():
        measures = table.get("measures", [])
        left_matches = [measure for measure in measures if left_words <= set(re.findall(r"[a-z0-9]+", measure.casefold()))]
        right_matches = [measure for measure in measures if right_words <= set(re.findall(r"[a-z0-9]+", measure.casefold()))]
        grouping_matches = [column for column in table.get("columns", []) if group_words <= words(column)]
        if len(left_matches) == len(right_matches) == len(grouping_matches) == 1:
            candidates.append((table_name, left_matches[0], right_matches[0], grouping_matches[0]))
    if len(candidates) != 1:
        return None
    table_name, left_measure, right_measure, group = candidates[0]
    table = metadata["tables"][table_name]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in raw_tables.get(table_name, {}).get("rows", []):
        if row.get(group) is not None:
            grouped.setdefault(str(row[group]), []).append(row)
    findings = [
        {"category": category, "group": {group: category}, left_measure: _aggregate_measure(table, left_measure, rows), right_measure: _aggregate_measure(table, right_measure, rows)}
        for category, rows in grouped.items()
    ]
    if not findings:
        return None
    answer_lines = [f"{left_measure} versus {right_measure} by {group}:"]
    answer_lines.extend(
        f"- {item['category']}: {_format_result_value(item[left_measure])} vs {_format_result_value(item[right_measure])}"
        for item in findings
    )
    return {"answer": "\n".join(answer_lines), "question": question,
            "method": f"Applied the model-defined aggregation for {left_measure} and {right_measure}, grouped by {group}.",
            "sourceFields": [f"{table_name}.{left_measure}", f"{table_name}.{right_measure}", f"{table_name}.{group}"],
            "findings": findings, "filters": [],
            "analysisPlan": {"intent": "comparison", "table": table_name, "measures": [left_measure, right_measure], "groupBy": [group]}}


class AnalysisClarificationRequired(Exception):
    def __init__(
        self,
        question: str,
        options: list[str] | None = None,
        *,
        provider: str,
        model: str,
    ) -> None:
        super().__init__(question)
        self.question = question
        self.options = options or []
        self.provider = provider
        self.model = model

    def response(self, question: str) -> dict[str, Any]:
        return {
            "answer": self.question,
            "question": question,
            "method": "AI could not determine a safe analysis plan; no calculation was run.",
            "sourceFields": [],
            "findings": [],
            "filters": [],
            "analysisPlan": {
                "status": "needs_clarification",
                "clarificationOptions": self.options,
                "provider": self.provider,
                "model": self.model,
            },
        }


def _analysis_schema(metadata: dict[str, Any]) -> dict[str, Any]:
    tables = {}
    for name, table in metadata.get("tables", {}).items():
        tables[name] = {
            "columns": list(table.get("columns", [])),
            "column_types": table.get("columnTypes", {}),
            "column_roles": table.get("columnRoles", {}),
            "column_profiles": {
                column: {
                    key: profile[key]
                    for key in ("distinct_count", "non_null_count")
                    if key in profile
                }
                for column, profile in table.get("columnProfiles", {}).items()
            },
            "measures": list(table.get("measures", [])),
            "measure_definitions": {
                measure: {
                    key: definition[key]
                    for key in ("column", "aggregation", "filterColumn", "filterValue")
                    if key in definition
                }
                for measure, definition in table.get("measureDefinitions", {}).items()
                if isinstance(definition, dict)
            },
        }
    return {"tables": tables, "relationships":metadata.get("relationships",[])}


async def analyze_data_question(
    metadata: dict[str, Any],
    raw_tables: dict[str, Any],
    question: str,
    *,
    selected_table: str | None = None,
    clarification_answer: str | None = None,
    conversation: list[dict[str, str]] | None = None,
    clarification_history: list[dict[str, str]] | None = None,
    repair_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Require an AI interpretation, then calculate only against validated source fields."""
    settings = get_settings()
    provider = get_ai_provider(settings)
    comparison = _comparison_answer(metadata, raw_tables, question)
    if comparison is not None:
        return comparison
    schema = _analysis_schema(metadata)
    raw_plan = await provider.plan_data_question(
        question,
        schema,
        {
            "selected_table": selected_table,
            "clarification_answer": clarification_answer,
            "clarification_history": clarification_history or [],
            "repair_context": repair_context,
            "conversation": (conversation or [])[-10:],
        },
    )
    if not isinstance(raw_plan, dict):
        raise AIServiceError("AI returned an invalid data-analysis plan. Please retry.")
    if raw_plan.get("status") == "needs_clarification":
        fallback_plan = _schema_availability_fallback(metadata, question, selected_table)
        if fallback_plan is not None:
            raw_plan = fallback_plan
        else:
            clarification = raw_plan.get("question")
            options = raw_plan.get("options", [])
            if not isinstance(clarification, str) or not clarification.strip():
                raise AIServiceError("AI returned an invalid clarification request. Please retry.")
            if not isinstance(options, list) or any(not isinstance(option, str) for option in options):
                raise AIServiceError("AI returned invalid clarification options. Please retry.")
            raise AnalysisClarificationRequired(
                clarification.strip(),
                list(dict.fromkeys(option.strip() for option in options if option.strip())),
                provider=settings.ai_provider,
                model=provider.model,
            )
    if raw_plan.get("status") == "ready" and raw_plan.get("intent") == "analytical_query":
        import asyncio
        from backend.services.analytical_query import execute_query
        if not isinstance(raw_plan.get("explanation"),str) or not raw_plan["explanation"].strip():
            raise AIServiceError("The analysis plan must explain its calculation and assumptions.")
        try:
            result = await asyncio.to_thread(execute_query,raw_plan.get("sql"),metadata,raw_tables)
        except ValueError as exc:
            raise ValueError(f"Generated analytical query failed validation: {exc}") from exc
        return {"answer":"The requested analysis was calculated; see the result rows and method.",
                "question":question,"method":"Executed the validated read-only DuckDB query over uploaded source rows; no generated Python was executed.",
                "sourceFields":[t+".*" for t in result["sourceTables"]],"findings":result["rows"],"filters":[],
                "analysisPlan":{"intent":"analytical_query","query":result["query"],"planExplanation":raw_plan["explanation"],"provider":settings.ai_provider,"model":provider.model},
                "coverage":{"returnedRows":len(result["rows"]),"truncated":result["truncated"],"rowLimit":result["rowLimit"]},
                "resultColumns":result["columns"]}
    if raw_plan.get("status") != "ready":
        raise AIServiceError("AI returned an unsupported data-analysis plan. Please retry.")

    intent = raw_plan.get("intent")
    table_name = raw_plan.get("table")
    normalized_question = raw_plan.get("normalized_question")
    measure = raw_plan.get("measure")
    time_field = raw_plan.get("time_field")
    time_grain = raw_plan.get("time_grain")
    group_by = raw_plan.get("group_by", [])
    field = raw_plan.get("field")
    direction = raw_plan.get("direction")
    limit = raw_plan.get("limit")
    if not isinstance(intent, str) or intent not in _ANALYSIS_INTENTS:
        raise AIServiceError("AI returned an unsupported analysis intent. Please retry.")
    if not isinstance(table_name, str) or table_name not in metadata.get("tables", {}):
        raise AIServiceError("AI selected a table that is not in the current data source.")
    if not isinstance(normalized_question, str) or not normalized_question.strip():
        raise AIServiceError("AI did not provide an executable analysis question. Please retry.")
    normalized_question = normalized_question.strip()
    table = metadata["tables"][table_name]
    # Availability is a schema check, not a time-series calculation. Models
    # sometimes return conversational grains such as "monthly"; ignore them
    # rather than exposing an internal validation error to the user.
    if intent == "availability":
        time_grain = None
    if measure is not None and (
        not isinstance(measure, str) or measure not in table.get("measures", [])
    ):
        available_measures = list(table.get("measures", []))
        question_text = (
            f"I couldn't match '{measure}' to an executable measure in {table_name}. "
            "Which available measure should I use?"
            if available_measures else
            f"I couldn't match '{measure}' to an executable measure in {table_name}. "
            "Could you clarify the calculation?"
        )
        raise AnalysisClarificationRequired(
            question_text,
            available_measures,
            provider=settings.ai_provider,
            model=provider.model,
        )
    time_field_type = str(table.get("columnTypes", {}).get(time_field, "")).casefold() if isinstance(time_field, str) else ""
    time_field_role = str(table.get("columnRoles", {}).get(time_field, "")).casefold() if isinstance(time_field, str) else ""
    is_year_dimension = isinstance(time_field, str) and (
        time_field_role == "time"
        or (time_field.casefold() in {"year", "fiscal year", "calendar year"} and time_field_type in {"integer", "int", "int64", "decimal", "number"})
    )
    is_named_period_dimension = isinstance(time_field, str) and bool(
        re.search(r"\b(?:day|week|month|quarter|period|date|time)\b", time_field, re.IGNORECASE)
    )
    if time_field is not None and (
        not isinstance(time_field, str)
        or time_field not in table.get("columns", [])
        or (time_field_type not in {"date", "datetime"} and not is_year_dimension and not is_named_period_dimension)
    ):
        raise AIServiceError("AI selected a time field that is not available in the chosen table.")
    if not isinstance(group_by, list) or any(
        not isinstance(field, str) or field not in table.get("columns", [])
        for field in group_by
    ):
        raise AIServiceError("AI selected a grouping field that is not available in the chosen table.")
    if len(set(group_by)) != len(group_by):
        raise AIServiceError("AI returned duplicate grouping fields.")
    if field is not None and (not isinstance(field, str) or field not in table.get("columns", [])):
        raise AIServiceError("AI selected a field that is not available in the chosen table.")
    if time_grain is not None and (
        not isinstance(time_grain, str)
        or time_grain not in {"day", "month", "quarter", "year"}
        or not time_field
    ):
        raise AIServiceError("AI returned an invalid time grouping grain.")
    if intent == "ranking":
        if not isinstance(direction, str) or direction not in {"ascending", "descending"}:
            raise AIServiceError("AI returned an invalid ranking direction.")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise AIServiceError("AI returned an invalid result limit.")
    if intent == "other":
        raise AnalysisClarificationRequired(
            "I couldn't determine what result you want. Could you clarify?",
            provider=settings.ai_provider,
            model=provider.model,
        )

    if intent == "availability":
        matched_fields = list(dict.fromkeys(
            [name for name in [measure, field, time_field, *group_by] if name]
        ))
        if not matched_fields:
            raise AnalysisClarificationRequired(
                "Which measure or field should I check for in the data?",
                list(table.get("measures", [])) + list(table.get("columns", [])),
                provider=settings.ai_provider,
                model=provider.model,
            )
        labelled_fields = [
            f"measure '{name}'" if name == measure else f"field '{name}'"
            for name in matched_fields
        ]
        if len(labelled_fields) == 1:
            listed_fields = labelled_fields[0]
        elif len(labelled_fields) == 2:
            listed_fields = " and ".join(labelled_fields)
        else:
            listed_fields = ", ".join(labelled_fields[:-1]) + ", and " + labelled_fields[-1]
        return {
            "answer": f"Yes. {table_name} contains {listed_fields} that support this question.",
            "question": question,
            "method": "Checked the validated uploaded-workbook schema; no values were calculated.",
            "sourceFields": [f"{table_name}.{name}" for name in matched_fields],
            "findings": [],
            "filters": [],
            "analysisPlan": {
                "intent": intent, "table": table_name, "measure": measure,
                "timeField": time_field, "timeGrain": time_grain, "groupBy": group_by,
                "field": field, "direction": direction, "limit": limit,
                "provider": settings.ai_provider, "model": provider.model,
            },
        }

    if intent in {"aggregate", "ranking", "trend"} and not measure:
        available_measures = list(table.get("measures", []))
        raise AnalysisClarificationRequired(
            "Which measure should I use for this calculation?",
            available_measures,
            provider=settings.ai_provider,
            model=provider.model,
        )
    if intent == "count_distinct" and not field:
        raise AIServiceError("AI did not identify a field to count distinct values for.")
    if intent == "ranking" and not group_by:
        raise AIServiceError("AI must identify a grouping field for this calculation.")
    if intent == "ranking" and len(group_by) != 1:
        raise AIServiceError("AI must identify exactly one grouping field for a ranking.")
    if intent == "trend" and not time_field:
        raise AIServiceError("AI did not identify a date field for the requested trend.")
    if intent == "distinct_values" and not field:
        raise AIServiceError("AI did not identify a field for the requested distinct values.")
    if intent == "ranking" and limit > MAX_RANKING_COUNT:
        limit = MAX_RANKING_COUNT

    # A distinct count of Year must not become a year-by-year trend: that
    # would count each Year value within its own group and always return 1.
    # Preserve an explicitly requested breakdown by a different field.
    if intent == "count_distinct":
        group_by = [name for name in group_by if name != field]
        if time_field == field:
            time_field = None
            time_grain = None

    execution_table = {**table, "preferredTimeField": time_field}
    scoped_rows = raw_tables.get(table_name, {}).get("rows", [])
    if intent in {"aggregate", "count_distinct", "ranking", "trend"}:
        filter_result = _aggregate_filters(execution_table, scoped_rows, normalized_question)
        if isinstance(filter_result, str):
            raise AnalysisClarificationRequired(
                filter_result,
                provider=settings.ai_provider,
                model=provider.model,
            )
        filters, filtered_rows = filter_result
        if intent == "ranking":
            grouping_fields = group_by
        elif intent == "trend":
            grouping_fields = [time_field, *group_by]
        elif time_grain:
            grouping_fields = [time_field, *group_by]
        else:
            grouping_fields = group_by
        if grouping_fields:
            grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
            for row in filtered_rows:
                values = tuple(
                    (
                        _time_period(row[name], time_grain)
                        if name == time_field and time_grain else str(row[name])
                    )
                    for name in grouping_fields
                    if row.get(name) is not None
                )
                if len(values) == len(grouping_fields) and all(value is not None for value in values):
                    grouped.setdefault(values, []).append(row)
            if intent == "count_distinct":
                findings = [
                    {
                        "category": " / ".join(key),
                        "group": dict(zip(grouping_fields, key)),
                        "value": len({row[field] for row in rows if row.get(field) is not None}),
                    }
                    for key, rows in grouped.items()
                ]
            else:
                findings = [
                    {
                        "category": " / ".join(key),
                        "group": dict(zip(grouping_fields, key)),
                        "value": _aggregate_measure(execution_table, measure, rows),
                    }
                    for key, rows in grouped.items()
                ]
        else:
            if intent == "count_distinct":
                findings = [{"field": field, "distinctCount": len({
                    row[field] for row in filtered_rows if row.get(field) is not None
                })}]
            else:
                findings = [{"measure": measure, "value": _aggregate_measure(execution_table, measure, filtered_rows)}]
        if intent == "ranking":
            findings.sort(key=lambda item: item["value"], reverse=direction == "descending")
            findings = findings[:min(limit, MAX_PREVIEW_POINTS)]
        elif intent == "trend":
            findings.sort(key=lambda item: (
                _date_value(item["group"][time_field]) is None,
                _date_value(item["group"][time_field]) or item["category"],
            ))
            findings = findings[:MAX_PREVIEW_POINTS]
        grouping_description = ", ".join(
            f"{name} ({time_grain})" if name == time_field and time_grain else name
            for name in grouping_fields
        )
        filter_description = " and ".join(f"{item['field']} = {item['value']}" for item in filters)
        if intent == "ranking" and not findings:
            answer = f"{grouping_description} has no populated values to rank."
            method = "No ranking was calculated because the selected grouping field has no populated values."
        elif intent == "ranking":
            direction_label = "highest" if direction == "descending" else "lowest"
            answer = f"{findings[0]['category']} is {direction_label} for {measure} at {_format_result_value(findings[0]['value'])}."
            method = f"Applied the model-defined aggregation for {measure} by {grouping_description}, then ranked the results."
        elif intent == "trend" and findings:
            answer = f"Calculated {measure} over {grouping_description}; see the ordered findings for the trend."
            method = f"Applied the model-defined aggregation for {measure}, grouped by {grouping_description}."
        elif intent == "count_distinct" and findings and grouping_fields:
            answer = f"Counted distinct {field} values by {grouping_description}; see the grouped findings."
            method = f"Counted distinct {field} values within each {grouping_description} group."
        elif intent == "count_distinct":
            answer = f"There are {findings[0]['distinctCount']} distinct {field} values."
            method = f"Counted distinct non-empty values in {field}."
        elif intent == "trend":
            answer = f"{grouping_description} has no populated values to trend."
            method = "No trend was calculated because the selected time field has no populated values."
        elif grouping_fields and not findings:
            answer = f"No populated rows were available for {grouping_description}."
            method = "No grouped calculation was returned because the selected fields have no populated values."
        elif grouping_fields:
            shown = findings[:MAX_PREVIEW_POINTS]
            result_lines = [f"{item['category']}: {_format_result_value(item['value'])}" for item in shown]
            answer = f"{measure} by {grouping_description}:\n- " + "\n- ".join(result_lines)
            method = f"Applied the model-defined aggregation for {measure}, grouped by {grouping_description}."
        else:
            answer = f"{measure}: {_format_result_value(findings[0]['value'])}"
            method = (
                f"Applied the model-defined aggregation for {measure} after applying {filter_description}."
                if filters else f"Applied the model-defined aggregation for {measure} across all available rows."
            )
        source_fields = (
            [f"{table_name}.{field}"] if intent == "count_distinct"
            else [f"{table_name}.{measure}"]
        )
        source_fields.extend(f"{table_name}.{name}" for name in grouping_fields)
        source_fields.extend(f"{table_name}.{item['field']}" for item in filters)
        return {
            "answer": answer,
            "question": question,
            "method": method,
            "sourceFields": list(dict.fromkeys(source_fields)),
            "findings": findings,
            "filters": filters,
            "analysisPlan": {
                "intent": intent, "table": table_name, "measure": measure,
                "timeField": time_field, "timeGrain": time_grain, "groupBy": group_by,
                "field": field,
                "direction": direction, "limit": limit,
                "provider": settings.ai_provider, "model": provider.model,
            },
        }

    if intent == "distinct_values":
        values = list(dict.fromkeys(
            row.get(field) for row in scoped_rows if row.get(field) is not None
        ))
        values.sort(key=lambda value: str(value).casefold())
        shown = values[:MAX_PREVIEW_POINTS]
        return {
            "answer": f"{field} has {len(values)} distinct values in {table_name}: {', '.join(map(str, shown))}"
            + ("." if len(values) <= MAX_PREVIEW_POINTS else f", and {len(values) - MAX_PREVIEW_POINTS} more."),
            "question": question,
            "method": f"Listed distinct non-empty values from {field}.",
            "sourceFields": [f"{table_name}.{field}"],
            "findings": [{"field": field, "distinctCount": len(values), "values": ", ".join(map(str, shown))}],
            "filters": [],
        }
    if intent == "schema":
        return {
            "answer": f"{table_name} contains {len(table.get('columns', []))} fields and {len(table.get('measures', []))} measures.",
            "question": question,
            "method": "Read the validated uploaded-workbook schema.",
            "sourceFields": [f"{table_name}.{name}" for name in table.get("columns", [])],
            "findings": [
                {"field": name, "type": table.get("columnTypes", {}).get(name, "unknown")}
                for name in table.get("columns", [])
            ],
            "filters": [],
        }
    if intent == "quality":
        from backend.services.quality_analysis import quality_analysis

        result = quality_analysis(metadata, raw_tables, normalized_question)
        if result is None:
            raise AnalysisClarificationRequired(
                "Which data-quality check and fields should I examine? You can request missing values, duplicates or an explicit analytical check.",
                provider=settings.ai_provider,model=provider.model)
    else:
        raise AIServiceError("AI returned an unsupported data-analysis intent.")
    result["analysisPlan"] = {
        "intent": intent,
        "table": table_name,
        "measure": measure,
        "timeField": time_field,
        "groupBy": group_by,
        "provider": settings.ai_provider,
        "model": provider.model,
    }
    return result
