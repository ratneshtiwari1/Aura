import argparse
import json
import os
import re
import zipfile
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import UUID, uuid5

from automation.design_system import (
    DEFAULT_DESIGN,
    ICON_IDS,
    ICON_GLYPHS,
    THEME_PRESETS,
    build_theme,
    layout_positions,
    normalize_design_spec,
    register_custom_theme,
)
from automation.policy import MAX_VISUALS_PER_PAGE
from automation.visual_registry import (
    ALL_VISUAL_TYPES,
    CATEGORY_VALUE_TYPES,
    MULTI_MEASURE_TYPES,
    PBIR_VISUAL_TYPES,
    STANDARD_CHART_TYPES,
)
from backend.services.model_service import _active_relationships

try:
    from automation.semantic_model_inspector import inspect_semantic_model
except ModuleNotFoundError:
    from semantic_model_inspector import inspect_semantic_model


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
_ID_NAMESPACE = UUID("802d6c09-19e5-4fba-8f79-31fd0de6cde7")
_STOP_WORDS = {
    "a", "an", "and", "by", "chart", "create", "dashboard", "for", "generate",
    "in", "legend", "make", "of", "on", "report", "series", "show", "showing", "the", "visual",
}
_DASHBOARD_COMMAND = r"(?:create|build|make|generate)\b"
_EXPLICIT_VISUAL_ITEM = re.compile(
    r"\b(?:cards?|kpi|gauges?|slicers?|filters?|charts?|graphs?|matrices?|tables?)\b",
    re.IGNORECASE,
)
# Retained for callers that imported the former private constant. New code
# should import MAX_VISUALS_PER_PAGE from automation.policy.
_MAX_VISUALS_PER_PAGE = MAX_VISUALS_PER_PAGE
# Compatibility alias for callers that imported the former private constant.
_STANDARD_VISUAL_TYPES = STANDARD_CHART_TYPES
# Accept both conventional multi-line lists and compact one-line lists pasted
# from chat (for example ``1. Card 2. Line chart``).
# Numeric markers may appear in compact one-line lists (``1. ... 2. ...``),
# while dash/star bullets must begin a line or follow a true list delimiter.
# This prevents ordinary names such as ``Plan - Base Case`` from being split
# into separate visual instructions.
_VISUAL_LIST_MARKER = re.compile(
    r"(?:^|[\n:;])\s*[-*]\s+|(?:^|[\n:;]|\s)\d+[.)]\s+",
    re.MULTILINE,
)
_VISUAL_COUNT_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}


def _tokens(value: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", value.casefold())
    return [word[:-3] + "y" if word.endswith("ies") else word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word for word in words if word not in _STOP_WORDS]


def _name_score(name: str, query_tokens: list[str]) -> float:
    name_tokens = _tokens(name)
    if not name_tokens:
        return 0
    overlap = len(set(name_tokens) & set(query_tokens))
    score = overlap / len(set(name_tokens))
    if any(query_tokens[index:index + len(name_tokens)] == name_tokens for index in range(len(query_tokens) - len(name_tokens) + 1)):
        score += 2
    return score


def _resolve_field(
    candidates: list[tuple[str, str]],
    phrase: str,
    kind: str,
    preferred_table: str | None = None,
) -> tuple[str, str]:
    query_tokens = _tokens(phrase)
    scored = [( _name_score(field_name, query_tokens), table_name, field_name) for table_name, field_name in candidates]
    scored = [candidate for candidate in scored if candidate[0] > 0]
    if not scored:
        raise ValueError(f"Could not resolve {kind} from request: {phrase.strip()}")
    best_score = max(candidate[0] for candidate in scored)
    preferred_matches = [
        (table, field) for score, table, field in scored
        if score == best_score and table == preferred_table
    ]
    if preferred_matches:
        return preferred_matches[0]
    best_matches = [(table, field) for score, table, field in scored if score == best_score]
    # Generated workbook measures expose both Total and Average variants for
    # percentages/rates. A rate request should use the average rather than an
    # additive total when the wording does not make that choice explicit.
    if kind in {"measure", "KPI measure", "gauge measure", "multi-row card measure"} and re.search(
        r"%|percent|percentage|rate|margin", phrase, re.IGNORECASE
    ):
        average_matches = [(table, field) for table, field in best_matches if field.casefold().startswith("average ")]
        if len(average_matches) == 1:
            return average_matches[0]
    if len(best_matches) != 1:
        options = ", ".join(f"{table}[{field}]" for table, field in best_matches)
        raise ValueError(f"Ambiguous {kind} in request; matching model fields: {options}")
    return best_matches[0]


def _has_active_relationship_path(metadata: dict[str, Any], start: str, target: str) -> bool:
    graph = {name: set() for name in metadata.get("tables", {})}
    for relationship in _active_relationships(metadata):
        source, destination = relationship.get("source_table"), relationship.get("target_table")
        if source in graph and destination in graph:
            graph[source].add(destination)
            graph[destination].add(source)
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


def _chart_type(request: str) -> str:
    """Resolve the chart type named in one visual instruction."""
    lowered_request = request.casefold()
    if re.search(r"\b(?:stacked\s+combo|stacked\s+combination)\s+chart\b", lowered_request):
        return "lineStackedColumnComboChart"
    if re.search(r"\b(?:combo|combination)\s+chart\b", lowered_request):
        return "lineClusteredColumnComboChart"
    if re.search(r"\bscatter\s+chart\b", lowered_request):
        return "scatterChart"
    if re.search(r"\bwaterfall\s+chart\b", lowered_request):
        return "waterfallChart"
    if re.search(r"\bfunnel\s+chart\b", lowered_request):
        return "funnel"
    if re.search(r"\btreemap\b", lowered_request):
        return "treemap"
    if re.search(r"\b(?:map|geo(?:graphic)? map)\b", lowered_request):
        return "filledMap" if re.search(r"\bfilled\s+map\b", lowered_request) else "map"
    if re.search(r"\bribbon\s+chart\b", lowered_request):
        return "ribbonChart"
    if re.search(r"\b(?:decomposition|decompose)\b", lowered_request):
        return "decompositionTree"
    if re.search(r"\b100\s*%?\s*stacked\s+(?:column|bar)\s+chart\b", lowered_request):
        return "hundredPercentStackedBarChart" if "bar" in lowered_request else "hundredPercentStackedColumnChart"
    if re.search(r"\bstacked\s+(?:column|bar)\s+chart\b", lowered_request):
        return "stackedBarChart" if "bar" in lowered_request else "stackedColumnChart"
    if re.search(r"\bstacked\s+area\s+chart\b", lowered_request):
        return "stackedAreaChart"
    if re.search(r"\b(?:line|trend|over time)\b", lowered_request):
        return "lineChart"
    if re.search(r"\b(?:horizontal bar|bar chart)\b", lowered_request):
        return "clusteredBarChart"
    if re.search(r"\b(?:pie|donut) chart\b", lowered_request):
        return "donutChart" if "donut" in lowered_request else "pieChart"
    if re.search(r"\barea chart\b", lowered_request):
        return "areaChart"
    if re.search(r"\b(?:distribution|share|proportion)\b", lowered_request):
        return "donutChart" if re.search(r"\bdonut\b", lowered_request) else "pieChart"
    if re.search(r"\b(?:top|rank|ranking)\b", lowered_request):
        return "clusteredBarChart"
    return "clusteredColumnChart"


def _select_chart_type(instruction: str, table_name: str, category_field: str, metadata: dict[str, Any]) -> str:
    explicit = re.search(
        r"\b(?:line|horizontal\s+bar|bar|pie|donut|area|column|combo|combination|scatter|waterfall|funnel|ribbon|stacked)\s+chart\b|\b(?:treemap|map|decomposition)\b",
        instruction,
        re.IGNORECASE,
    )
    if explicit or re.search(r"\b(?:trend|over time|distribution|share|proportion|top|rank|ranking)\b", instruction, re.I):
        return _chart_type(instruction)
    roles = metadata["tables"][table_name].get("columnRoles", {})
    if roles.get(category_field) == "time":
        return "lineChart"
    profile = metadata["tables"][table_name].get("columnProfiles", {}).get(category_field, {})
    if profile.get("distinct_count", 0) >= 12:
        return "clusteredBarChart"
    return "clusteredColumnChart"


def _split_visual_instructions(request: str) -> list[str]:
    """Split a prompt only where a new dashboard command begins.

    A comma in ``by region, product`` remains part of one instruction, while
    comma/newline-separated dashboard commands or numbered visual lists become
    separate instructions.
    """
    list_markers = list(_VISUAL_LIST_MARKER.finditer(request))
    if len(list_markers) > 1:
        instructions = [
            request[
                marker.end() : list_markers[index + 1].start() if index + 1 < len(list_markers) else len(request)
            ].strip(" \t\r\n,;.")
            for index, marker in enumerate(list_markers)
        ]
        instructions = [instruction for instruction in instructions if instruction]
    else:
        delimited_items = [
            item.strip(" ,;\n.")
            for item in re.split(r"[;\n]+", request)
            if item.strip(" ,;\n.")
        ]
        if len(delimited_items) > 1 and all(_EXPLICIT_VISUAL_ITEM.search(item) for item in delimited_items):
            instructions = delimited_items
        else:
            requested_count = _requested_visual_count(request)
            compact_body = request.split(":", 1)[1] if requested_count and ":" in request else ""
            compact_items = [item.strip(" ,;\n.") for item in compact_body.split(",") if item.strip(" ,;\n.")]
            if requested_count and len(compact_items) == requested_count:
                instructions = compact_items
            else:
                commands = re.split(rf"(?:[,;\n]+|\.)\s*(?={_DASHBOARD_COMMAND})", request, flags=re.IGNORECASE)
                instructions = [command.strip(" ,;\n.") for command in commands if command.strip(" ,;\n.")]
    expanded: list[str] = []
    for instruction in instructions:
        trailing_slicers = re.fullmatch(
            r"(.+?)\s+(?:add|include)\s+slicers?\s+(?:for|on|by)\s+(.+?)\.?",
            instruction,
            flags=re.IGNORECASE,
        )
        slicer_only = re.fullmatch(
            r"(?:add|include)\s+slicers?\s+(?:for|on|by)\s+(.+?)\.?",
            instruction,
            flags=re.IGNORECASE,
        )
        if trailing_slicers or slicer_only:
            if trailing_slicers:
                expanded.append(trailing_slicers.group(1).strip(" ,;"))
            field_text = (trailing_slicers or slicer_only).group(2 if trailing_slicers else 1).strip(" ,;.")
            fields = [
                re.sub(r"^and\s+", "", field.strip(" ,;."), flags=re.IGNORECASE)
                for field in re.split(r"\s*,\s*|\s+and\s+", field_text)
                if field.strip(" ,;.")
            ]
            expanded.extend(f"Add a slicer for {field}" for field in fields)
            continue
        inline_slicer = re.fullmatch(
            r"(.+?)(?:,?\s+(?:and\s+)?(?:with\s+)?(?:an?\s+)?slicer\s+(?:for|on|by)\s+(.+?))\.?",
            instruction,
            flags=re.IGNORECASE,
        )
        if inline_slicer and re.search(r"\b(?:by|per|across)\b", inline_slicer.group(1), re.IGNORECASE):
            expanded.extend([
                inline_slicer.group(1).strip(" ,;"),
                f"Add a slicer for {inline_slicer.group(2).strip(' ,;.')}",
            ])
        else:
            expanded.append(instruction)
    return expanded


def _requested_visual_count(request: str) -> int | None:
    count_match = re.search(
        r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(?:visuals?|charts?|graphs?)\b",
        request,
        flags=re.IGNORECASE,
    )
    if not count_match:
        return None
    count_text = count_match.group(1).casefold()
    return int(count_text) if count_text.isdigit() else _VISUAL_COUNT_WORDS[count_text]


def requested_visual_count_mode(request: str) -> str:
    """Derive whether a stated count covers all items or analytical visuals.

    The decision comes from the request's grammar and list structure. It does
    not depend on field names, measures, or dashboard-specific vocabulary.
    """
    match = re.search(
        r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(visuals?|charts?|graphs?)\b",
        request,
        flags=re.IGNORECASE,
    )
    requested_count = _requested_visual_count(request)
    if not match or requested_count is None:
        return "either"
    noun = match.group(1).casefold()
    if noun.startswith(("chart", "graph")):
        return "analytical"
    instructions = _split_visual_instructions(request)
    if len(instructions) == requested_count:
        return "total"
    slicer_items = sum(bool(re.search(r"\bslicers?\b", item, re.IGNORECASE)) for item in instructions)
    if slicer_items and len(instructions) - slicer_items == requested_count:
        return "analytical"
    if re.search(r"\b(?:plus|in addition to|along with)\b[^.;\n]*\bslicers?\b", request, re.IGNORECASE):
        return "analytical"
    return "either"


def _visual_specs_from_instruction(instruction: str, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve one dashboard instruction into one or more visual specifications."""
    category_match = re.search(r"\b(?:by|per|across)\s+([^?.!,;]+)", instruction, flags=re.IGNORECASE)
    if not category_match:
        # Accept concise visual names such as "Margin Driver Impact as a
        # waterfall chart" when the workbook provides one matching dimension
        # and one matching measure. This is common in natural-language asks.
        measures = [
            (table_name, measure)
            for table_name, table in metadata["tables"].items()
            for measure in table["measures"]
        ]
        columns = [
            (table_name, column)
            for table_name, table in metadata["tables"].items()
            for column in table["columns"]
            if str(table.get("columnTypes", {}).get(column, "string")).casefold()
            not in {"integer", "decimal", "number", "float", "double"}
        ]
        preferred_table = metadata.get("preferredTable")
        selected_type = _chart_type(instruction)
        if selected_type in MULTI_MEASURE_TYPES:
            raise ValueError(f"A {selected_type} request must identify a category using 'by', 'per', or 'across'")
        category_table, category_field = _resolve_field(columns, instruction, "category", preferred_table)
        value_table, value_field = _resolve_field(measures, instruction, "measure", preferred_table)
        return [{
            "type": selected_type,
            "category": {"table": category_table, "field": category_field, "fieldType": "column"},
            "value": {"table": value_table, "field": value_field, "fieldType": "measure"},
        }]

    value_phrase = instruction[:category_match.start()]
    category_phrase = re.split(r"\s+as\s+", category_match.group(1), maxsplit=1, flags=re.IGNORECASE)[0]
    category_phrase = re.split(r"\s+(?:with|using)\s+", category_phrase, maxsplit=1, flags=re.IGNORECASE)[0]
    category_phrase = re.split(
        r"\s+(?:as|in)\s+(?:a\s+)?(?:line|bar|horizontal bar|pie|donut|area|column|combo|combination|scatter|waterfall|funnel|ribbon|stacked)\s+chart\b|\s+(?:as|in)\s+(?:a\s+)?(?:treemap|map|filled map|decomposition(?:\s+tree)?)\b",
        category_phrase,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    category_phrases = [phrase.strip() for phrase in re.split(r"\s+(?:and|,)\s+", category_phrase) if phrase.strip()]
    hierarchy_phrases = []
    if re.search(r"\b(?:drill\s*down|drilldown|hierarchy)\b|\bthen\b|->|>", category_phrase, re.IGNORECASE):
        hierarchy_phrases = [
            re.sub(r"\s*(?:drill\s*down|drilldown|hierarchy)\b", "", phrase, flags=re.IGNORECASE).strip()
            for phrase in re.split(r"\s*(?:then|->|>)\s*", category_phrase, flags=re.IGNORECASE)
            if re.sub(r"\s*(?:drill\s*down|drilldown|hierarchy)\b", "", phrase, flags=re.IGNORECASE).strip()
        ]
        category_phrases = hierarchy_phrases[:1]
    measures = [
        (table_name, measure)
        for table_name, table in metadata["tables"].items()
        for measure in table["measures"]
    ]
    columns = [
        (table_name, column)
        for table_name, table in metadata["tables"].items()
        for column in table["columns"]
    ]
    preferred_table = metadata.get("preferredTable")
    categories = []
    for phrase in category_phrases:
        try:
            categories.append(_resolve_field(columns, phrase, "category", preferred_table))
        except ValueError as exc:
            clarified_category = metadata.get("clarifiedField")
            if (
                isinstance(clarified_category, tuple)
                and len(clarified_category) == 2
                and clarified_category[0] in metadata["tables"]
                and clarified_category[1] in metadata["tables"][clarified_category[0]]["columns"]
            ):
                categories.append(clarified_category)
                continue
            scored = [(_name_score(field, _tokens(phrase)), table, field) for table, field in columns]
            best_score = max((score for score, _, _ in scored), default=0)
            best = [(table, field) for score, table, field in scored if score == best_score and score > 0]
            # The same conformed field repeated across fact tables can be
            # resolved after measures establish each visual's source table.
            if best and len({field.casefold() for _, field in best}) == 1:
                categories.append(next((item for item in best if item[0] == preferred_table), best[0]))
            else:
                raise exc
    hierarchy_fields = []
    if hierarchy_phrases:
        hierarchy_fields = [
            _resolve_field(columns, phrase, "drilldown level", preferred_table)
            for phrase in hierarchy_phrases
        ]
        if len({table for table, _ in hierarchy_fields}) != 1:
            raise ValueError("All drilldown hierarchy levels must belong to the same table")
        categories = [hierarchy_fields[0]]
    selected_type = _select_chart_type(instruction, categories[0][0], categories[0][1], metadata)
    measure_phrases = [
        phrase.strip(" ,")
        for phrase in re.split(r"\s*(?:,|\bvs\.?\b|\bversus\b|\band\b)\s*", value_phrase, flags=re.IGNORECASE)
        if phrase.strip(" ,")
    ]
    multi_value_request = selected_type in MULTI_MEASURE_TYPES or len(measure_phrases) > 1
    if multi_value_request:
        if len(measure_phrases) < 2:
            raise ValueError(f"A {selected_type} visual requires two named measures")
        measure_phrases = measure_phrases[:3]
        resolved_values = [_resolve_field(measures, phrase, "measure", preferred_table) for phrase in measure_phrases]
        # Prefer one fact table that contains every requested measure. This
        # avoids accidental fact-to-fact joins (which duplicate aggregations).
        common_candidates: list[tuple[int, str, list[tuple[str, str]]]] = []
        for candidate_table, candidate_info in metadata["tables"].items():
            if not any(_name_score(column, _tokens(category_phrases[0])) > 0 for column in candidate_info["columns"]):
                continue
            candidate_values: list[tuple[str, str]] = []
            score = 0
            for phrase in measure_phrases:
                matches = [
                    (_name_score(measure, _tokens(phrase)), measure)
                    for measure in candidate_info["measures"]
                ]
                best_score, best_measure = max(matches, default=(0, ""))
                if best_score <= 0:
                    break
                candidate_values.append((candidate_table, best_measure))
                score += best_score
            if len(candidate_values) == len(measure_phrases):
                common_candidates.append((score, candidate_table, candidate_values))
        if common_candidates:
            _, _, resolved_values = max(common_candidates, key=lambda item: item[0])
        value_table, value_field = resolved_values[0]
    else:
        value_table, value_field = _resolve_field(measures, value_phrase, "measure", preferred_table)
        resolved_values = [(value_table, value_field)]
    value_tables = {table for table, _ in resolved_values}
    if len(resolved_values) == 1 and categories[0][0] != value_table:
        category_table = categories[0][0]
        candidates = [
            (_name_score(measure, _tokens(value_phrase)), measure)
            for measure in metadata["tables"][category_table]["measures"]
        ]
        score, matching_measure = max(candidates, default=(0, ""))
        if score > 0:
            value_table, value_field = category_table, matching_measure
            resolved_values = [(value_table, value_field)]
            value_tables = {value_table}
        elif not _has_active_relationship_path(metadata, category_table, value_table):
            value_columns = [(value_table, column) for column in metadata["tables"][value_table]["columns"]]
            categories = [
                _resolve_field(value_columns, phrase, "category", value_table)
                for phrase in category_phrases
            ]
    if len(resolved_values) > 1 and len(value_tables) == 1:
        value_table = next(iter(value_tables))
        value_columns = [
            (value_table, column)
            for column in metadata["tables"][value_table]["columns"]
        ]
        categories = [
            _resolve_field(value_columns, phrase, "category", value_table)
            for phrase in category_phrases
        ]
    elif len(resolved_values) > 1 and len(value_tables) > 1:
        # Never place measures from unrelated fact tables in one visual. Build
        # one comparable visual per fact table, using that table's own category.
        separate_visuals = []
        for measure_table, measure_field in resolved_values:
            table_columns = [(measure_table, column) for column in metadata["tables"][measure_table]["columns"]]
            category_table, category_field = _resolve_field(
                table_columns, category_phrases[0], "category", measure_table,
            )
            separate_visuals.append({
                "type": selected_type,
                "title": f"{measure_field} by {category_field}",
                "category": {"table": category_table, "field": category_field, "fieldType": "column"},
                "value": {"table": measure_table, "field": measure_field, "fieldType": "measure"},
            })
        return separate_visuals
    legend_match = re.search(
        r"\b(?:with|using)\s+"
        r"(?!(?:an?\s+|the\s+)?(?:(?:100\s*%?\s*)?"
        r"(?:line|trend|horizontal|bar|pie|donut|area|column|combo|combination|scatter|"
        r"waterfall|funnel|ribbon|stacked|filled)\s+)*(?:charts?|graphs?|treemap|map|decomposition)\b)"
        r"([^?.!,;]+)",
        instruction,
        flags=re.IGNORECASE,
    )
    legend_fields = []
    if legend_match:
        legend_phrase = re.split(r"\s+(?:as\s+)?(?:the\s+)?legend\b", legend_match.group(1), maxsplit=1, flags=re.IGNORECASE)[0]
        legend_phrase = re.split(r"\s+series\b", legend_phrase, maxsplit=1, flags=re.IGNORECASE)[0]
        legend_phrase = re.split(
            r"\s+(?:as|in)\s+(?:a\s+)?(?:line|bar|horizontal bar|pie|donut|area|column|combo|combination|scatter|waterfall|funnel|ribbon|stacked)\s+chart\b|\s+(?:as|in)\s+(?:a\s+)?(?:treemap|map|filled map|decomposition(?:\s+tree)?)\b",
            legend_phrase,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        for phrase in re.split(r"\s+(?:and|,)\s+", legend_phrase):
            if phrase.strip():
                legend_table, legend_field = _resolve_field(columns, phrase, "legend", preferred_table)
                legend_fields.append({"table": legend_table, "field": legend_field, "fieldType": "column"})
        legend_field_names = {field["field"] for field in legend_fields}
        if legend_field_names == {"Scenario", "Segment"}:
            table_name = legend_fields[0]["table"]
            if all(field["table"] == table_name for field in legend_fields) and (table_name, "ScenarioSegment") in columns:
                legend_fields = [{
                    "table": table_name,
                    "field": "ScenarioSegment",
                    "fieldType": "column",
                }]

    return [
        {
            "type": selected_type,
            "category": {"table": category_table, "field": category_field, "fieldType": "column"},
            **(
                {"categoryHierarchy": [
                    {"table": table_name, "field": field_name, "fieldType": "column"}
                    for table_name, field_name in hierarchy_fields
                ]}
                if hierarchy_fields else {}
            ),
            **(
                {"values": [{"table": table, "field": field, "fieldType": "measure"} for table, field in resolved_values]}
                if len(resolved_values) > 1
                else {"value": {"table": value_table, "field": value_field, "fieldType": "measure"}}
            ),
            **({"legend": legend_fields[0]} if legend_fields else {}),
            **({"legendBreakdown": legend_fields[1:]} if len(legend_fields) > 1 else {}),
        }
        for category_table, category_field in categories
    ]


def _special_visual_spec(instruction: str, metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve cards, matrices, slicers, and comparisons from an explicit request."""
    lowered = instruction.casefold()
    measures = [(table_name, measure) for table_name, table in metadata["tables"].items() for measure in table["measures"]]
    columns = [(table_name, column) for table_name, table in metadata["tables"].items() for column in table["columns"]]
    preferred_table = metadata.get("preferredTable")
    metric_phrase = re.split(r"\b(?:cards?|gauges?|kpi)\s+(?:for|of|showing)\s+", instruction, maxsplit=1, flags=re.I)[-1]
    metric_phrase = re.split(r"\b(?:called|named)\s+", metric_phrase, maxsplit=1, flags=re.I)[-1]
    if re.search(r"\bmulti[ -]?row\s+card\b", lowered):
        table, field = _resolve_field(measures, metric_phrase, "multi-row card measure", preferred_table)
        return {"type": "multiRowCard", "value": {"table": table, "field": field, "fieldType": "measure"}}
    if re.search(r"\bkpi\b(?!\s+card\b)", lowered):
        table, field = _resolve_field(measures, metric_phrase, "KPI measure", preferred_table)
        return {"type": "kpi", "value": {"table": table, "field": field, "fieldType": "measure"}}
    if re.search(r"\bgauge\b", lowered):
        table, field = _resolve_field(measures, metric_phrase, "gauge measure", preferred_table)
        return {"type": "gauge", "value": {"table": table, "field": field, "fieldType": "measure"}}
    if re.search(r"\b(?:kpi|card)\b", lowered):
        table, field = _resolve_field(measures, metric_phrase, "measure", preferred_table)
        return {"type": "card", "value": {"table": table, "field": field, "fieldType": "measure"}}
    if re.search(r"\b(?:slicer|filter)\b", lowered):
        field_phrase = re.sub(r"\b(?:show|add|create|include|a|the|slicer|filter|by|for)\b", " ", instruction, flags=re.IGNORECASE)
        field_phrase = re.sub(r"\s+", " ", field_phrase).strip(" .,:;")
        try:
            table, field = _resolve_field(columns, field_phrase, "slicer field", preferred_table)
        except ValueError:
            clarified_field = metadata.get("clarifiedField")
            if (
                not isinstance(clarified_field, tuple)
                or len(clarified_field) != 2
                or clarified_field not in columns
                or _name_score(clarified_field[1], _tokens(field_phrase)) <= 0
            ):
                raise
            table, field = clarified_field
        return {"type": "slicer", "field": {"table": table, "field": field, "fieldType": "column"}}
    if re.search(r"\b(?:heat\s*map|matrix|table)\b", lowered):
        category_match = re.search(r"\b(?:by|per|across)\s+([^?.!,;]+)", instruction, flags=re.IGNORECASE)
        if not category_match:
            raise ValueError("A matrix request must identify rows using 'by', 'per', or 'across'")
        value_table, value_field = _resolve_field(measures, instruction[:category_match.start()], "measure", preferred_table)
        row_table, row_field = _resolve_field(columns, category_match.group(1), "matrix row", preferred_table)
        is_heatmap = bool(re.search(r"\bheat\s*map\b", lowered))
        return {"type": "pivotTable" if is_heatmap or "matrix" in lowered else "table", "rows": [{"table": row_table, "field": row_field, "fieldType": "column"}], "values": [{"table": value_table, "field": value_field, "fieldType": "measure"}], **({"heatmap": True, "conditionalFormatting": True} if is_heatmap else {})}
    if (
        re.search(r"\b(?:compare|comparison|versus|vs\.?|actual\s+.*budget|budget\s+.*actual)\b", lowered)
        and not re.search(r"\b(?:combo|combination|scatter)\s+chart\b", lowered)
    ):
        category_match = re.search(r"\b(?:by|per|across)\s+([^?.!,;]+)", instruction, flags=re.IGNORECASE)
        if not category_match:
            raise ValueError("A comparison request must identify a category using 'by', 'per', or 'across'")
        named_measures = [
            phrase.strip(" ,")
            for phrase in re.split(
                r"\s*(?:,|\bvs\.?\b|\bversus\b|\bto\b|\band\b)\s*",
                instruction[:category_match.start()],
                flags=re.IGNORECASE,
            )
            if phrase.strip(" ,")
        ]
        if len(named_measures) != 2:
            return None
        first_table, first_field = _resolve_field(measures, named_measures[0], "first comparison measure", preferred_table)
        second_table, second_field = _resolve_field(measures, named_measures[1], "second comparison measure", preferred_table)
        category_table, category_field = _resolve_field(columns, category_match.group(1), "comparison category", preferred_table)
        if len({first_table, second_table, category_table}) > 1:
            # Let the general chart planner produce safe per-table visuals.
            return None
        return {"type": "clusteredColumnChart", "category": {"table": category_table, "field": category_field, "fieldType": "column"}, "values": [{"table": first_table, "field": first_field, "fieldType": "measure"}, {"table": second_table, "field": second_field, "fieldType": "measure"}], "comparison": True}
    return None


def create_report_spec(request: str, metadata: dict[str, Any]) -> dict[str, Any]:
    instructions = _split_visual_instructions(request)
    if not instructions:
        raise ValueError("Request is required")

    requested_count = _requested_visual_count(request)
    if requested_count is not None and requested_count > MAX_VISUALS_PER_PAGE:
        raise ValueError(f"A page supports at most {MAX_VISUALS_PER_PAGE} visuals")

    visuals = []
    for instruction in instructions:
        scope = metadata
        if len(instructions) > 1:
            matches = [name for name, table in metadata["tables"].items()
                       if any(re.search(rf"(?<!\w){re.escape(m)}(?!\w)", instruction, re.I)
                              for m in _metadata_field_names(table, "measures"))]
            if len(matches) == 1:
                scope = {**metadata, "preferredTable": matches[0]}
        special = _special_visual_spec(instruction, scope)
        visuals.extend([special] if special else _visual_specs_from_instruction(instruction, scope))
    if len(visuals) > MAX_VISUALS_PER_PAGE:
        raise ValueError(f"A page supports at most {MAX_VISUALS_PER_PAGE} visuals")
    analytical_visual_count = sum(visual.get("type") != "slicer" for visual in visuals)
    if requested_count is not None and requested_count not in {analytical_visual_count, len(visuals)}:
        raise ValueError(
            f"Request asks for {requested_count} visuals but specifies {len(visuals)} total and "
            f"{analytical_visual_count} analytical visuals; "
            "list each visual separately so the plan can match the requested count"
        )

    dashboard_match = re.search(
        r"\b(?:create|build|make|generate)\s+(?:a\s+|an\s+|the\s+)?([a-z0-9 ]+?)\s+dashboard\b",
        request,
        flags=re.IGNORECASE,
    )
    page_name = f"{dashboard_match.group(1).strip().title()} Dashboard" if dashboard_match else "Generated Report"
    return {"page": page_name, "visuals": visuals, "design": dict(DEFAULT_DESIGN)}


def _visual_positions(visuals: list[dict[str, Any]]) -> list[dict[str, int]]:
    return layout_positions(visuals, DEFAULT_DESIGN["layout"])[0]


def _page_canvas_size(
    visual_count: int,
    visuals: list[dict[str, Any]] | None = None,
) -> tuple[int, int, int, int]:
    visuals = visuals if visuals is not None else [{"type": "lineChart"} for _ in range(visual_count)]
    _, width, height = layout_positions(visuals, DEFAULT_DESIGN["layout"])
    return width, height, 36, 20


def _field_binding(field: dict[str, str]) -> dict[str, Any]:
    field_kind = "Measure" if field["fieldType"] == "measure" else "Column"
    return {
        field_kind: {
            "Expression": {"SourceRef": {"Entity": field["table"]}},
            "Property": field["field"],
        }
    }


def _projection(field: dict[str, str]) -> dict[str, Any]:
    return {
        "field": _field_binding(field),
        "queryRef": f"{field['table']}.{field['field']}",
        "nativeQueryRef": field["field"],
        "active": True,
    }


def _metadata_field_names(table: dict[str, Any], field_type: str) -> list[str]:
    names = []
    for item in table.get(field_type, []):
        if isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str):
                names.append(name)
        elif isinstance(item, str):
            names.append(item)
    return names


def validate_report_spec(spec: dict[str, Any], metadata: dict[str, Any]) -> None:
    if not isinstance(spec, dict):
        raise ValueError("Report spec must be a JSON object")
    if "additionalPages" in spec:
        from automation.report_pages import report_pages
        for page in report_pages(spec)[1:]:
            validate_report_spec(page, metadata)
    if not isinstance(spec.get("page"), str) or not spec["page"].strip():
        raise ValueError("Report spec must include a page name")
    if "pageNavigation" in spec and not isinstance(spec["pageNavigation"], bool):
        raise ValueError("Page navigation must be a boolean")
    for page_option in ("tooltipPage", "resetFilters"):
        if page_option in spec and not isinstance(spec[page_option], bool):
            raise ValueError(f"{page_option} must be a boolean")
    if "bookmarks" in spec and (
        not isinstance(spec["bookmarks"], list)
        or not spec["bookmarks"]
        or any(not isinstance(name, str) or not name.strip() or len(name) > 100 for name in spec["bookmarks"])
    ):
        raise ValueError("Bookmarks must be a non-empty list of names")
    from automation.report_pages import validate_sync_groups
    validate_sync_groups(spec, require_multiple_pages="additionalPages" in spec)
    visuals = spec.get("visuals")
    if not isinstance(visuals, list) or not visuals:
        raise ValueError("Report spec must include at least one visual")
    if len(visuals) > MAX_VISUALS_PER_PAGE:
        raise ValueError(f"A page supports at most {MAX_VISUALS_PER_PAGE} visuals")
    normalize_design_spec(spec)
    for visual in visuals:
        if not isinstance(visual, dict):
            raise ValueError("Report visuals must be JSON objects")
        supported_types = ALL_VISUAL_TYPES
        if visual.get("type") not in supported_types:
            raise ValueError(f"Unsupported visual type: {visual.get('type')}")
        visual_type = visual["type"]
        if visual_type in {"card", "multiRowCard", "kpi", "gauge"} and not visual.get("value"):
            raise ValueError(f"A {visual_type} visual requires a value measure")
        if "title" in visual and (
            not isinstance(visual["title"], str) or len(visual["title"]) > 120
        ):
            raise ValueError("Visual titles must be strings no longer than 120 characters")
        if "icon" in visual and visual["icon"] not in ICON_IDS:
            raise ValueError(f"Unsupported icon identifier: {visual['icon']}")
        if "conditionalFormatting" in visual and not isinstance(visual["conditionalFormatting"], bool):
            raise ValueError("conditionalFormatting must be a boolean")
        if visual.get("heatmap"):
            if visual_type != "pivotTable":
                raise ValueError("A heatmap must use a matrix visual")
            visual["conditionalFormatting"] = True
        if visual.get("conditionalFormatting") and visual_type not in {"table", "pivotTable"}:
            raise ValueError("Conditional formatting is supported only on tables and matrices")
        if "bookmark" in visual and (
            not isinstance(visual["bookmark"], str) or not visual["bookmark"].strip() or len(visual["bookmark"]) > 100
        ):
            raise ValueError("A visual bookmark must be a non-empty name")
        if "tooltipPage" in visual and (
            not isinstance(visual["tooltipPage"], str) or not visual["tooltipPage"].strip() or len(visual["tooltipPage"]) > 100
        ):
            raise ValueError("tooltipPage must name a report page")
        drill_through = visual.get("drillThrough")
        if drill_through is not None:
            if not isinstance(drill_through, dict) or not isinstance(drill_through.get("targetPage"), str) or not drill_through["targetPage"].strip():
                raise ValueError("Drill-through requires a targetPage")
            if "fields" in drill_through and (not isinstance(drill_through["fields"], list) or any(not isinstance(field, dict) for field in drill_through["fields"])):
                raise ValueError("Drill-through fields must be field bindings")
        comparison = visual.get("comparison")
        if isinstance(comparison, bool):
            if visual_type not in _STANDARD_VISUAL_TYPES:
                raise ValueError("A visual comparison flag is only supported on charts")
            comparison = None
        elif comparison is not None:
            if visual_type != "card" or not isinstance(comparison, dict):
                raise ValueError("KPI comparisons are only supported on card visuals")
            if comparison.get("label") not in {"previous_year", "previous_period", "target", "variance"}:
                raise ValueError("KPI comparison label must be previous_year, previous_period, target, or variance")
            if comparison.get("direction") not in {"positive", "negative"}:
                raise ValueError("KPI comparison direction must be positive or negative")
            if not isinstance(comparison.get("value"), dict):
                raise ValueError("KPI comparison requires a measure binding")
        if visual_type == "slicer" and not visual.get("field"):
            raise ValueError("A slicer visual requires a column field")
        if visual_type == "pivotTable" and (not visual.get("rows") or not visual.get("values")):
            raise ValueError(f"A {visual_type} visual requires at least one row and one value field")
        if visual_type == "table" and not visual.get("rows"):
            raise ValueError("A table visual requires at least one row field")
        if visual_type == "table" and (
            not isinstance(visual["rows"], list)
            or not isinstance(visual.get("values", []), list)
        ):
            raise ValueError("Table rows and values must be lists of field bindings")
        if visual_type == "table" and (
            any(not isinstance(field, dict) or field.get("fieldType") != "column" for field in visual["rows"])
            or any(not isinstance(field, dict) or field.get("fieldType") != "measure" for field in visual.get("values", []))
        ):
            raise ValueError("Table rows must be columns and table values must be measures")
        if visual_type in CATEGORY_VALUE_TYPES and (
            not visual.get("category") or not (visual.get("value") or visual.get("values"))
        ):
            raise ValueError(f"A {visual_type} visual requires a category and at least one value measure")
        if visual_type in MULTI_MEASURE_TYPES and len(visual.get("values", [])) < 2:
            raise ValueError(f"A {visual_type} visual requires at least two value measures")
        hierarchy = visual.get("categoryHierarchy", [])
        if hierarchy and (
            visual.get("type") in {"card", "multiRowCard", "kpi", "gauge", "slicer", "pivotTable", "table"}
            or any(field.get("table") != hierarchy[0].get("table") for field in hierarchy)
            or visual.get("category", {}).get("table") != hierarchy[0].get("table")
        ):
            raise ValueError("A drilldown hierarchy requires chart categories from the same table")
        for role in ("columns", "tooltips"):
            if role in visual and (not isinstance(visual[role],list) or any(not isinstance(f,dict) for f in visual[role])):
                raise ValueError(f"{role} must be a list of field bindings")
        if visual.get("tooltips") and visual_type not in CATEGORY_VALUE_TYPES:
            raise ValueError("Measure tooltips are supported only for chart visuals")
        extras = {"target": {"gauge", "kpi"}, "minimum":{"gauge"}, "maximum":{"gauge"},
                  "trend":{"kpi"}, "size":{"scatterChart"}}
        for role, kinds in extras.items():
            if visual.get(role) is not None and visual_type not in kinds:
                raise ValueError(f"{role} binding is unsupported for {visual_type}")
        if visual.get("columns") and visual_type != "pivotTable":
            raise ValueError("Column groups are supported only on matrix visuals")
        fields = [
            ("category", visual.get("category"), "columns"),
            ("value", visual.get("value"), "measures"),
            ("field", visual.get("field"), "columns"),
            ("legend", visual.get("legend"), "columns"),
        ]
        if isinstance(comparison, dict):
            fields.append(("comparison", comparison["value"], "measures"))
        fields.extend(("legend", field, "columns") for field in visual.get("legendBreakdown", []))
        fields.extend(("row", field, "columns") for field in visual.get("rows", []))
        fields.extend(("value", field, "measures") for field in visual.get("values", []))
        fields.extend(("drilldown level", field, "columns") for field in visual.get("categoryHierarchy", []))
        for role in ("target", "minimum", "maximum", "size"):
            fields.append((role,visual.get(role),"measures"))
        fields.append(("trend",visual.get("trend"),"columns"))
        fields.extend(("tooltip",f,"measures") for f in visual.get("tooltips",[]))
        fields.extend(("column group",f,"columns") for f in visual.get("columns",[]))
        fields.extend(("drill-through", f, "columns") for f in (drill_through or {}).get("fields", []))
        bound_tables = set()
        for role, field, field_type in fields:
            if field is None:
                continue
            if not isinstance(field, dict) or not isinstance(field.get("table"), str) or not isinstance(field.get("field"), str):
                raise ValueError(f"Invalid {role} field in report spec: {field}")
            table_name = field["table"].strip()
            canonical_table_name = next(
                (
                    name for name in metadata["tables"]
                    if name.casefold() == table_name.casefold()
                ),
                None,
            )
            table = metadata["tables"].get(canonical_table_name) if canonical_table_name else None
            names = _metadata_field_names(table, field_type) if table else []
            canonical_field_name = next(
                (
                    name for name in names
                    if name.strip().casefold() == field["field"].strip().casefold()
                ),
                None,
            )
            if not table or canonical_field_name is None:
                raise ValueError(f"Unknown {role} field in report spec: {field}")
            field["table"] = canonical_table_name
            field["field"] = canonical_field_name
            bound_tables.add(canonical_table_name)
        if len(bound_tables) > 1:
            raise ValueError(
                "A visual must use one source table. Use shared dimension tables only after they are explicitly modeled; "
                "automatic fact-to-fact joins can duplicate totals."
            )


def _report_directory(report_path: str | Path, root: Path = WORKSPACE_ROOT) -> Path:
    workspace_root = root.resolve()
    report_dir = (workspace_root / str(report_path)).resolve()
    if report_dir == workspace_root or workspace_root not in report_dir.parents:
        raise ValueError("Report path must point inside the configured workspace")
    return report_dir


def _field_reference_name(field: Any) -> str:
    if isinstance(field, dict):
        if "Column" in field:
            return str(field["Column"].get("Property", ""))
        if "Measure" in field:
            return str(field["Measure"].get("Property", ""))
        if "Expression" in field:
            return str(field.get("Property", ""))
    return ""


def validate_pbir(report_path: str | Path, metadata: dict[str, Any] | None = None, root: Path = WORKSPACE_ROOT) -> dict[str, Any]:
    report_dir = _report_directory(report_path, root)
    errors: list[str] = []
    warnings: list[str] = []

    required_paths = [
        report_dir / "definition.pbir",
        report_dir / "definition" / "report.json",
        report_dir / "definition" / "version.json",
        report_dir / "definition" / "pages" / "pages.json",
    ]
    for path in required_paths:
        if not path.is_file():
            errors.append(f"Missing required PBIR file: {path.relative_to(report_dir)}")

    if errors:
        return {"reportPath": str(report_path), "passed": False, "errors": errors, "warnings": warnings}

    properties = json.loads((report_dir / "definition.pbir").read_text(encoding="utf-8"))
    report_definition = json.loads((report_dir / "definition" / "report.json").read_text(encoding="utf-8"))
    base_theme = report_definition.get("themeCollection", {}).get("baseTheme", {}).get("name")
    if base_theme:
        theme_file = report_dir / "StaticResources" / "SharedResources" / "BaseThemes" / f"{base_theme}.json"
        if not theme_file.is_file():
            errors.append(f"Missing report theme resource: {theme_file.relative_to(report_dir)}")
        else:
            try:
                theme = json.loads(theme_file.read_text(encoding="utf-8"))
                if not isinstance(theme.get("dataColors"), list) or not theme["dataColors"]:
                    errors.append("Report theme must define at least one chart color")
                elif any(
                    not isinstance(color, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color)
                    for color in theme["dataColors"]
                ):
                    errors.append("Report theme contains an invalid chart color")
                for color_key in ("background", "foreground", "tableAccent"):
                    color = theme.get(color_key)
                    if not isinstance(color, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                        errors.append(f"Report theme has an invalid {color_key} color")
            except json.JSONDecodeError as exc:
                errors.append(f"Invalid JSON in report theme {theme_file.relative_to(report_dir)}: {exc}")
    custom_theme = report_definition.get("themeCollection", {}).get("customTheme")
    if custom_theme is not None:
        name = custom_theme.get("name") if isinstance(custom_theme, dict) else None
        if not isinstance(name, str) or not name.endswith(".json") or Path(name).name != name or custom_theme.get("type") != "RegisteredResources":
            errors.append("Invalid custom report theme reference")
        else:
            packages = report_definition.get("resourcePackages", [])
            matches = [
                item for package in packages
                if package.get("type") == "RegisteredResources" and not package.get("disabled", False)
                for item in package.get("items", [])
                if item.get("name") == name and item.get("path") == name and item.get("type") == "CustomTheme"
            ]
            if len(matches) != 1:
                errors.append(f"Custom report theme is not registered exactly once: {name}")
            theme_file = report_dir / "StaticResources" / "RegisteredResources" / name
            if not theme_file.is_file():
                errors.append(f"Missing custom report theme resource: {theme_file.relative_to(report_dir)}")
            else:
                try:
                    custom_data = json.loads(theme_file.read_text(encoding="utf-8"))
                    if not isinstance(custom_data, dict) or custom_data.get("name") != name:
                        errors.append(f"Custom report theme name does not match its resource: {name}")
                    elif not isinstance(custom_data.get("dataColors"), list) or not custom_data["dataColors"]:
                        errors.append("Custom report theme must define at least one chart color")
                except json.JSONDecodeError as exc:
                    errors.append(f"Invalid JSON in custom report theme {name}: {exc}")
    model_relative_path = properties.get("datasetReference", {}).get("byPath", {}).get("path")
    if model_relative_path is None:
        errors.append("definition.pbir is missing datasetReference.byPath.path")
    elif not (report_dir / model_relative_path).resolve().exists():
        errors.append(f"Referenced semantic model does not exist: {model_relative_path}")
    else:
        model_path = (report_dir / model_relative_path).resolve()
        required_model_files = [
            model_path / "definition.pbism",
            model_path / "definition" / "database.tmdl",
            model_path / "definition" / "model.tmdl",
        ]
        for path in required_model_files:
            if not path.is_file():
                errors.append(f"Missing required semantic model file: {path.relative_to(model_path)}")
        table_files = sorted((model_path / "definition" / "tables").rglob("*.tmdl")) if (model_path / "definition" / "tables").is_dir() else []
        if not table_files:
            errors.append("Semantic model does not contain any TMDL table definitions")
        else:
            try:
                from automation.semantic_model_inspector import inspect_semantic_model
                model_metadata = inspect_semantic_model(model_path)
                if metadata is None:
                    metadata = model_metadata
                else:
                    for table_name, table in metadata.get("tables", {}).items():
                        model_table = model_metadata.get("tables", {}).get(table_name)
                        if model_table is None:
                            errors.append(f"Semantic model is missing table '{table_name}'")
                            continue
                        for column in table.get("columns", []):
                            if column not in model_table["columns"]:
                                errors.append(f"Semantic model is missing column '{column}' on table '{table_name}'")
                        for measure in table.get("measures", []):
                            if measure not in model_table["measures"]:
                                errors.append(f"Semantic model is missing measure '{measure}' on table '{table_name}'")
                    actual_relationships = {
                        (relationship.get("fromColumn"), relationship.get("toColumn"))
                        for relationship in model_metadata.get("relationships", [])
                    }
                    # Match the cycle-free set emitted into the TMDL model.
                    # Other inferred candidates are intentionally excluded to
                    # prevent ambiguous active filter paths in Power BI.
                    for relationship in _active_relationships(metadata):
                        source = f"{relationship['source_table']}.{relationship['source_column']}"
                        target = f"{relationship['target_table']}.{relationship['target_column']}"
                        if (source, target) not in actual_relationships:
                            errors.append(f"Semantic model is missing relationship '{source}' to '{target}'")
            except Exception as error:
                errors.append(f"Unable to inspect semantic model TMDL: {error}")

    pages_metadata = json.loads((report_dir / "definition" / "pages" / "pages.json").read_text(encoding="utf-8"))
    page_order = pages_metadata.get("pageOrder", [])
    if not isinstance(page_order, list) or not page_order:
        errors.append("Page metadata must include a non-empty pageOrder list")

    if metadata is None:
        metadata = {"tables": {}}

    seen_pages: set[str] = set()
    for page_id in page_order:
        page_file = (report_dir / "definition" / "pages" / str(page_id) / "page.json").resolve()
        if report_dir / "definition" / "pages" not in page_file.parents:
            errors.append(f"Page reference escapes the pages directory: {page_id}")
            continue
        if not page_file.is_file():
            errors.append(f"Missing page definition for page id: {page_id}")
            continue
        seen_pages.add(str(page_id))
        page = json.loads(page_file.read_text(encoding="utf-8"))
        if not page.get("displayName"):
            warnings.append(f"Page {page_id} is missing a display name")
        page_width = page.get("width")
        page_height = page.get("height")

        visuals_dir = page_file.parent / "visuals"
        if visuals_dir.exists():
            visual_files = sorted(visuals_dir.rglob("visual.json"))
            if not visual_files:
                warnings.append(f"Page {page_id} does not contain any visual definitions")
            positioned_visuals = []
            for visual_file in visual_files:
                try:
                    visual = json.loads(visual_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    errors.append(f"Invalid JSON in visual {visual_file.relative_to(report_dir)}: {exc}")
                    continue
                query_state = visual.get("visual", {}).get("query", {}).get("queryState", {})
                position = visual.get("position")
                if position is not None:
                    try:
                        x, y = int(position["x"]), int(position["y"])
                        width, height = int(position["width"]), int(position["height"])
                        if min(x, y) < 0 or min(width, height) <= 0:
                            errors.append(f"Visual {visual_file.relative_to(report_dir)} has an invalid position")
                        elif (
                            isinstance(page_width, int)
                            and isinstance(page_height, int)
                            and (x + width > page_width or y + height > page_height)
                        ):
                            errors.append(f"Visual {visual_file.relative_to(report_dir)} exceeds page bounds")
                        else:
                            positioned_visuals.append((
                                visual_file, x, y, width, height,
                                visual.get("name", "").startswith("autobi-decoration-"),
                            ))
                    except (KeyError, TypeError, ValueError):
                        errors.append(f"Visual {visual_file.relative_to(report_dir)} has an invalid position")
                for role_name, projection in query_state.items():
                    if not isinstance(projection, dict):
                        continue
                    projections = projection.get("projections", [])
                    for descriptor in projections:
                        field = descriptor.get("field")
                        if not isinstance(field, dict):
                            continue
                        kind = "Column" if "Column" in field else "Measure" if "Measure" in field else None
                        if kind is None:
                            continue
                        property_name = field.get(kind, {}).get("Property")
                        table_name = field.get(kind, {}).get("Expression", {}).get("SourceRef", {}).get("Entity")
                        if not property_name:
                            errors.append(f"Visual {visual_file.relative_to(report_dir)} is missing a valid {kind} property")
                            continue
                        if not table_name:
                            tables = list(metadata.get("tables", {}).keys())
                            if len(tables) == 1:
                                table_name = tables[0]
                            else:
                                errors.append(
                                    f"Visual {visual_file.relative_to(report_dir)} is missing a table binding for {kind} '{property_name}'"
                                )
                                continue
                        table = metadata.get("tables", {}).get(table_name)
                        if table is None:
                            errors.append(f"Unknown table '{table_name}' in visual {visual_file.relative_to(report_dir)}")
                            continue
                        valid_fields = table.get("columns" if kind == "Column" else "measures", [])
                        if property_name not in valid_fields:
                            errors.append(
                                f"Unknown {kind.lower()} field '{property_name}' on table '{table_name}' in visual "
                                f"{visual_file.relative_to(report_dir)}"
                            )
            for index, (first_file, first_x, first_y, first_width, first_height, first_decoration) in enumerate(positioned_visuals):
                for second_file, second_x, second_y, second_width, second_height, second_decoration in positioned_visuals[index + 1:]:
                    if first_decoration or second_decoration:
                        continue
                    overlaps = (
                        first_x < second_x + second_width
                        and first_x + first_width > second_x
                        and first_y < second_y + second_height
                        and first_y + first_height > second_y
                    )
                    if overlaps:
                        errors.append(
                            f"Visuals {first_file.relative_to(report_dir)} and {second_file.relative_to(report_dir)} overlap"
                        )

    if seen_pages and len(seen_pages) != len(page_order):
        missing_pages = [page_id for page_id in page_order if str(page_id) not in seen_pages]
        errors.extend(f"Page metadata includes missing page definition: {page_id}" for page_id in missing_pages)

    return {"reportPath": str(report_path), "passed": not errors, "errors": errors, "warnings": warnings}


def _pbip_project_definition(report_name: str, report_dir: Path) -> dict[str, Any]:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0",
        "artifacts": [{"report": {"path": report_dir.name}}],
    }


def create_pbip_project_file(report_path: str | Path, root: Path = WORKSPACE_ROOT) -> Path:
    report_dir = _report_directory(report_path, root)
    report_name = report_dir.name.rsplit(".", 1)[0] if "." in report_dir.name else report_dir.name
    project_path = report_dir.parent / f"{report_name}.pbip"
    project_path.write_text(
        json.dumps(_pbip_project_definition(report_name, report_dir), ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return project_path


def package_pbip(report_path: str | Path, root: Path = WORKSPACE_ROOT) -> Path:
    report_dir = _report_directory(report_path, root)
    report_name = report_dir.name.rsplit(".", 1)[0] if "." in report_dir.name else report_dir.name
    output_dir = root / "generated-pbip"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = (output_dir / f"{report_name}.pbip.zip").resolve()
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            f"{report_name}.pbip",
            json.dumps(_pbip_project_definition(report_name, report_dir), ensure_ascii=True, indent=2) + "\n",
        )
        for file_path in sorted(report_dir.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, arcname=file_path.relative_to(report_dir.parent))
        properties_path = report_dir / "definition.pbir"
        if properties_path.is_file():
            properties = json.loads(properties_path.read_text(encoding="utf-8"))
            model_relative_path = properties.get("datasetReference", {}).get("byPath", {}).get("path")
            if model_relative_path:
                model_path = (report_dir / model_relative_path).resolve()
                workspace_root = root.resolve()
                if workspace_root not in model_path.parents:
                    raise ValueError("Referenced semantic model must remain inside the configured workspace")
                if model_path.is_dir():
                    for file_path in sorted(model_path.rglob("*")):
                        if file_path.is_file():
                            archive.write(file_path, arcname=file_path.relative_to(report_dir.parent))
    return output_path


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
        json.dump(value, file, ensure_ascii=True, indent=2)
        file.write("\n")
        temporary_path = Path(file.name)
    os.replace(temporary_path, path)


def _literal(value: str) -> dict[str, Any]:
    return {"expr": {"Literal": {"Value": value}}}


def _blend_colors(foreground: str, background: str, foreground_weight: float) -> str:
    foreground_rgb = tuple(int(foreground[index:index + 2], 16) for index in (1, 3, 5))
    background_rgb = tuple(int(background[index:index + 2], 16) for index in (1, 3, 5))
    blended = tuple(
        round(foreground_channel * foreground_weight + background_channel * (1 - foreground_weight))
        for foreground_channel, background_channel in zip(foreground_rgb, background_rgb)
    )
    return "#" + "".join(f"{channel:02X}" for channel in blended)


def _dashboard_title_visual(
    page_id: str,
    title: str,
    color: str,
    width: int,
    font_family: str,
    use_header: bool = False,
) -> dict[str, Any]:
    visual_id = uuid5(UUID(page_id), "autobi-decoration-title").hex
    position = (
        {"x": 720, "y": 14, "z": 1000, "width": width - 756, "height": 52, "tabOrder": 0}
        if use_header
        else {"x": 36, "y": 16, "z": 1000, "width": width - 72, "height": 56, "tabOrder": 0}
    )
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.12.0/schema.json",
        "name": f"autobi-decoration-{visual_id}",
        "position": position,
        "visual": {
            "visualType": "textbox",
            "objects": {
                "general": [{
                    "properties": {
                        "paragraphs": [{
                            "textRuns": [{
                                "value": title,
                                "textStyle": {
                                    "fontFamily": font_family,
                                    "fontSize": "20pt" if use_header else "24pt",
                                    "fontWeight": "bold",
                                    "color": color,
                                },
                            }],
                            "horizontalTextAlignment": "right" if use_header else "left",
                        }],
                    },
                }],
            },
            "visualContainerObjects": {
                "background": [{"properties": {"show": _literal("false"), "transparency": _literal("100D")}}],
                "border": [{"properties": {"show": _literal("false")}}],
            },
            "drillFilterOtherVisuals": True,
        },
    }


def _accent_strip_visual(page_id: str, index: int, position: dict[str, int], color: str) -> dict[str, Any]:
    visual_id = uuid5(UUID(page_id), f"autobi-decoration-kpi-accent-{index}").hex
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.12.0/schema.json",
        "name": f"autobi-decoration-{visual_id}",
        "position": {
            "x": position["x"] + 1,
            "y": position["y"] + 1,
            "z": 1000 + index,
            "width": 56,
            "height": position["height"] - 2,
            "tabOrder": 1000 + index,
        },
        "visual": {
            "visualType": "shape",
            "visualContainerObjects": {
                "background": [{"properties": {"show": _literal("false"), "transparency": _literal("100D")}}],
                "border": [{"properties": {"show": _literal("false")}}],
                "title": [{"properties": {"show": _literal("false")}}],
            },
            "objects": {
                "shape": [{
                    "properties": {
                        "tileShape": _literal("'rectangleRoundedByPixel'"),
                        "rectangleRoundedCurve": _literal("8D"),
                    },
                    "selector": {"id": "default"},
                }],
                "fill": [{
                    "properties": {
                        "show": _literal("true"),
                        "fillColor": {"solid": {"color": _literal(f"'{color}'")}},
                        "transparency": _literal("0D"),
                    },
                    "selector": {"id": "default"},
                }],
                "outline": [{
                    "properties": {"show": _literal("false")},
                    "selector": {"id": "default"},
                }],
            },
            "drillFilterOtherVisuals": True,
        },
    }


def _panel_background_visual(
    page_id: str,
    index: int,
    position: dict[str, int],
    fill_color: str,
    border_color: str,
    fill_transparency: int = 0,
) -> dict[str, Any]:
    visual_id = uuid5(UUID(page_id), f"autobi-decoration-panel-{index}").hex
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.12.0/schema.json",
        "name": f"autobi-decoration-{visual_id}",
        "position": {
            **position,
            "z": index * 2,
            "tabOrder": index * 2,
        },
        "visual": {
            "visualType": "shape",
            "visualContainerObjects": {
                "background": [{"properties": {"show": _literal("false"), "transparency": _literal("100D")}}],
                "border": [{"properties": {"show": _literal("false")}}],
                "title": [{"properties": {"show": _literal("false")}}],
            },
            "objects": {
                "shape": [{
                    "properties": {
                        "tileShape": _literal("'rectangleRoundedByPixel'"),
                        "rectangleRoundedCurve": _literal("8D"),
                    },
                    "selector": {"id": "default"},
                }],
                "fill": [{
                    "properties": {
                        "show": _literal("true"),
                        "fillColor": {"solid": {"color": _literal(f"'{fill_color}'")}},
                        "transparency": _literal(f"{fill_transparency}D"),
                    },
                    "selector": {"id": "default"},
                }],
                "outline": [{
                    "properties": {
                        "show": _literal("true"),
                        "lineColor": {"solid": {"color": _literal(f"'{border_color}'")}},
                        "weight": _literal("1D"),
                    },
                    "selector": {"id": "default"},
                }],
            },
            "drillFilterOtherVisuals": True,
        },
    }


def _semantic_column(table: dict[str, Any], role: str) -> dict[str, str] | None:
    columns = _metadata_field_names(table, "columns")
    normalized = {
        re.sub(r"[^a-z0-9]", "", name.casefold()): name
        for name in columns
    }
    aliases = {
        "year": {"year", "fiscalyear", "calendaryear"},
        "scenario": {"scenario", "scenarioname"},
    }
    exact = [name for key, name in normalized.items() if key in aliases[role]]
    if len(exact) == 1:
        return {"table": table["name"], "field": exact[0], "fieldType": "column"}
    partial = [
        name for key, name in normalized.items()
        if role in key and (role != "year" or "scenario" not in key)
    ]
    if len(partial) == 1:
        return {"table": table["name"], "field": partial[0], "fieldType": "column"}
    return None


def _normalize_year_scenario_visuals(
    spec: dict[str, Any],
    metadata: dict[str, Any],
) -> list[dict[str, str]]:
    requested_filters: dict[tuple[str, str], dict[str, str]] = {}
    for visual in spec["visuals"]:
        if visual.get("type") not in _STANDARD_VISUAL_TYPES:
            continue
        category = visual.get("category")
        if not isinstance(category, dict):
            continue
        table = metadata["tables"].get(category.get("table"))
        if not table:
            continue
        year = _semantic_column({**table, "name": category["table"]}, "year")
        scenario = _semantic_column({**table, "name": category["table"]}, "scenario")
        if not year or not scenario:
            continue

        hierarchy = visual.get("categoryHierarchy", [])
        category_key = re.sub(r"[^a-z0-9]", "", category["field"].casefold())
        composite = "year" in category_key and "scenario" in category_key
        hierarchy_fields = {
            re.sub(r"[^a-z0-9]", "", field.get("field", "").casefold())
            for field in hierarchy
            if isinstance(field, dict)
        }
        combined_hierarchy = any("year" in field for field in hierarchy_fields) and any(
            "scenario" in field for field in hierarchy_fields
        )
        category_is_year = category["field"].casefold() == year["field"].casefold()
        hierarchy_has_year = any("year" in field for field in hierarchy_fields)
        if not (composite or combined_hierarchy or category_is_year or hierarchy_has_year):
            continue

        visual["category"] = year
        visual.pop("categoryHierarchy", None)
        existing_legend = visual.get("legend")
        if existing_legend and existing_legend["field"].casefold() != scenario["field"].casefold():
            visual["legendBreakdown"] = [
                existing_legend,
                *visual.get("legendBreakdown", []),
            ]
        visual["legend"] = scenario
        for field in (year, scenario):
            requested_filters[(field["table"].casefold(), field["field"].casefold())] = field

    existing_filters = {
        (
            visual["field"]["table"].casefold(),
            visual["field"]["field"].casefold(),
        )
        for visual in spec["visuals"]
        if visual.get("type") == "slicer" and isinstance(visual.get("field"), dict)
    }
    added_filters = [
        field for key, field in requested_filters.items()
        if key not in existing_filters
    ][:MAX_VISUALS_PER_PAGE - len(spec["visuals"])]
    spec["visuals"].extend({"type": "slicer", "field": field} for field in added_filters)
    return added_filters


def _add_kpi_cards_from_dashboard_measures(
    spec: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    if spec["design"]["layout"] == "compact_dashboard":
        return
    charts = [
        visual for visual in spec["visuals"]
        if visual.get("type") in _STANDARD_VISUAL_TYPES
    ]
    if len(charts) < 2:
        return
    cards = [visual for visual in spec["visuals"] if visual.get("type") == "card"]
    seen = {
        (card["value"]["table"].casefold(), card["value"]["field"].casefold())
        for card in cards
    }
    measures = []
    for visual in spec["visuals"]:
        if visual.get("type") == "card":
            candidates = [visual["value"]]
        elif visual.get("type") in _STANDARD_VISUAL_TYPES:
            candidates = visual.get("values") or ([visual["value"]] if visual.get("value") else [])
        else:
            continue
        for measure in candidates:
            key = (measure["table"].casefold(), measure["field"].casefold())
            if key not in seen:
                measures.append(measure)
                seen.add(key)
    # A two-chart executive page still needs four KPI cards.  Complete the
    # row with other existing measures from the same chart tables; no measures
    # are invented or duplicated.
    chart_tables = {
        binding["table"]
        for visual in charts
        for binding in (visual.get("values") or ([visual["value"]] if visual.get("value") else []))
        if isinstance(binding, dict) and isinstance(binding.get("table"), str)
    }
    for table_name in chart_tables:
        table = metadata.get("tables", {}).get(table_name, {})
        for measure_name in table.get("measures", []):
            if not isinstance(measure_name, str):
                continue
            key = (table_name.casefold(), measure_name.casefold())
            if key not in seen:
                measures.append({"table": table_name, "field": measure_name, "fieldType": "measure"})
                seen.add(key)
    if len(seen) < 4:
        return
    available_slots = max(0, MAX_VISUALS_PER_PAGE - len(spec["visuals"]))
    for measure in measures[: min(available_slots, max(0, 4 - len(cards)))]:
        spec["visuals"].append({"type": "card", "value": dict(measure)})


def generate_pbir(report_path: str | Path, spec: dict[str, Any], metadata: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    validate_report_spec(spec, metadata)
    design = normalize_design_spec(spec)
    spec["design"] = design
    if design["auto_time_slicers"] or design["auto_kpi_cards"]:
        _normalize_year_scenario_visuals(spec, metadata)
    if design["auto_kpi_cards"]:
        _add_kpi_cards_from_dashboard_measures(spec, metadata)
    workspace_root = (root or WORKSPACE_ROOT).resolve()
    report_dir = (workspace_root / report_path).resolve()
    if workspace_root not in report_dir.parents:
        raise ValueError("Report path must point inside the workspace")

    definition_dir = report_dir / "definition"
    report_properties = json.loads((report_dir / "definition.pbir").read_text(encoding="utf-8"))
    model_relative_path = report_properties["datasetReference"]["byPath"]["path"]
    model_path = (report_dir / model_relative_path).resolve()
    if not model_path.is_dir():
        raise FileNotFoundError(f"Referenced semantic model does not exist: {model_path}")

    pages_metadata_path = definition_dir / "pages" / "pages.json"
    pages_metadata = json.loads(pages_metadata_path.read_text(encoding="utf-8"))
    signature = {"page": spec["page"], "visuals": spec["visuals"], "design": design}
    if spec.get("pageNavigation"):
        signature["pageNavigation"] = True
    page_signature = json.dumps(
        signature,
        sort_keys=True,
        separators=(",", ":"),
    )
    page_id = uuid5(_ID_NAMESPACE, f"{report_dir}:{page_signature}").hex
    # Visual containers have a 12px internal inset. Starting at 88 places
    # visible content at y=100 and retains the established report geometry.
    content_top = 136 if design["auto_kpi_cards"] else 88
    if spec.get("pageNavigation"):
        content_top += 48
    page_dir = definition_dir / "pages" / page_id
    page_path = page_dir / "page.json"
    if page_path.exists():
        page = json.loads(page_path.read_text(encoding="utf-8"))
    else:
        page = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json",
            "name": page_id,
            "displayName": spec["page"],
            "displayOption": "FitToPage",
            "height": layout_positions(spec["visuals"], design["layout"], content_top)[2],
            "width": layout_positions(spec["visuals"], design["layout"], content_top)[1],
        }
    page["displayName"] = spec["page"]
    positions, page["width"], page["height"] = layout_positions(
        spec["visuals"], design["layout"], content_top
    )
    theme = build_theme(design)
    reference_style = design["theme"] == "dar_light"
    canvas_color = "#F3F3F3" if reference_style else theme["background"]
    page_objects = {}
    if not reference_style:
        page_objects["background"] = [{
            "properties": {
                "color": {"solid": {"color": _literal(f"'{canvas_color}'")}},
                "transparency": {"expr": {"Literal": {"Value": "0.0D"}}},
            },
        }]
    page_objects["outspace"] = [{
            "properties": {
                "color": {"solid": {"color": _literal(f"'{canvas_color}'")}},
                "transparency": {"expr": {"Literal": {"Value": "0.0D"}}},
            },
        }]
    page["objects"] = page_objects
    title_visual = _dashboard_title_visual(
        page_id,
        spec["page"],
        theme["foreground"],
        page["width"],
        theme["textClasses"]["title"]["fontFace"],
        reference_style,
    )
    title_visual_path = page_dir / "visuals" / title_visual["name"] / "visual.json"
    _write_json(title_visual_path, title_visual)
    if spec.get("pageNavigation"):
        navigator_id = f"autobi-decoration-{uuid5(UUID(page_id), 'page-navigation').hex}"
        navigator = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.12.0/schema.json",
            "name": navigator_id,
            "position": {"x": 36, "y": 80, "z": 1001, "width": page["width"] - 72, "height": 40, "tabOrder": 0},
            "visual": {"visualType": "pageNavigator", "objects": {}},
        }
        _write_json(page_dir / "visuals" / navigator_id / "visual.json", navigator)

    report_json_path = definition_dir / "report.json"
    if report_json_path.is_file():
        report_json = json.loads(report_json_path.read_text(encoding="utf-8"))
        report_theme = build_theme(design)
        theme_name = report_theme["name"]
        # The PBIR slicer visual still honors the legacy outline property.
        # Keep the source DAR theme unmodified for preset fidelity, while
        # writing the compatibility value into generated report resources.
        slicer_items = (
            report_theme.setdefault("visualStyles", {})
            .setdefault("slicer", {})
            .setdefault("*", {})
            .setdefault("items", [])
        )
        if not slicer_items:
            slicer_items.append({})
        if isinstance(slicer_items[0], dict):
            slicer_items[0]["outline"] = "None"
        theme_path = (
            report_dir / "StaticResources" / "SharedResources" / "BaseThemes" / f"{theme_name}.json"
        )
        _write_json(theme_path, report_theme)
        theme_resource = report_json.get("resourcePackages", [{}])[0].get("items", [{}])[0]
        theme_resource["name"] = theme_name
        theme_resource["path"] = f"BaseThemes/{theme_name}.json"
        theme_resource["type"] = "BaseTheme"
        report_json.setdefault("themeCollection", {}).setdefault("baseTheme", {})["name"] = theme_name
        register_custom_theme(report_dir, report_json, report_theme)
        _write_json(report_json_path, report_json)

    generated_visuals = []
    for index, visual_spec in enumerate(spec["visuals"]):
        visual_id = uuid5(UUID(page_id), f"generated-visual-{index}").hex
        visual_dir = page_dir / "visuals" / visual_id
        visual_type = visual_spec["type"]
        # cardVisual requires a different PBIR query/configuration schema than
        # the legacy card definition emitted below.  Retain card so Desktop
        # materializes the KPI reliably; DAR's legacy card theme still applies.
        rendered_visual_type = PBIR_VISUAL_TYPES.get(visual_type, visual_type)
        kpi_comparison = visual_spec.get("comparison")
        if not isinstance(kpi_comparison, dict):
            kpi_comparison = None
        slot_position = dict(positions[index])
        panel = _panel_background_visual(
            page_id,
            index,
            slot_position,
            theme["tableAccent"] if reference_style and visual_type == "card" else theme["background"] if reference_style else theme["backgroundLight"],
            theme["tableAccent"] if reference_style and visual_type == "card" else theme["backgroundNeutral"],
            94 if reference_style and visual_type == "card" else 0,
        )
        panel_path = page_dir / "visuals" / panel["name"] / "visual.json"
        if reference_style and visual_type == "slicer":
            if panel_path.is_file():
                panel_path.unlink()
                panel_path.parent.rmdir()
        else:
            _write_json(panel_path, panel)
        inset = 5 if visual_type == "slicer" else 12
        position = {
            "x": slot_position["x"] + inset,
            "y": slot_position["y"] + inset,
            "z": index * 2 + 1,
            "width": slot_position["width"] - inset * 2,
            "height": slot_position["height"] - inset * 2,
            "tabOrder": index * 2 + 1,
        }
        query_state: dict[str, Any]
        if visual_type in {"card", "multiRowCard"}:
            card_values = [visual_spec["value"]]
            if kpi_comparison and design["show_variance"]:
                card_values.append(kpi_comparison["value"])
            query_state = {"Values": {"projections": [_projection(field) for field in card_values]}}
        elif visual_type == "slicer":
            query_state = {"Values": {"projections": [_projection(visual_spec["field"])]}}
        elif visual_type in {"pivotTable", "table"}:
            query_state = {
                "Rows": {"projections": [_projection(field) for field in visual_spec["rows"]]},
                "Values": {"projections": [_projection(field) for field in visual_spec.get("values", [])]},
            }
            if visual_type == "table":
                query_state = {"Values": {"projections": [
                    *[_projection(field) for field in visual_spec["rows"]],
                    *[_projection(field) for field in visual_spec.get("values", [])],
                ]}}
        elif visual_type == "gauge":
            query_state = {"Y": {"projections": [_projection(visual_spec["value"])]}}
        elif visual_type == "kpi":
            query_state = {"Indicator": {"projections": [_projection(visual_spec["value"])]}}
        else:
            value_fields = visual_spec["values"] if "values" in visual_spec else [visual_spec["value"]]
            category_fields = visual_spec.get("categoryHierarchy") or [visual_spec["category"]]
            query_state = {
                "Category": {"projections": [_projection(field) for field in category_fields]},
                "Y": {"projections": [_projection(field) for field in value_fields]},
            }
            if visual_type == "scatterChart":
                query_state = {
                    "Category": {"projections": [_projection(field) for field in category_fields]},
                    "X": {"projections": [_projection(value_fields[0])]},
                    "Y": {"projections": [_projection(value_fields[1])]},
                }
            elif visual_type in {"lineClusteredColumnComboChart", "lineStackedColumnComboChart"}:
                query_state = {
                    "Category": {"projections": [_projection(field) for field in category_fields]},
                    "Y": {"projections": [_projection(value_fields[0])]},
                    "Y2": {"projections": [_projection(field) for field in value_fields[1:]]},
                }
            elif visual_type == "treemap":
                query_state = {"Group": {"projections": [_projection(field) for field in category_fields]}, "Values": {"projections": [_projection(field) for field in value_fields]}}
            elif visual_type in {"map", "filledMap"}:
                query_state = {"Location": {"projections": [_projection(field) for field in category_fields]}, "Size": {"projections": [_projection(field) for field in value_fields]}}
            elif visual_type == "decompositionTree":
                query_state = {"Values": {"projections": [_projection(field) for field in value_fields]}, "ExplainBy": {"projections": [_projection(field) for field in category_fields]}}
            if visual_spec.get("legend"):
                series_fields = [visual_spec["legend"], *visual_spec.get("legendBreakdown", [])]
                query_state["Series"] = {"projections": [_projection(field) for field in series_fields]}
        if visual_type == "pivotTable" and visual_spec.get("columns"):
            query_state["Columns"] = {"projections":[_projection(f) for f in visual_spec["columns"]]}
        extra_roles = {"gauge":{"minimum":"MinValue","maximum":"MaxValue","target":"TargetValue"},
                       "kpi":{"target":"Goal","trend":"TrendLine"},"scatterChart":{"size":"Size"}}
        for field_name, role in extra_roles.get(visual_type,{}).items():
            if visual_spec.get(field_name):
                query_state[role] = {"projections":[_projection(visual_spec[field_name])]}
        if visual_spec.get("tooltips"):
            query_state["Tooltips"] = {"projections":[_projection(f) for f in visual_spec["tooltips"]]}
        colors = {**THEME_PRESETS[design["theme"]], **design.get("colors", {})}
        title = visual_spec.get("title")
        if title is None and visual_type in {"card", "multiRowCard", "kpi", "gauge"}:
            title = visual_spec["value"]["field"]
        if title is None and visual_type in CATEGORY_VALUE_TYPES:
            measure = (visual_spec.get("values") or [visual_spec.get("value")])[0]
            title = f"{measure['field']} by {visual_spec['category']['field']}"
        if title is None and visual_type == "slicer":
            title = visual_spec["field"]["field"]
        if kpi_comparison and design["show_variance"]:
            indicator = "▲" if kpi_comparison["direction"] == "positive" else "▼"
            comparison_label = kpi_comparison["label"].replace("_", " ").title()
            title = f"{title} · {comparison_label} {indicator}"
        if visual_spec.get("icon") and design["show_icons"]:
            title = f"{ICON_GLYPHS[visual_spec['icon']]}  {title}"
        if title is None:
            title = "Matrix" if visual_type == "pivotTable" else "Table" if visual_type == "table" else visual_type
        title_color = colors["text"]
        if kpi_comparison and design["show_variance"]:
            title_color = (
                colors["chartColors"][6] if reference_style else theme["good"]
            ) if kpi_comparison["direction"] == "positive" else theme["bad"]
        container_objects = {
            "background": [{
                "properties": {
                    "show": _literal("false"),
                    "color": {"solid": {"color": colors["cardBackground"]}},
                    "transparency": _literal("100D"),
                },
            }],
            "border": [{
                "properties": {
                    "show": _literal("false"),
                    "color": {"solid": {"color": colors["border"]}},
                },
            }],
            "title": [{
                "properties": {
                    "show": {"expr": {"Literal": {"Value": "true"}}},
                    "text": {"expr": {"Literal": {"Value": f"'{title.replace(chr(39), chr(39) * 2)}'"}}},
                    "fontColor": {"solid": {"color": title_color}},
                    "fontSize": _literal("16D" if reference_style and visual_type == "card" else "13D" if visual_type == "card" or reference_style else "11D"),
                    "bold": {"expr": {"Literal": {"Value": "true"}}},
                    "titleWrap": {"expr": {"Literal": {"Value": "true"}}},
                },
            }],
        }
        if reference_style:
            if visual_type in {"card", "multiRowCard", "kpi", "gauge"}:
                # The DAR card theme supplies the measure label and value.
                # A generated container title would duplicate that label.
                container_objects["title"][0]["properties"]["show"] = _literal("false")
            else:
                # Keep only the generated title text; DAR supplies its style.
                for property_name in ("fontColor", "fontSize", "bold"):
                    container_objects["title"][0]["properties"].pop(property_name, None)
        if visual_type == "slicer":
            container_objects["title"][0]["properties"]["show"] = _literal("false")
            slicer_mode = "List" if design["slicer_style"] == "horizontal" else "Dropdown"
            if design["slicer_style"] == "date":
                table = metadata["tables"][visual_spec["field"]["table"]]
                column_type = str(table.get("columnTypes", {}).get(visual_spec["field"]["field"], "")).casefold()
                if "date" in column_type or "time" in column_type:
                    slicer_mode = "Between"
            visual_objects = {
                "data": [{
                    "properties": {
                        "mode": {"expr": {"Literal": {"Value": f"'{slicer_mode}'"}}},
                    },
                }],
                "selection": [{
                    "properties": {
                        "singleSelect": _literal("false"),
                    },
                }],
            }
            visual_objects["dropdown"] = [{
                "properties": {
                    "borderColor": {"solid": {"color": colors["cardBackground"]}},
                    "accentBarShow": _literal("false"),
                    "borderRadius": _literal("6D"),
                },
            }]
        elif visual_type in CATEGORY_VALUE_TYPES and reference_style:
            # Use the DAR theme's axis, legend, label, and title definitions.
            visual_objects = {}
        elif visual_type in CATEGORY_VALUE_TYPES:
            has_legend = bool(visual_spec.get("legend") or visual_spec.get("legendBreakdown"))
            value_fields = visual_spec.get("values") or [visual_spec.get("value")]
            chart_labels = visual_type in {"pieChart", "donutChart"} or (
                design["visual_style"] == "data_dense"
                and visual_type in {"clusteredColumnChart", "clusteredBarChart"}
            )
            visual_objects = {
                "categoryAxis": [{
                    "properties": {
                        "showAxisTitle": _literal("false"),
                        "fontSize": _literal("12D" if reference_style else "9D"),
                        "labelColor": {"solid": {"color": colors["mutedText"]}},
                        "gridlineColor": {"solid": {"color": colors["gridline"]}},
                    },
                }],
                "valueAxis": [{
                    "properties": {
                        "showAxisTitle": _literal("false"),
                        "fontSize": _literal("12D" if reference_style else "9D"),
                        "labelColor": {"solid": {"color": colors["mutedText"]}},
                        "gridlineColor": {"solid": {"color": colors["gridline"]}},
                    },
                }],
                "legend": [{
                    "properties": {
                        "show": _literal("true" if has_legend or len(value_fields) > 1 else "false"),
                        "position": _literal("'Bottom'"),
                        "showTitle": _literal("false"),
                        "fontSize": _literal("12D" if reference_style else "10D"),
                    },
                }],
                "labels": [{
                    "properties": {
                        "show": _literal("true" if chart_labels else "false"),
                        "fontSize": _literal("10D"),
                    },
                }],
            }
        else:
            visual_objects = {}
        visual_json = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.12.0/schema.json",
            "name": visual_id,
            "position": position,
            "visual": {
                "visualType": rendered_visual_type,
                "query": {
                    "queryState": query_state
                },
                # Explicit empty objects let Desktop materialize default chart
                # and container formatting for a generated visual.
                "objects": visual_objects,
                "visualContainerObjects": container_objects,
                "drillFilterOtherVisuals": True,
            },
        }
        if visual_type == "slicer" and visual_spec.get("syncGroup"):
            visual_json["visual"]["syncGroup"] = {
                "groupName": visual_spec["syncGroup"], "fieldChanges": True, "filterChanges": True,
            }
        _write_json(visual_dir / "visual.json", visual_json)
        generated_visuals.append(visual_id)
        # Do not overlay a separate accent shape on a KPI card.  Power BI
        # renders a higher z-index decoration above the card title and can
        # hide the heading.  The card's theme supplies the accent instead.

    written = {p.parent.name for p in (page_dir / "visuals").glob("*/visual.json")
               if not p.parent.name.startswith("autobi-decoration-")}
    if len(generated_visuals) != len(written) or set(generated_visuals) != written:
        raise ValueError("Written PBIR visuals do not match the planned page")
    _write_json(page_path, page)
    if page_id not in pages_metadata.get("pageOrder", []):
        pages_metadata.setdefault("pageOrder", []).append(page_id)
    pages_metadata["activePageName"] = page_id
    _write_json(pages_metadata_path, pages_metadata)

    validation_result = validate_pbir(report_path, metadata, workspace_root)
    decorations = [
        path.parent.name
        for path in (page_dir / "visuals").glob("autobi-decoration-*/visual.json")
    ]
    return {
        "pageId": page_id,
        "page": spec["page"],
        "visualIds": generated_visuals,
        "decorationIds": decorations,
        "validation": validation_result,
    }


def automate_report(request: str, report_path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    report_dir = (WORKSPACE_ROOT / report_path).resolve()
    properties = json.loads((report_dir / "definition.pbir").read_text(encoding="utf-8"))
    model_path = (report_dir / properties["datasetReference"]["byPath"]["path"]).resolve()
    metadata = inspect_semantic_model(model_path)
    spec = create_report_spec(request, metadata)
    result = generate_pbir(report_path, spec, metadata)
    return spec, result


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a PBIR page from a natural-language report request")
    parser.add_argument("request", help="For example: Create a dashboard showing a measure by category.")
    parser.add_argument(
        "--report",
        required=True,
        help="Path to the target .Report folder, relative to the repository root or absolute.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved specification without writing PBIR")
    arguments = parser.parse_args()

    if arguments.dry_run:
        report_dir = (WORKSPACE_ROOT / arguments.report).resolve()
        properties = json.loads((report_dir / "definition.pbir").read_text(encoding="utf-8"))
        model_path = (report_dir / properties["datasetReference"]["byPath"]["path"]).resolve()
        metadata = inspect_semantic_model(model_path)
        print(json.dumps(create_report_spec(arguments.request, metadata), indent=2))
        return

    spec, result = automate_report(arguments.request, arguments.report)
    print(json.dumps({"spec": spec, "generated": result}, indent=2))


if __name__ == "__main__":
    main()
