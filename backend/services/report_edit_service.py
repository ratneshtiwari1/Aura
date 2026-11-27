"""Safe, additive conversational edits for validated report specifications."""
from __future__ import annotations

import copy
import re
from typing import Any

from automation.report_automation import _requested_visual_count
from automation.report_pages import MAX_REPORT_PAGES, pack_pages, report_pages
from backend.services.report_planner import build_report_plan
from backend.policy import MAX_VISUALS_PER_PAGE

_TYPE_WORDS = {
    "pie": "pieChart", "donut": "donutChart", "bar": "clusteredBarChart",
    "column": "clusteredColumnChart", "line": "lineChart", "area": "areaChart",
    "combo": "lineClusteredColumnComboChart", "scatter": "scatterChart",
    "waterfall": "waterfallChart", "treemap": "treemap", "map": "map",
    "ribbon": "ribbonChart", "decomposition": "decompositionTree",
    "gauge": "gauge", "table": "table",
    "funnel": "funnel", "filled map": "filledMap", "stacked": "stackedColumnChart",
}
_CHART_TYPES = set(_TYPE_WORDS.values())
_NEW_PAGE_REQUEST = re.compile(
    r"^\s*(?:please\s+)?(?:add|create|include)\s+(?:(?:a|the)\s+)?(?:new\s+)?"
    r"page(?:\s+(\d+))?(?:\s+(?:called|named|titled)\s+([^:\n]+?))?\s*(?::|\s+with\b)\s*(.+?)\s*$",
    re.I | re.S,
)
_TRAILING_NUMBERED_PAGE = re.compile(
    r"\s+(?:on|to|in)\s+(?:the\s+)?(?:(?:page\s+(\d+))|"
    r"(?:(first|second|third|fourth|fifth|\d+)(?:st|nd|rd|th)?\s+page))\s*[.!]?$", re.I,
)


def numbered_page_target(instruction: str) -> tuple[int, str] | None:
    match = _TRAILING_NUMBERED_PAGE.search(instruction)
    if not match:
        return None
    word = (match.group(1) or match.group(2)).casefold()
    number = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5}.get(word)
    return (number if number is not None else int(word), instruction[:match.start()].strip())


def _types(text: str) -> list[str]:
    matches: list[tuple[int, str]] = []
    for word, kind in _TYPE_WORDS.items():
        match = re.search(rf"\b{word}(?:\s+chart)?\b", text.casefold())
        if match:
            matches.append((match.start(), kind))
    return [kind for _, kind in sorted(matches)]


def _field(metadata: dict[str, Any], phrase: str, kind: str) -> dict[str, str] | None:
    words = set(re.findall(r"[a-z0-9]+", phrase.casefold()))
    key = "columns" if kind == "column" else "measures"
    choices = []
    for table, info in metadata.get("tables", {}).items():
        for name in info.get(key, []):
            score = len(words & set(re.findall(r"[a-z0-9]+", name.casefold())))
            if score:
                choices.append((score, {"table": table, "field": name, "fieldType": kind}))
    return max(choices, key=lambda item: item[0])[1] if choices else None


def _existing_chart_bindings(visuals: list[dict[str, Any]]) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    """Return the first validated chart's category and measure bindings."""
    for visual in visuals:
        if visual.get("type") not in _CHART_TYPES:
            continue
        category = visual.get("category")
        value = visual.get("value")
        if not isinstance(category, dict) and isinstance(visual.get("categoryHierarchy"), list):
            category = next((item for item in visual["categoryHierarchy"] if isinstance(item, dict)), None)
        if not isinstance(value, dict) and isinstance(visual.get("values"), list):
            value = next((item for item in visual["values"] if isinstance(item, dict)), None)
        if isinstance(category, dict) and isinstance(value, dict):
            return dict(category), dict(value)
    return None, None


def _category_phrase(instruction: str) -> str | None:
    match = re.search(r"\b(?:for|by|per|across)\s+(.+?)(?:\s+(?:as|with|using)\b|$)", instruction, re.I)
    return match.group(1).strip(" ,;.") if match else None


def _slicer_field_phrases(instruction: str) -> list[str]:
    match = re.search(
        r"\b(?:add|include)\b([^.!?]*?)\bslicers?\b(?:\s+(?:for|on|by)\s+([^.!?]+))?",
        instruction,
        re.I,
    )
    if not match:
        return []
    field_text = match.group(2)
    if field_text is None:
        field_text = re.sub(
            r"\b(?:interactive|a|an|the|new)\b",
            " ",
            match.group(1),
            flags=re.I,
        )
    return [
        phrase.strip(" ,;.")
        for phrase in re.split(r"\s*,\s*|\s+(?:and|or)\s+", field_text, flags=re.I)
        if phrase.strip(" ,;.")
    ]


def _slicer_removal_field_phrases(instruction: str) -> list[str]:
    match = re.search(
        r"\b(?:remove|delete|drop)\b\s+(?:(?:the|a|an)\s+)?"
        r"(?:slicers?\s+(?:for|on|by)\s+|(.+?)\s+slicers?\b)([^.!?]*)",
        instruction,
        re.I,
    )
    if not match:
        return []
    field_text = match.group(1) or match.group(2)
    if not field_text:
        return []
    field_text = re.sub(
        r"\s+(?:from|in)\s+(?:the\s+)?(?:report|page)\b.*$",
        "",
        field_text,
        flags=re.I,
    )
    return [
        phrase.strip(" ,;.")
        for phrase in re.split(r"\s*,\s*|\s+(?:and|or)\s+", field_text, flags=re.I)
        if phrase.strip(" ,;.")
    ]


def edit_report_spec(spec: dict[str, Any], instruction: str, metadata: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    target = numbered_page_target(instruction)
    if target:
        number, edit_instruction = target
        pages = report_pages(spec)
        if number < 1 or number > len(pages) + 1:
            raise ValueError(f"The next page number is {len(pages) + 1}; add pages in order.")
        if number == len(pages) + 1:
            if not metadata:
                raise ValueError("Workbook fields are required to create a new report page.")
            if number > MAX_REPORT_PAGES:
                raise ValueError(f"A report supports at most {MAX_REPORT_PAGES} pages.")
            page = build_report_plan(edit_instruction, metadata)
            page_name = f"Page {number}"
            if any(existing["page"].casefold() == page_name.casefold() for existing in pages):
                raise ValueError(f"A report page named '{page_name}' already exists.")
            pages.append({**page, "page": page_name})
            return pack_pages(pages), [f"Added page '{page_name}'"]
        pages[number - 1], changes = edit_report_spec(pages[number - 1], edit_instruction, metadata)
        return pack_pages(pages), changes
    new_page = _NEW_PAGE_REQUEST.fullmatch(instruction)
    if new_page:
        if not metadata:
            raise ValueError("Workbook fields are required to create a new report page.")
        pages = report_pages(spec)
        next_number = len(pages) + 1
        requested_number = int(new_page.group(1)) if new_page.group(1) else next_number
        if requested_number != next_number:
            raise ValueError(f"The next page number is {next_number}; add pages in order.")
        if next_number > MAX_REPORT_PAGES:
            raise ValueError(f"A report supports at most {MAX_REPORT_PAGES} pages.")
        page_name = (new_page.group(2) or f"Page {next_number}").strip()
        if any(page["page"].casefold() == page_name.casefold() for page in pages):
            raise ValueError(f"A report page named '{page_name}' already exists.")
        page_plan = build_report_plan(new_page.group(3).strip(), metadata)
        pages.append({**page_plan, "page": page_name})
        return pack_pages(pages), [f"Added page '{page_name}'"]

    if spec.get("additionalPages"):
        pages = report_pages(spec)
        matches = [(i, page) for i, page in enumerate(pages)
                   if re.search(rf"(?<!\w){re.escape(page['page'])}(?!\w)", instruction, re.I)]
        if len(matches) != 1:
            raise ValueError("Name one page to edit: " + ", ".join(p["page"] for p in pages))
        i, page = matches[0]
        instruction = re.sub(rf"\s+(?:on|to|from|in)\s+(?:the\s+)?(?:page\s+)?{re.escape(page['page'])}(?:\s+page)?\s*[.!]?$", "", instruction, flags=re.I)
        pages[i], changes = edit_report_spec(page, instruction, metadata)
        return pack_pages(pages), changes
    revised, lowered, changes = copy.deepcopy(spec), instruction.casefold(), []
    visuals = revised["visuals"]
    if metadata and _requested_visual_count(instruction) and re.match(r"^\s*(?:please\s+)?(?:add|include)\b", lowered):
        additions = build_report_plan(instruction, metadata)["visuals"]
        if len(visuals) + len(additions) > MAX_VISUALS_PER_PAGE:
            raise ValueError(f"This edit would exceed the {MAX_VISUALS_PER_PAGE}-visual dashboard limit")
        visuals.extend(additions)
        return revised, [f"Added {len(additions)} visuals"]

    types = _types(instruction)

    if metadata and types and re.search(r"\b(add|include)\b", lowered):
        chart_type = types[-1]
        phrase = _category_phrase(instruction)
        existing_category, existing_measure = _existing_chart_bindings(visuals)
        category = _field(metadata, phrase, "column") if phrase else existing_category
        measure = existing_measure
        if category and not measure:
            table_measures = metadata.get("tables", {}).get(category["table"], {}).get("measures", [])
            if len(table_measures) == 1:
                measure = {"table": category["table"], "field": table_measures[0], "fieldType": "measure"}
        if not category:
            raise ValueError("I could not find a category for that chart. Name an uploaded field, such as 'region'.")
        if not measure:
            raise ValueError("I could not find a measure for that chart. Name an available measure.")
        if chart_type == "gauge":
            chart = {"type": "gauge", "value": measure}
        elif chart_type == "table":
            chart = {"type": "table", "rows": [category], "values": [measure]}
        elif chart_type in {"lineClusteredColumnComboChart", "scatterChart"}:
            candidates = metadata.get("tables", {}).get(measure["table"], {}).get("measures", [])
            secondary = next((name for name in candidates if name != measure["field"]), None)
            if not secondary:
                raise ValueError(f"A {chart_type} needs two available measures. Name the second measure to add it.")
            chart = {
                "type": chart_type,
                "category": category,
                "values": [measure, {"table": measure["table"], "field": secondary, "fieldType": "measure"}],
            }
        else:
            chart = {"type": chart_type, "category": category, "value": measure}
        if not any(
            visual.get("type") == chart_type
            and visual.get("category") == category
            and visual.get("value") == measure
            for visual in visuals
        ):
            visuals.append(chart)
            changes.append(f"Added {chart_type} for {measure['field']} by {category['field']}")

    if metadata and re.search(r"\b(?:add|include)\b.*\bslicers?\b", lowered):
        phrases = _slicer_field_phrases(instruction)
        if not phrases:
            raise ValueError("I could not find a column for that slicer. Name an uploaded field.")
        for phrase in phrases:
            field = _field(metadata, phrase, "column")
            if not field:
                raise ValueError(f"I could not find the uploaded column '{phrase}' for that slicer.")
            if not any(v.get("type") == "slicer" and v.get("field") == field for v in visuals):
                visuals.append({"type": "slicer", "field": field})
                changes.append(f"Added slicer for {field['field']}")

    if metadata and re.search(r"\b(?:remove|delete|drop)\b.*\bslicers?\b", lowered):
        phrases = _slicer_removal_field_phrases(instruction)
        if not phrases:
            raise ValueError("Name the uploaded field whose slicer you want to remove.")
        for phrase in phrases:
            field = _field(metadata, phrase, "column")
            if not field:
                raise ValueError(f"I could not find the uploaded column '{phrase}' for that slicer.")
            matching = [
                visual for visual in visuals
                if visual.get("type") == "slicer"
                and isinstance(visual.get("field"), dict)
                and str(visual["field"].get("table", "")).casefold() == field["table"].casefold()
                and str(visual["field"].get("field", "")).casefold() == field["field"].casefold()
            ]
            if not matching:
                raise ValueError(f"No slicer for '{field['field']}' exists in this report.")
            visuals[:] = [
                visual for visual in visuals
                if not (
                    visual.get("type") == "slicer"
                    and isinstance(visual.get("field"), dict)
                    and str(visual["field"].get("table", "")).casefold() == field["table"].casefold()
                    and str(visual["field"].get("field", "")).casefold() == field["field"].casefold()
                )
            ]
            changes.append(f"Removed slicer for {field['field']}")

    if metadata and re.search(r"\b(add|include)\b.*\b(kpi|card)\b", lowered):
        match = re.search(r"\b(?:kpi\s+card|card|kpi)\s+(?:for|of)?\s*(.+?)(?:\s+and\b|$)", instruction, re.I)
        measure = _field(metadata, match.group(1) if match else instruction, "measure")
        if not measure:
            raise ValueError("I could not find a measure for that KPI card. Name an available measure.")
        if not any(v.get("type") == "card" and v.get("value") == measure for v in visuals):
            visuals.append({"type": "card", "value": measure})
            changes.append(f"Added KPI card for {measure['field']}")

    charts = [
        index for index, visual in enumerate(visuals)
        if visual.get("type") not in {"slicer", "card", "multiRowCard", "kpi", "gauge", "pivotTable", "table"}
    ]
    if charts:
        target = next((i for i in charts if len(types) > 1 and visuals[i].get("type") == types[0]), charts[0])
        visual = visuals[target]
        if len(types) >= 2 and visual["type"] != types[-1]:
            changes.append(f"Visual {target + 1}: {visual['type']} → {types[-1]}")
            visual["type"] = types[-1]
        ranking = re.search(r"\b(top|bottom)\s+(\d{1,2})\b", lowered)
        if ranking:
            visual["ranking"] = {"direction": ranking.group(1), "count": int(ranking.group(2))}
            changes.append(f"Visual {target + 1}: show {ranking.group(1)} {ranking.group(2)} values in the preview")
    if len(visuals) > MAX_VISUALS_PER_PAGE:
        raise ValueError(f"This edit would exceed the {MAX_VISUALS_PER_PAGE}-visual dashboard limit")
    if not changes:
        raise ValueError("I could not safely map that edit. Name an uploaded field and the change you want, such as adding a slicer, KPI, or chart.")
    return revised, changes
