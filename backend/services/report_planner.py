import re
import json
from typing import Any

from backend.services.comparison_plan import paired_comparison
from automation.policy import MAX_VISUALS_PER_PAGE
from automation.visual_registry import CATEGORY_VALUE_TYPES
from automation.report_automation import (
    _requested_visual_count, _split_visual_instructions, create_report_spec, requested_visual_count_mode, validate_report_spec,
)


_NON_ADDITIVE_WORDS = {"average", "avg", "margin", "percent", "percentage", "pct", "rate", "ratio", "share"}
_EXPLICIT_VISUAL_REQUEST = re.compile(
    r"\b(?:kpi|cards?|slicers?|filters?|gauges?|tables?|matrices?|charts?|graphs?)\b",
    re.IGNORECASE,
)

class UnsupportedReportFeature(ValueError):
    """A requested report feature cannot be represented in generated projects."""


def _requests_feature(request: str, pattern: str) -> bool:
    for match in re.finditer(pattern, request, re.IGNORECASE):
        prefix = request[max(0, match.start() - 200):match.start()]
        if not re.search(
            r"\b(?:no|without|omit|exclude|remove|do not (?:add|include|use))\b"
            r"(?:(?!\b(?:but|instead|add|include|create)\b)[^.;\n])*$",
            prefix, re.IGNORECASE,
        ):
            return True
    return False


def requests_page_navigation(request: str) -> bool:
    return _requests_feature(request, r"\b(?:page\s+navigat(?:ion|ors?)|navigat(?:ion|or)\s+buttons?|buttons?\s+for\s+(?:page\s+)?navigation)\b")


def requested_synced_slicer_fields(request: str) -> list[str]:
    match = re.search(r"\b(?:synchronized|synchronised|sync|linked)\s+([^.;\n]{1,100}?)\s+slicers?\b", request, re.IGNORECASE)
    if not match or not _requests_feature(request, re.escape(match.group(0))):
        return []
    return [name.strip() for name in re.split(r"\s*,\s*|\s+and\s+", match.group(1), flags=re.IGNORECASE) if name.strip()]


def requests_synced_slicers(request: str) -> bool:
    return _requests_feature(request, r"\b(?:sync(?:hroni[sz](?:e[ds]?|ing|ation))?|linked)\b[^.;\n]{0,80}\bslicers?\b|\bslicers?\b[^.;\n]{0,80}\b(?:sync(?:hroni[sz](?:e[ds]?|ing|ation))?|linked)\b")


def validate_requested_report_features(request: str) -> None:
    # Report interactions are represented in the report specification and
    # rendered by the PBIR writer.  Keep this hook for genuinely unsupported
    # requests, rather than rejecting native Power BI features up front.
    return None


def _words(value: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", value.casefold())
    return [word[:-3] + "y" if word.endswith("ies") else word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word for word in words]


def _semantic_score(name: str, request: str) -> float:
    name_words = [word for word in _words(name) if word not in {"total", "average", "avg"}]
    request_words = set(_words(request))
    if not name_words:
        return 0.0
    overlap = len(set(name_words) & request_words)
    score = overlap / len(set(name_words))
    compact_name = " ".join(name_words)
    compact_request = " ".join(_words(request))
    if compact_name and compact_name in compact_request:
        score += 1.0
    return score


def _measure_column(table: dict[str, Any], measure: str) -> str:
    definition = table.get("measureDefinitions", {}).get(measure)
    if isinstance(definition, dict) and isinstance(definition.get("column"), str):
        return definition["column"]
    return re.sub(r"^(?:Total|Average)\s+", "", measure, flags=re.IGNORECASE)


def _schema_driven_plan(request: str, metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Recover a conservative plan from schema semantics when parsing fails.

    The recovery never assumes business-domain field names. It requires the
    request to match existing measures and chooses dimensions using declared
    roles, types, profiles, and numeric ranges. Ambiguous requests still fail
    and are returned to the user for clarification.
    """
    request_years = [int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", request)]
    table_candidates: list[tuple[float, str, list[tuple[float, str, str]]]] = []
    for table_name, table in metadata.get("tables", {}).items():
        best_by_column: dict[str, tuple[float, str, str]] = {}
        for measure in table.get("measures", []):
            column = _measure_column(table, str(measure))
            score = max(_semantic_score(str(measure), request), _semantic_score(column, request))
            if score <= 0:
                continue
            definition = table.get("measureDefinitions", {}).get(measure, {})
            aggregation = str(definition.get("aggregation", "")).upper() if isinstance(definition, dict) else ""
            column_format = str(table.get("columnFormats", {}).get(column) or "")
            column_words = set(_words(column))
            non_additive = "%" in column_format or bool(column_words & _NON_ADDITIVE_WORDS)
            preference = 0.2 if non_additive and aggregation == "AVERAGE" else 0.0
            candidate = (score + preference, str(measure), column)
            if candidate[0] > best_by_column.get(column, (0.0, "", ""))[0]:
                best_by_column[column] = candidate
        ranked = sorted(best_by_column.values(), reverse=True)
        if ranked:
            best_score = ranked[0][0]
            selected = [item for item in ranked if item[0] >= max(0.24, best_score * 0.20)][:4]
            table_score = sum(item[0] for item in selected)
            if table_name == metadata.get("preferredTable"):
                table_score += 0.05
            table_candidates.append((table_score, table_name, selected))
    if not table_candidates:
        return None
    table_score, table_name, selected_measures = max(table_candidates, key=lambda item: item[0])
    if table_score <= 0 or not selected_measures:
        return None
    table = metadata["tables"][table_name]

    time_candidates: list[tuple[float, str]] = []
    category_candidates: list[tuple[float, str]] = []
    for column in table.get("columns", []):
        column = str(column)
        role = str(table.get("columnRoles", {}).get(column, "")).casefold()
        data_type = str(table.get("columnTypes", {}).get(column, "")).casefold()
        profile = table.get("columnProfiles", {}).get(column, {})
        score = _semantic_score(column, request)
        is_time = role == "time" or data_type in {"date", "datetime"}
        if request_years and data_type in {"integer", "decimal", "number", "int64"}:
            minimum, maximum = profile.get("min"), profile.get("max")
            if isinstance(minimum, (int, float)) and isinstance(maximum, (int, float)) and min(request_years) >= minimum and max(request_years) <= maximum:
                is_time = True
                score += 1.0
        if is_time:
            time_candidates.append((score + (0.5 if role == "time" else 0.0), column))
        elif role in {"category", "dimension", "geographic"} or data_type in {"string", "text", "boolean"}:
            category_candidates.append((score, column))

    time_field = max(time_candidates, default=(0.0, ""))[1]
    ranked_categories = sorted(category_candidates, reverse=True)
    named_category = next((field for score, field in ranked_categories if score > 0), "")
    category_field = time_field or named_category
    if not category_field:
        return None

    visuals: list[dict[str, Any]] = []
    for _, measure, column in selected_measures:
        visual: dict[str, Any] = {
            "type": "lineChart" if time_field else "clusteredColumnChart",
            "title": column,
            "category": {"table": table_name, "field": category_field, "fieldType": "column"},
            "value": {"table": table_name, "field": measure, "fieldType": "measure"},
        }
        if time_field and named_category and named_category != time_field:
            visual["legend"] = {"table": table_name, "field": named_category, "fieldType": "column"}
        visuals.append(visual)

    dashboard_match = re.search(
        r"\b(?:create|build|make|generate)\s+(?:a\s+|an\s+|the\s+)?([a-z0-9 ]+?)\s+dashboard\b",
        request,
        flags=re.IGNORECASE,
    )
    page = f"{dashboard_match.group(1).strip().title()} Dashboard" if dashboard_match else "Generated Report"
    return {"page": page, "visuals": visuals}


def _requests_time_range(request: str) -> bool:
    return bool(re.search(
        r"\bfrom\s+(?:19|20)\d{2}\s+to\s+(?:19|20)\d{2}\b"
        r"|\bbetween\s+(?:19|20)\d{2}\s+and\s+(?:19|20)\d{2}\b"
        r"|\b(?:by\s+year|year\s+wise|over\s+time)\b",
        request,
        re.IGNORECASE,
    ))


def _parse_report_plan(
    request: str,
    metadata: dict[str, Any],
    clarification_answer: str | None = None,
) -> dict[str, Any]:
    if clarification_answer:
        match = re.fullmatch(r"\s*(?:Use\s+)?([^\[\]]+)\[([^\[\]]+)\]\s*", clarification_answer, re.I)
        if match:
            table, field = match.group(1).strip(), match.group(2).strip()
            table_metadata = metadata.get("tables", {}).get(table)
            if not table_metadata or field not in table_metadata.get("columns", []):
                raise ValueError("The selected field is not available in the current data source")
            metadata = {**metadata, "clarifiedField": (table, field)}
    try:
        direct = create_report_spec(request, metadata)
        if _requests_time_range(request):
            uses_time = any(
                str(metadata.get("tables", {}).get(category.get("table"), {}).get("columnRoles", {}).get(category.get("field"), "")).casefold() == "time"
                for visual in direct.get("visuals", [])
                if isinstance(visual, dict)
                for category in [visual.get("category")]
                if isinstance(category, dict)
            )
            if not uses_time:
                recovered = _schema_driven_plan(request, metadata)
                if recovered is not None:
                    return recovered
        return direct
    except ValueError as original_error:
        if _EXPLICIT_VISUAL_REQUEST.search(request):
            raise
        recovered = _schema_driven_plan(request, metadata)
        if recovered is not None:
            return recovered
        if "Could not resolve category" in str(original_error):
            category_match = re.search(r"\b(?:by|per|across)\s+", request, re.IGNORECASE)
            value_phrase = request[:category_match.start()].strip(" ,.;") if category_match else request.strip()
            measure_scores = [
                _semantic_score(str(measure), value_phrase)
                for table in metadata.get("tables", {}).values()
                for measure in table.get("measures", [])
            ]
            if not measure_scores or max(measure_scores) <= 0:
                raise ValueError(f"Could not resolve measure from request: {value_phrase}") from original_error
        raise


def _suggest_visuals(request: str, metadata: dict[str, Any], count: int) -> dict[str, Any] | None:
    # Only choose the contents ourselves when the user delegates that choice.
    match = re.fullmatch(
        r"\s*(?:please\s+)?(?:(?:create|draw|make|build|generate|show|plot|add|include|give me)\s+)?"
        r"(?:\d+|\w+)\s+(visuals?|charts?|graphs?)(?:\s+(?:for|of|about)\s+(.+?))?[.!]?\s*", request, re.I,
    )
    if not match:
        return None
    tables = metadata.get("tables", {})
    name = metadata.get("preferredTable") or (next(iter(tables)) if len(tables) == 1 else None)
    if name not in tables:
        raise ValueError("Choose the workbook table to use for these visuals.")
    table = tables[name]
    topic = (match.group(2) or "").strip().casefold()
    measures = [m for m in table.get("measures", []) if not topic or topic in {
        m.casefold(), _measure_column(table, m).casefold(), name.casefold()
    }]
    dims = [c for c in table.get("columns", []) if table.get("columnRoles", {}).get(c) in
            {"category", "dimension", "geographic", "time"} or
            (table.get("columnRoles", {}).get(c) not in {"identifier", "measure"} and
             table.get("columnTypes", {}).get(c) in {"string", "date", "datetime"})]
    visuals = []
    for m in measures:
        value = {"table": name, "field": m, "fieldType": "measure"}
        if match.group(1).lower().startswith("visual"):
            visuals.append({"type": "card", "title": m, "value": value})
        for c in dims:
            kind = "lineChart" if table.get("columnRoles", {}).get(c) == "time" else "clusteredColumnChart"
            visuals.append({"type": kind, "title": f"{m} by {c}", "value": value,
                            "category": {"table": name, "field": c, "fieldType": "column"}})
        if len(visuals) >= count:
            break
    if len(visuals) < count:
        raise ValueError(f"I can bind {len(visuals)} distinct visuals for this request in {name}. "
                         f"To create {count}, specify the measures and groupings or select another table.")
    return {"page": f"{name} Overview", "visuals": visuals[:count]}


def build_report_plan(request: str, metadata: dict[str, Any], clarification_answer: str | None = None) -> dict[str, Any]:
    count = _requested_visual_count(request)
    if count is not None and not 1 <= count <= MAX_VISUALS_PER_PAGE:
        raise ValueError(f"Request between 1 and {MAX_VISUALS_PER_PAGE} visuals per page.")
    suggested = _suggest_visuals(request, metadata, count) if count and not clarification_answer else None
    plan = suggested or paired_comparison(request, metadata) or _parse_report_plan(request, metadata, clarification_answer)
    if count is not None:
        mode = requested_visual_count_mode(request)
        visuals = plan["visuals"]
        analytical = sum(v["type"] != "slicer" for v in visuals)
        allowed = {len(visuals)} if mode == "total" else {analytical} if mode == "analytical" else {analytical, len(visuals)}
        if count not in allowed:
            raise ValueError(f"Requested {count} visuals but resolved {len(visuals)} items including "
                             f"{len(visuals) - analytical} slicers. List the missing visuals and their fields.")
        validate_report_spec(plan, metadata)
    check_visual_request(plan, request, metadata)
    return plan

def check_visual_request(plan: dict[str, Any], request: str, metadata: dict[str, Any]) -> None:
    """Keep counted or explicitly listed visuals complete and correctly bound."""
    count = _requested_visual_count(request)
    items = _split_visual_instructions(request)
    explicit_visual_list = len(items) > 1 and all(
        re.search(r"\b(?:cards?|kpi|gauges?|slicers?|filters?|charts?|graphs?|matrices?|tables?)\b", item, re.I)
        for item in items
    )
    if plan.get("status") != "needs_clarification":
        slicers = re.search(r"\b(?:include|add|with)\s+(?:interactive\s+)?((?!a\b|an\b|the\b)[\w-]+(?:\s+and\s+[\w-]+)*)\s+slicers?\b", request, re.I)
        if slicers:
            requested = re.split(r"\s+and\s+", slicers[1].casefold())
            bound = {str(v.get("field", {}).get("field", "")).casefold() for v in plan["visuals"] if v.get("type") == "slicer"}
            absent = [f for f in requested if f not in bound]
            if absent:
                raise ValueError("Cannot bind the requested slicers (" + ", ".join(absent) +
                                 ") to this report plan. Identify a common dimension or split the report by data source.")
        paired = paired_comparison(request, metadata)
        if paired:
            from collections import Counter
            def bindings(v):
                return (v.get("category", {}).get("field"), tuple((m.get("table"), m.get("field"))
                        for m in (v.get("values") or [v.get("value", {})])))
            if Counter(map(bindings, paired["visuals"])) - Counter(map(bindings, plan["visuals"])):
                raise ValueError("The comparison plan omits a requested measure or grouping.")
    if (count is None and not explicit_visual_list) or plan.get("status") == "needs_clarification":
        return
    visuals = plan["visuals"]
    roles = ("type", "category", "value", "values", "rows", "field", "legend", "categoryHierarchy")
    def bindings(visual):
        result = {key: visual[key] for key in roles if key in visual}
        if visual.get("type") in CATEGORY_VALUE_TYPES:
            if visual.get("values"):
                result.pop("value", None)
            elif visual.get("value"):
                result["values"] = [result.pop("value")]
        if result.get("legend") is None:
            result.pop("legend", None)
        if result.get("categoryHierarchy") == []:
            result.pop("categoryHierarchy")
        return result
    def signature(visual):
        return json.dumps(bindings(visual), sort_keys=True)
    if explicit_visual_list or (count is not None and len(items) == count and count > 1):
        try:
            reference = create_report_spec(request, metadata)
        except ValueError as exc:
            if explicit_visual_list:
                raise ValueError(f"Could not verify the explicitly requested visual list: {exc}") from exc
            reference = None
        if reference and [signature(v) for v in reference["visuals"]] != [signature(v) for v in visuals]:
            expected = [bindings(visual) for visual in reference["visuals"]]
            actual = [bindings(visual) for visual in visuals]
            differences = []
            if len(expected) != len(actual):
                differences.append(f"Expected {len(expected)} visuals; received {len(actual)}.")
            for index, expected_visual in enumerate(expected):
                if index >= len(actual):
                    differences.append(f"Visual {index + 1} is missing; expected {json.dumps(expected_visual)}.")
                    continue
                changed = [
                    role for role in roles
                    if expected_visual.get(role) != actual[index].get(role)
                ]
                if changed:
                    expected_roles = {role: expected_visual.get(role) for role in changed}
                    actual_roles = {role: actual[index].get(role) for role in changed}
                    differences.append(
                        f"Visual {index + 1} expected roles {json.dumps(expected_roles)}, "
                        f"received {json.dumps(actual_roles)} "
                        "(null means omit the role)."
                    )
            raise ValueError("The proposed visuals differ from the requested list or its field bindings. "
                             + " ".join(differences))
    elif len({signature(v) for v in visuals}) != len(visuals):
        raise ValueError("The plan repeated a visual to fill the requested count.")
    plan["design"] = {**(plan.get("design") or {}), "auto_kpi_cards": False, "auto_time_slicers": False}
