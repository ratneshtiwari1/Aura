from __future__ import annotations

import logging
import re
import time
from zipfile import ZipFile
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Any, Callable

from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter, range_boundaries

from automation.excel.models import ColumnProfile, SheetProfile, TableProfile, WorkbookProfile
from automation.policy import (
    CROSS_TAB_COMBINED_SERIES_FIELD,
    CROSS_TAB_METRIC_FIELD,
    CROSS_TAB_SEGMENT_FIELD,
    CROSS_TAB_SERIES_FIELD,
    CROSS_TAB_TIME_FIELD,
    CROSS_TAB_VALUE_FIELD,
)

logger = logging.getLogger("autobi.excel")
_CELL_REFERENCE = re.compile(r"(?<![A-Z0-9_])(\$?[A-Z]{1,3}\$?\d+)(?![A-Z0-9_])")
_EXTERNAL_REFERENCE = re.compile(r"\[[^\]]+\.(?:xlsx|xlsm|xlsb|xls)\]", re.IGNORECASE)
_FORMULA_FUNCTION = re.compile(r"\b([A-Z][A-Z0-9_.]*)\s*\(", re.IGNORECASE)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_UNSUPPORTED_FUNCTIONS = {"WEBSERVICE", "FILTERXML", "RTD", "CUBEVALUE", "CUBEMEMBER"}
_PERCENTAGE_VALUE = re.compile(r"^\(?\s*-?[\d,]+(?:\.\d+)?\s*%\s*\)?$")


class WorkbookAnalysisCancelled(Exception):
    """Raised when a workbook analysis is cancelled by its caller."""


def _ref_pattern(formula: str) -> str:
    redacted = _EXTERNAL_REFERENCE.sub("[EXTERNAL]", formula)
    redacted = re.sub(r'"(?:[^"]|"")*"', '"[TEXT]"', redacted)
    redacted = re.sub(r"(?<![A-Z0-9_])\d+(?:\.\d+)?%?", "[NUMBER]", redacted)
    return _CELL_REFERENCE.sub("[ROW]", redacted.upper())


def _formula_kinds(formula: str) -> list[str]:
    kinds = []
    upper = formula.upper()
    if re.search(r"\b(SUM|AVERAGE|COUNT|COUNTA|MIN|MAX|SUBTOTAL)\s*\(", upper):
        kinds.append("aggregation")
    if re.search(r"\b(IF|IFS|AND|OR|NOT)\s*\(", upper):
        kinds.append("conditional")
    if re.search(r"\b(VLOOKUP|HLOOKUP|XLOOKUP|LOOKUP|INDEX|MATCH)\s*\(", upper):
        kinds.append("lookup")
    if re.search(r"\b(DATE|TODAY|NOW|YEAR|MONTH|DAY|EDATE|EOMONTH)\s*\(", upper):
        kinds.append("date")
    if _EXTERNAL_REFERENCE.search(formula):
        kinds.append("external_reference")
    if re.search(r"(?:'[^']+'|[A-Z_][A-Z0-9_.]*)!", formula, re.IGNORECASE):
        kinds.append("cross_sheet_reference")
    if re.search(r"(?<![<>=])(?:\+|-|\*|/|\^)(?![=])", formula):
        kinds.append("arithmetic")
    return kinds or ["other"]


def _clean_header(value: Any, column_index: int) -> str:
    if value is None or str(value).strip() == "":
        return f"Column_{column_index}"
    return str(value).strip()


def _deduplicate_headers(headers: list[str]) -> list[str]:
    result = []
    seen: set[str] = set()
    for header in headers:
        name = header
        suffix = 2
        while name in seen:
            name = f"{header}_{suffix}"
            suffix += 1
        seen.add(name)
        result.append(name)
    return result


def _header_abbreviation(value: str) -> str:
    """Return the initials for a header label using only its meaningful words."""
    words = re.findall(r"[A-Za-z0-9]+", value)
    return "".join(word[0] for word in words).casefold()


def _is_redundant_header_level(candidate: str, prior_labels: list[str]) -> bool:
    """Avoid turning a parent header plus its abbreviation into a new field name.

    This is intentionally structural rather than domain-specific.  Grouped
    spreadsheets often put a full label in one header row and its initials in
    the next (for example, any multi-word label followed by its initials).
    Keeping both creates a field that no longer matches the logical schema.
    """
    compact = re.sub(r"[^A-Za-z0-9]+", "", candidate).casefold()
    return bool(compact) and any(
        compact == _header_abbreviation(label)
        for label in prior_labels
        if _header_abbreviation(label)
    )


def _flatten_header_path(labels: list[str]) -> str:
    """Combine grouped header levels into one stable, non-redundant name."""
    flattened: list[str] = []
    for label in labels:
        label = label.strip()
        if not label or label.startswith("Column_"):
            continue
        if label.casefold() in {existing.casefold() for existing in flattened}:
            continue
        if _is_redundant_header_level(label, flattened):
            continue
        flattened.append(label)
    return " ".join(flattened)


def _row_values(sheet: Any, row: int, min_column: int, max_column: int) -> list[Any]:
    return [sheet.cell(row, column).value for column in range(min_column, max_column + 1)]


def _percentage_value(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _PERCENTAGE_VALUE.fullmatch(text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    number_text = text.strip("() %").replace(",", "")
    try:
        number = float(number_text)
    except ValueError:
        return None
    return -number if negative else number


def _find_regions(sheet: Any) -> list[tuple[int, int, int, int]]:
    excluded = [
        range_boundaries(table.ref)
        for table in sheet.tables.values()
    ]
    occupied = {
        (cell.row, cell.column)
        for cell in sheet._cells.values()
        if cell.value is not None
        and not any(
            min_column <= cell.column <= max_column
            and min_row <= cell.row <= max_row
            for min_column, min_row, max_column, max_row in excluded
        )
    }
    if not occupied:
        return []
    columns_by_row: dict[int, set[int]] = defaultdict(set)
    for row, column in occupied:
        columns_by_row[row].add(column)
    active_rows = sorted(columns_by_row)
    row_groups: list[list[int]] = []
    for row in active_rows:
        if not row_groups or row > row_groups[-1][-1] + 1:
            row_groups.append([row])
        else:
            row_groups[-1].append(row)

    merged_groups: list[list[int]] = []
    for group in row_groups:
        if merged_groups and group[0] - merged_groups[-1][-1] == 2:
            previous_columns = {
                column
                for row in merged_groups[-1]
                for column in columns_by_row[row]
            }
            current_columns = {
                column
                for row in group
                for column in columns_by_row[row]
            }
            first_values = [
                sheet.cell(group[0], column).value
                for column in sorted(current_columns)
            ]
            header_like = bool(first_values) and all(
                isinstance(value, str) and not value.startswith("=")
                for value in first_values
            ) and len({str(value).casefold() for value in first_values}) == len(first_values)
            if (
                previous_columns == current_columns
                and not header_like
            ):
                merged_groups[-1].extend(group)
                continue
        merged_groups.append(group)

    regions = []
    for row_group in merged_groups:
        active_columns = sorted({
            column for row in row_group for column in columns_by_row[row]
        })
        column_groups: list[list[int]] = []
        for column in active_columns:
            if not column_groups or column > column_groups[-1][-1] + 1:
                column_groups.append([column])
            else:
                column_groups[-1].append(column)
        for column_group in column_groups:
            if len(row_group) < 2 and len(column_group) < 2:
                continue
            regions.append((
                row_group[0],
                column_group[0],
                row_group[-1],
                column_group[-1],
            ))
    return sorted(regions)


def _header_row(sheet: Any, bounds: tuple[int, int, int, int]) -> tuple[int, float]:
    min_row, min_column, max_row, max_column = bounds
    candidates = []
    for row_index in range(min_row, min(max_row, min_row + 11) + 1):
        values = _row_values(sheet, row_index, min_column, max_column)
        populated = [value for value in values if value is not None]
        if not populated:
            continue
        text_count = sum(isinstance(value, str) and not value.startswith("=") for value in populated)
        distinct_ratio = len({str(value).strip().casefold() for value in populated}) / len(populated)
        type_transition = 0
        below = [
            value
            for row in range(row_index + 1, min(max_row, row_index + 4) + 1)
            for value in _row_values(sheet, row, min_column, max_column)
            if value is not None
        ]
        if below:
            below_text_ratio = sum(isinstance(value, str) and not value.startswith("=") for value in below) / len(below)
            type_transition = int(below_text_ratio < (text_count / len(populated)))
        bold_count = sum(
            1 for column in range(min_column, max_column + 1)
            if sheet.cell(row_index, column).font.bold
        )
        fill_count = sum(
            1 for column in range(min_column, max_column + 1)
            if sheet.cell(row_index, column).fill.fill_type is not None
        )
        density = len(populated) / max_column_width(bounds)
        score = (
            0.42 * (text_count / len(populated))
            + 0.20 * distinct_ratio
            + 0.12 * density
            + 0.10 * min(bold_count / max(len(populated), 1), 1)
            + 0.06 * min(fill_count / max(len(populated), 1), 1)
            + 0.10 * type_transition
        )
        candidates.append((score, row_index))
    if not candidates:
        return min_row, 0.0
    score, row = max(candidates, key=lambda item: (item[0], -item[1]))
    while row < max_row:
        has_grouped_header = any(
            merged.min_row <= row <= merged.max_row
            and merged.max_col > merged.min_col
            and merged.min_col >= min_column and merged.max_col <= max_column
            and merged.max_col - merged.min_col + 1 < max_column_width(bounds)
            for merged in sheet.merged_cells.ranges
        )
        if not has_grouped_header and not _header_hierarchy(sheet, row, min_column, max_column, min_row):
            break
        child_values = [
            value
            for value in _row_values(sheet, row + 1, min_column, max_column)
            if value is not None
        ]
        if (
            len(child_values) >= max(2, max_column_width(bounds) // 2)
            and all(
                isinstance(value, (date, datetime))
                or isinstance(value, (int, float)) and not isinstance(value, bool) and _YEAR.fullmatch(str(value))
                or isinstance(value, str) and not value.startswith("=")
                and _percentage_value(value) is None
                and (not re.fullmatch(r"[+-]?\d[\d,.]*", value.strip()) or _YEAR.fullmatch(value.strip()))
                for value in child_values
            )
        ):
            row += 1
            score = min(score + 0.08, 0.99)
        else:
            break
    return row, round(min(score, 0.99), 2)


def max_column_width(bounds: tuple[int, int, int, int]) -> int:
    return max(bounds[3] - bounds[1] + 1, 1)


def _header_hierarchy(sheet: Any, header_row: int, min_column: int, max_column: int, min_row: int = 1) -> list[list[str]]:
    if header_row <= 1:
        return []
    hierarchy = []
    for row_index in range(header_row - 1, min_row - 1, -1):
        row = _row_values(sheet, row_index, min_column, max_column)
        populated = [value for value in row if value is not None]
        groups = [merged for merged in sheet.merged_cells.ranges
                  if merged.min_row <= row_index <= merged.max_row
                  and merged.min_col >= min_column and merged.max_col <= max_column
                  and merged.max_col > merged.min_col
                  and merged.max_col - merged.min_col + 1 < max_column - min_column + 1]
        if not populated or (not groups and (hierarchy or len(populated) < 2
                                             or not any(isinstance(value, str) for value in populated))):
            break
        labels = []
        for column in range(min_column, max_column + 1):
            value = sheet.cell(row_index, column).value
            for merged in groups:
                if merged.min_col <= column <= merged.max_col:
                    value = sheet.cell(merged.min_row, merged.min_col).value
                    break
            labels.append(str(value).strip() if value is not None else "")
        hierarchy.insert(0, labels)
    return hierarchy


def _number_format_clues(sheet: Any, column: int, start: int, end: int) -> str | None:
    formats = Counter(
        sheet.cell(row, column).number_format
        for row in range(start, end + 1)
        if sheet.cell(row, column).value is not None
    )
    return formats.most_common(1)[0][0] if formats else None


def _infer_type(
    values: list[Any],
    number_format: str | None,
    formula_count: int,
    formula_expressions: list[str] | None = None,
    percentage_column: bool = False,
) -> tuple[str, str, float]:
    populated = [value for value in values if value is not None]
    format_text = (number_format or "").casefold()
    if percentage_column:
        return "percentage", "measure", 0.9
    if formula_count:
        expression_text = " ".join(formula_expressions or []).upper()
        if any(token in format_text for token in ("yy", "dd", "hh")) or re.search(
            r"\b(DATE|TODAY|NOW|EDATE|EOMONTH)\s*\(",
            expression_text,
        ):
            return "datetime", "time", 0.75
        if "%" in format_text:
            return "percentage", "calculated_measure", 0.75
        if any(symbol in format_text for symbol in ("$", "€", "£", "¥")) or "currency" in format_text:
            return "currency", "calculated_measure", 0.75
        formula_kinds = {
            kind
            for expression in formula_expressions or []
            for kind in _formula_kinds(expression)
        }
        if formula_kinds & {"aggregation", "arithmetic"}:
            return "decimal", "calculated_measure", 0.62
        return "formula", "calculated_measure", 0.62
    if not populated:
        return "blank", "unknown", 0.5
    if all(isinstance(value, bool) for value in populated):
        return "boolean", "dimension", 0.98
    if all(isinstance(value, datetime) for value in populated):
        return "datetime", "time", 0.98
    if all(isinstance(value, date) for value in populated):
        return "date", "time", 0.98
    numeric = [value for value in populated if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if len(numeric) == len(populated):
        if "%" in format_text:
            return "percentage", "measure", 0.9
        if any(symbol in format_text for symbol in ("$", "€", "£", "¥")):
            return "currency", "measure", 0.9
        return ("integer" if all(float(value).is_integer() for value in numeric) else "decimal"), "measure", 0.9
    if len(populated) != len(numeric):
        if numeric:
            return "mixed", "unknown", 0.6
        normalized = [str(value).strip() for value in populated]
        if all(_YEAR.fullmatch(value) for value in normalized):
            return "integer", "time", 0.72
        if re.search(r"\b(id|key|code|number)\b", str(populated[0]), re.IGNORECASE):
            return "text", "dimension", 0.6
    return "text", "dimension", 0.8


def _column_profile(
    sheet: Any,
    name: str,
    original_headers: list[str],
    column: int,
    start_row: int,
    end_row: int,
    values: list[Any],
    percentage_column: bool = False,
) -> ColumnProfile:
    formulas = [
        sheet.cell(row, column).value
        for row in range(start_row, end_row + 1)
        if sheet.cell(row, column).data_type == "f"
    ]
    number_format = _number_format_clues(sheet, column, start_row, end_row)
    if percentage_column and number_format == "General":
        number_format = r"0.##\%"
    formula_expressions = [str(value) for value in formulas]
    cached_values = [
        value for value in values
        if value is not None and not (isinstance(value, str) and value.startswith("="))
    ]
    if formulas and cached_values:
        data_type, role, confidence = _infer_type(
            cached_values,
            number_format,
            0,
            percentage_column=percentage_column,
        )
        if role in {"measure", "calculated_measure"}:
            role = "calculated_measure"
    else:
        data_type, role, confidence = _infer_type(
            values,
            number_format,
            len(formulas),
            formula_expressions,
            percentage_column,
        )
    normalized_name = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
    numeric_year_values = [
        int(value)
        for value in values
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and float(value).is_integer()
    ]
    # A year-like numeric column is a time dimension, not an additive measure.
    # Use both its semantic header and its value shape so arbitrary numeric IDs
    # are not reclassified as dates.
    if (
        role == "measure"
        and re.search(r"(?:^| )(?:year|yr|fiscal year|calendar year)(?:$| )", normalized_name)
        and numeric_year_values
        and len(numeric_year_values) == len([value for value in values if value is not None])
        and all(1000 <= value <= 9999 for value in numeric_year_values)
    ):
        role, confidence = "time", 0.94
    geographic_names = {
        "address", "city", "country", "county", "latitude", "longitude",
        "postal code", "province", "region", "state", "zip", "zipcode",
    }
    if role in {"dimension", "identifier"} and normalized_name in geographic_names:
        role, confidence = "geographic", 0.76
    elif re.search(r"(?:^| )(?:id|key|code|identifier)(?:$| )", normalized_name):
        role, confidence = "identifier", 0.82
    elif role == "dimension" and len({str(value) for value in values if value is not None}) <= min(30, max(len(values) // 5, 3)):
        role, confidence = "category", 0.72
    formula_pattern = Counter(_ref_pattern(value) for value in formula_expressions).most_common(1)
    formula_pattern_counts = Counter(_ref_pattern(value) for value in formula_expressions)
    kinds = sorted({kind for formula in formula_expressions for kind in _formula_kinds(formula)})
    populated = [value for value in values if value is not None and not (isinstance(value, str) and value.startswith("="))]
    numeric = [float(value) for value in populated if isinstance(value, (int, float)) and not isinstance(value, bool)]
    dates = [value for value in populated if isinstance(value, (date, datetime))]
    distinct = len({str(value) for value in populated})
    stats: dict[str, Any] = {
        "null_count": len(values) - len(populated),
        "non_null_count": len(populated),
        "distinct_count": distinct,
        "uniqueness_ratio": round(distinct / len(populated), 4) if populated else 0.0,
    }
    if numeric:
        stats.update(min=min(numeric), max=max(numeric), average=round(mean(numeric), 6))
    if dates:
        date_values = sorted(value.isoformat() for value in dates)
        stats.update(min_date=date_values[0], max_date=date_values[-1])
    sample_values = list(dict.fromkeys(str(value)[:120] for value in populated[:5]))
    return ColumnProfile(
        name=name,
        original_headers=original_headers,
        data_type=data_type,
        role_candidate=role,
        confidence=confidence,
        formula_count=len(formulas),
        formula_pattern=formula_pattern[0][0] if formula_pattern else None,
        formula_patterns=[
            {
                "pattern": pattern,
                "count": count,
                "coverage": round(count / len(formula_expressions), 4),
            }
            for pattern, count in formula_pattern_counts.most_common()
        ],
        formula_kinds=kinds,
        number_format=number_format,
        profile=stats,
        sample_values=sample_values,
    )


def _table_from_range(
    sheet: Any,
    sheet_name: str,
    bounds: tuple[int, int, int, int],
    name: str,
    method: str,
    excel_table: Any = None,
    cached_sheet: Any = None,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[TableProfile, dict[str, Any]]:
    min_row, min_column, max_row, max_column = bounds
    if excel_table is not None:
        header_row = min_row
        confidence = 1.0
    else:
        header_row, confidence = _header_row(sheet, bounds)
    section_title = next(
        (
            str(sheet.cell(row, min_column).value).strip()
            for row in range(min_row, header_row)
            if sheet.cell(row, min_column).value is not None
            and isinstance(sheet.cell(row, min_column).value, str)
        ),
        None,
    )
    original_headers = [
        _clean_header(value, index)
        for index, value in enumerate(
            _row_values(sheet, header_row, min_column, max_column),
            start=min_column,
        )
    ]
    hierarchy = _header_hierarchy(sheet, header_row, min_column, max_column, min_row) if excel_table is None else []
    if hierarchy:
        original_headers = [
            _flatten_header_path([*(level[index] for level in hierarchy), header]) or header
            for index, header in enumerate(original_headers)
        ]
    headers = _deduplicate_headers(original_headers)
    data_start = header_row + 1
    totals_row = bool(
        getattr(excel_table, "totalsRowShown", False)
        or getattr(excel_table, "totalsRowCount", 0)
    ) if excel_table is not None else False
    if excel_table is None and max_row > data_start:
        last = _row_values(sheet, max_row, min_column, max_column)
        label = str(last[0] or "").strip().casefold()
        if label in {"total", "grand total"}:
            for col in range(min_column + 1, max_column + 1):
                letter = get_column_letter(col)
                expected = f"=SUM({letter}{data_start}:{letter}{max_row - 1})"
                formula = str(sheet.cell(max_row, col).value).replace("$", "").replace(" ", "").upper()
                if formula == expected:
                    totals_row = True
                    break
    data_end = max_row - (1 if totals_row else 0)
    merged_data = [merged for merged in sheet.merged_cells.ranges
                   if merged.min_row >= data_start and merged.max_row <= data_end
                   and merged.min_col >= min_column and merged.max_col <= max_column]
    filled_cells = {}
    for merged in merged_data:
        anchor = sheet.cell(merged.min_row, merged.min_col)
        # Only repeat vertical text labels. Numeric and horizontal merges are
        # ambiguous and must never silently duplicate measures.
        if merged.min_col == merged.max_col and isinstance(anchor.value, str) and anchor.data_type != "f":
            for row_index in range(merged.min_row + 1, merged.max_row + 1):
                filled_cells[(row_index, merged.min_col)] = anchor.value
    source_headers = [
        str(value).strip().casefold() if value is not None else ""
        for value in _row_values(sheet, header_row, min_column, max_column)
    ]
    rows: list[dict[str, Any]] = []
    formula_cells: list[dict[str, Any]] = []
    repeated_header_rows = []
    if on_progress:
        on_progress("reading_formula_results")
    for row in range(data_start, data_end + 1):
        row_values = _row_values(sheet, row, min_column, max_column)
        row_values = [filled_cells.get((row, min_column + index), value)
                      for index, value in enumerate(row_values)]
        if not any(value is not None for value in row_values):
            continue
        if [
            str(value).strip().casefold() if value is not None else ""
            for value in row_values
        ] == source_headers:
            repeated_header_rows.append(row)
            continue
        # A merged, full-width annotation is not a category or fact row.
        # Require both an explicit note label and the merged annotation shape;
        # ordinary labels such as "Notes" in a real table must remain data.
        annotation = sheet.cell(row, min_column)
        if (excel_table is None and isinstance(annotation.value, str)
            and re.match(r"^\s*(?:note|notes|source)\s*:", annotation.value, re.I)
            and all(value is None for value in row_values[1:])
            and any(merged.min_row == merged.max_row == row
                    and merged.min_col == min_column and merged.max_col >= max_column
                    for merged in sheet.merged_cells.ranges)):
            continue
        row_index = len(rows)
        for index, value in enumerate(row_values):
            if isinstance(value, str) and value.startswith("="):
                cached_value = (
                    cached_sheet.cell(row, min_column + index).value
                    if cached_sheet is not None
                    else None
                )
                formula_cells.append({
                    "row_index": row_index,
                    "column": headers[index],
                    "formula": value,
                    "cached_value": cached_value,
                })
        rows.append({headers[index]: value for index, value in enumerate(row_values)})
    percentage_columns: set[int] = set()
    for index, header in enumerate(headers):
        populated = [row[header] for row in rows if row[header] is not None]
        if populated and all(
            _percentage_value(value) is not None
            or isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in populated
        ) and any(_percentage_value(value) is not None for value in populated):
            percentage_columns.add(index)
            for row in rows:
                parsed = _percentage_value(row[header])
                if parsed is not None:
                    row[header] = parsed
    columns = []
    if on_progress:
        on_progress("profiling_columns")
    cached_by_cell = {
        (formula_cell["row_index"], formula_cell["column"]): formula_cell.get("cached_value")
        for formula_cell in formula_cells
    }
    for index, header in enumerate(headers):
        column = min_column + index
        values = [
            cached_by_cell.get((row_index, header), row[header])
            for row_index, row in enumerate(rows)
        ]
        percentage_column = index in percentage_columns
        columns.append(_column_profile(
            sheet,
            header,
            [original_headers[index]],
            column,
            data_start,
            data_end,
            values,
            percentage_column,
        ))
    table = TableProfile(
        name=name,
        sheet=sheet_name,
        section_title=section_title,
        range=f"{get_column_letter(min_column)}{min_row}:{get_column_letter(max_column)}{max_row}",
        start_row=min_row,
        end_row=max_row,
        start_column=min_column,
        end_column=max_column,
        header_row=header_row,
        data_start_row=data_start,
        data_end_row=data_end,
        headers=headers,
        header_hierarchy=hierarchy,
        row_count=len(rows),
        column_count=len(headers),
        confidence=confidence,
        detection_method=method,
        excel_table=excel_table is not None,
        totals_row=totals_row,
        calculated_columns=[
            str(column.name)
            for column in getattr(excel_table, "tableColumns", [])
            if getattr(column, "calculatedColumnFormula", None) is not None
        ],
        totals_columns=[
            str(column.name)
            for column in getattr(excel_table, "tableColumns", [])
            if getattr(column, "totalsRowFunction", None) or getattr(column, "totalsRowLabel", None)
        ],
        table_style=getattr(getattr(excel_table, "tableStyleInfo", None), "name", None),
        formula_cells=formula_cells,
        columns=columns,
        rows=rows,
        merged_data_ranges=[str(merged) for merged in merged_data],
        filled_merged_cell_count=len(filled_cells),
        review_reasons=(
            (["Header or table boundary detection has low confidence."] if excel_table is None and confidence < 0.7 else [])
            + (["Repeated headers inside the region may indicate adjoining tables; verify the table boundaries."]
               if excel_table is None and repeated_header_rows else [])
            + (["Merged data cells contain ambiguous values; verify the detected range and blank cells."]
               if any(merged.min_col != merged.max_col or not isinstance(sheet.cell(merged.min_row, merged.min_col).value, str)
                      or sheet.cell(merged.min_row, merged.min_col).data_type == "f" for merged in merged_data) else [])
        ),
    )
    region = {
        "region_id": name,
        "sheet": sheet_name,
        "range": table.range,
        "header_row": header_row,
        "data_start_row": data_start,
        "data_end_row": data_end,
        "row_count": len(rows),
        "column_count": len(headers),
        "confidence": confidence,
        "detection_method": method,
    }
    return table, region


def _derived_column(
    name: str,
    values: list[Any],
    data_type: str,
    role: str,
    number_format: str | None = None,
) -> ColumnProfile:
    populated = [value for value in values if value is not None]
    distinct = len({str(value) for value in populated})
    profile: dict[str, Any] = {
        "null_count": len(values) - len(populated),
        "non_null_count": len(populated),
        "distinct_count": distinct,
        "uniqueness_ratio": round(distinct / len(populated), 4) if populated else 0.0,
    }
    numeric = [
        float(value) for value in populated
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if numeric:
        profile.update(min=min(numeric), max=max(numeric), average=round(mean(numeric), 6))
    return ColumnProfile(
        name=name,
        original_headers=[name],
        data_type=data_type,
        role_candidate=role,
        confidence=0.85,
        number_format=number_format,
        profile=profile,
        sample_values=list(dict.fromkeys(str(value)[:120] for value in populated[:5])),
    )


def _resolved_rows(source: TableProfile) -> list[dict[str, Any]]:
    rows = [dict(row) for row in source.rows]
    for formula_cell in source.formula_cells:
        row_index = formula_cell["row_index"]
        if row_index < len(rows):
            rows[row_index][formula_cell["column"]] = formula_cell.get("cached_value")
    return rows


def _cross_tab_view(source: TableProfile) -> TableProfile | None:
    if not source.header_hierarchy or source.column_count < 3:
        return None
    parent_headers = source.header_hierarchy[0]
    label_column = source.columns[0]
    if label_column.data_type not in {"text", "mixed"}:
        return None
    value_columns = []
    for index, parent in enumerate(parent_headers):
        if index == 0 or not parent.strip():
            continue
        child_header = source.headers[index]
        child_label = child_header.removeprefix(parent).strip()
        year_match = re.fullmatch(r"(?:FY\s*)?((?:19|20)\d{2})", child_label, re.IGNORECASE)
        column = source.columns[index]
        if year_match and column.role_candidate in {"measure", "calculated_measure"}:
            value_columns.append((index, parent.strip(), int(year_match.group(1)), column))
    if len(value_columns) < 2:
        return None

    section = source.section_title or source.sheet
    records = []
    for row in _resolved_rows(source):
        metric = row.get(label_column.name)
        if metric is None or not str(metric).strip():
            continue
        for index, scenario, year, column in value_columns:
            value = row.get(column.name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            segment = str(section).strip()
            records.append({
                CROSS_TAB_SEGMENT_FIELD: segment,
                CROSS_TAB_SERIES_FIELD: scenario,
                CROSS_TAB_COMBINED_SERIES_FIELD: f"{scenario} | {segment}",
                CROSS_TAB_TIME_FIELD: year,
                CROSS_TAB_METRIC_FIELD: str(metric).strip(),
                CROSS_TAB_VALUE_FIELD: value,
            })
    if not records:
        return None

    headers = list(records[0])
    formats = {column.number_format for _, _, _, column in value_columns}
    value_format = next(iter(formats)) if len(formats) == 1 else None
    value_type = (
        "percentage" if value_format and "%" in value_format
        else "currency" if value_format and any(symbol in value_format for symbol in ("$", "€", "£", "¥"))
        else "decimal"
    )
    columns = [
        _derived_column(CROSS_TAB_SEGMENT_FIELD, [row[CROSS_TAB_SEGMENT_FIELD] for row in records], "text", "dimension"),
        _derived_column(CROSS_TAB_SERIES_FIELD, [row[CROSS_TAB_SERIES_FIELD] for row in records], "text", "category"),
        _derived_column(CROSS_TAB_COMBINED_SERIES_FIELD, [row[CROSS_TAB_COMBINED_SERIES_FIELD] for row in records], "text", "category"),
        _derived_column(CROSS_TAB_TIME_FIELD, [row[CROSS_TAB_TIME_FIELD] for row in records], "integer", "time"),
        _derived_column(CROSS_TAB_METRIC_FIELD, [row[CROSS_TAB_METRIC_FIELD] for row in records], "text", "category"),
        _derived_column(CROSS_TAB_VALUE_FIELD, [row[CROSS_TAB_VALUE_FIELD] for row in records], value_type, "measure", value_format),
    ]
    return TableProfile(
        name=f"{source.name}_ChartData",
        sheet=source.sheet,
        section_title=section,
        derived_from=source.name,
        semantic_fields={
            "segment": CROSS_TAB_SEGMENT_FIELD,
            "series": CROSS_TAB_SERIES_FIELD,
            "combinedSeries": CROSS_TAB_COMBINED_SERIES_FIELD,
            "legendComponents": [CROSS_TAB_SERIES_FIELD, CROSS_TAB_SEGMENT_FIELD],
            "time": CROSS_TAB_TIME_FIELD,
            "metric": CROSS_TAB_METRIC_FIELD,
            "value": CROSS_TAB_VALUE_FIELD,
        },
        range=source.range,
        start_row=1,
        end_row=len(records) + 1,
        start_column=1,
        end_column=len(headers),
        header_row=1,
        data_start_row=2,
        data_end_row=len(records) + 1,
        headers=headers,
        row_count=len(records),
        column_count=len(headers),
        confidence=source.confidence,
        detection_method="derived_cross_tab_view",
        columns=columns,
        rows=records,
    )


def _relationships(tables: list[TableProfile]) -> list[dict[str, Any]]:
    relationships = []
    values_by_column = {
        (table.name, column.name): {
            str(row[column.name])
            for row in _resolved_rows(table)
            if row.get(column.name) is not None
        }
        for table in tables
        for column in table.columns
    }
    for source in tables:
        for source_column in source.columns:
            source_values = values_by_column[(source.name, source_column.name)]
            if not source_values or source_column.data_type not in {"text", "integer"}:
                continue
            source_unique = source_column.profile.get("uniqueness_ratio", 0)
            for target in tables:
                if target.name == source.name:
                    continue
                for target_column in target.columns:
                    if target_column.name.casefold() != source_column.name.casefold():
                        continue
                    if target_column.data_type != source_column.data_type:
                        continue
                    target_values = values_by_column[(target.name, target_column.name)]
                    overlap = len(source_values & target_values) / max(len(source_values), 1)
                    target_unique = target_column.profile.get("uniqueness_ratio", 0)
                    if overlap < 0.5:
                        continue
                    if target_unique < source_unique:
                        continue
                    if target_unique == source_unique and source.name.casefold() < target.name.casefold():
                        continue
                    relationships.append({
                        "source_table": source.name,
                        "source_column": source_column.name,
                        "target_table": target.name,
                        "target_column": target_column.name,
                        "confidence": round(min(overlap * 0.7 + target_unique * 0.3, 0.99), 2),
                        "reason": "Matching column names, compatible types, and overlapping values.",
                    })
    return relationships


def _formula_dependencies(workbook: Any, tables: list[TableProfile]) -> list[dict[str, Any]]:
    dependencies = []
    observed: set[tuple[str, str, str, str]] = set()
    for source in tables:
        sheet = workbook[source.sheet]
        for source_index, column in enumerate(source.columns):
            source_column_index = source.start_column + source_index
            for row in range(source.data_start_row, source.data_end_row + 1):
                cell = sheet.cell(row, source_column_index)
                if cell.data_type != "f":
                    continue
                expression = str(cell.value)
                for reference in _CELL_REFERENCE.finditer(expression.upper()):
                    raw_coordinate = reference.group(1).replace("$", "")
                    reference_row, reference_column = coordinate_to_tuple(raw_coordinate)
                    prefix = expression[:reference.start()]
                    sheet_match = re.search(
                        r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_. ]*))!$",
                        prefix,
                    )
                    target_sheet_name = (
                        (sheet_match.group(1) or sheet_match.group(2))
                        if sheet_match
                        else source.sheet
                    )
                    for target in tables:
                        if target.sheet != target_sheet_name:
                            continue
                        if not (
                            target.start_row <= reference_row <= target.end_row
                            and target.start_column <= reference_column <= target.end_column
                        ):
                            continue
                        target_index = reference_column - target.start_column
                        if target_index < 0 or target_index >= len(target.headers):
                            continue
                        target_column = target.headers[target_index]
                        key = (source.name, column.name, target.name, target_column)
                        if key in observed or key[:2] == key[2:]:
                            continue
                        observed.add(key)
                        dependencies.append({
                            "table": source.name,
                            "column": column.name,
                            "depends_on_table": target.name,
                            "depends_on_column": target_column,
                            "cross_sheet": source.sheet != target.sheet,
                            "confidence": 0.8 if sheet_match else 0.65,
                            "reason": "Formula cell reference overlaps a detected table column.",
                        })
                for structured_column in re.findall(r"\[([^\[\]]+)\]", expression):
                    for target in tables:
                        if target.name.casefold() != source.name.casefold():
                            continue
                        target_column = next(
                            (header for header in target.headers if header.casefold() == structured_column.casefold()),
                            None,
                        )
                        if target_column:
                            key = (source.name, column.name, target.name, target_column)
                            if key not in observed and key[:2] != key[2:]:
                                observed.add(key)
                                dependencies.append({
                                    "table": source.name,
                                    "column": column.name,
                                    "depends_on_table": target.name,
                                    "depends_on_column": target_column,
                                    "cross_sheet": False,
                                    "confidence": 0.7,
                                    "reason": "Formula contains a structured table-column reference.",
                                })
    return dependencies


def _unsupported_features(path: Path, workbook: Any) -> list[dict[str, str]]:
    features = []
    if path.suffix.casefold() == ".xlsm":
        features.append({"feature": "vba_macros", "location": path.name, "severity": "warning"})
    if getattr(workbook, "_external_links", []):
        features.append({"feature": "external_workbook_links", "location": path.name, "severity": "warning"})
    try:
        with ZipFile(path) as archive:
            members = set(archive.namelist())
    except OSError:
        members = set()
    if any(name.startswith("xl/externalLinks/") for name in members) and not any(
        item["feature"] == "external_workbook_links" for item in features
    ):
        features.append({"feature": "external_workbook_links", "location": path.name, "severity": "warning"})
    if "xl/connections.xml" in members:
        features.append({"feature": "external_data_connections", "location": path.name, "severity": "warning"})
    for sheet in workbook.worksheets:
        if getattr(sheet, "_pivots", []):
            features.append({"feature": "pivot_table_cache", "location": sheet.title, "severity": "warning"})
        if getattr(sheet, "tables", None) is None:
            features.append({"feature": "unsupported_sheet_objects", "location": sheet.title, "severity": "warning"})
        if getattr(sheet, "_charts", []):
            features.append({"feature": "embedded_charts", "location": sheet.title, "severity": "warning"})
        if getattr(sheet, "_images", []):
            features.append({"feature": "embedded_images", "location": sheet.title, "severity": "warning"})
    return features


def _classify_sheet(name: str, tables: list[TableProfile]) -> tuple[str, float]:
    normalized = name.casefold()
    if any(word in normalized for word in ("instruction", "readme", "guide")):
        return "instructions", 0.85
    if any(word in normalized for word in ("dashboard", "summary", "overview", "kpi")):
        return "summary", 0.78
    if any(word in normalized for word in ("lookup", "reference", "mapping", "config", "helper")):
        return "lookup_reference", 0.75
    row_count = sum(table.row_count for table in tables)
    if tables and row_count > 100:
        return "raw_data", 0.7
    if tables:
        return "data", 0.65
    return "other", 0.5


class ExcelAnalyzer:
    """Extract workbook structure and formula expressions with available saved results."""

    def analyze(self, path: str | Path, on_progress: Callable[[str], None] | None = None) -> WorkbookProfile:
        file_path = Path(path)
        started = time.perf_counter()
        logger.info(
            "Workbook analysis started",
            extra={"event": "excel.analysis.started", "workbook": file_path.name, "operation": "analyze"},
        )
        try:
            if on_progress:
                on_progress("reading_workbook")
            workbook = load_workbook(file_path, read_only=False, data_only=False, keep_links=True)
            cached_workbook = load_workbook(file_path, read_only=False, data_only=True, keep_links=True)
        except WorkbookAnalysisCancelled:
            raise
        except Exception as exc:
            if "workbook" in locals():
                workbook.close()
            logger.exception(
                "Workbook analysis failed",
                extra={"event": "excel.analysis.failed", "workbook": file_path.name, "operation": "open"},
            )
            raise ValueError(f"Unable to open Excel workbook '{file_path.name}': {exc}") from exc
        try:
            unsupported = _unsupported_features(file_path, workbook)
            warnings = []
            if unsupported:
                warnings.extend(
                    f"Workbook feature '{item['feature']}' at '{item['location']}' may not be fully interpreted."
                    for item in unsupported
                )
            sheets = []
            all_tables = []
            formula_dependencies = []
            native_table_count = 0
            for order, sheet in enumerate(workbook.worksheets, start=1):
                if on_progress:
                    on_progress("detecting_tables")
                logger.info(
                    "Worksheet discovered",
                    extra={
                        "event": "excel.sheet.discovered",
                        "workbook": file_path.name,
                        "sheet": sheet.title,
                        "operation": "analyze_sheet",
                        "order": order,
                        "visibility": sheet.sheet_state,
                    },
                )
                regions = _find_regions(sheet)
                cached_sheet = cached_workbook[sheet.title]
                tables = []
                logical_regions = []
                used_native_bounds = set()
                for excel_table in sheet.tables.values():
                    min_column, min_row, max_column, max_row = range_boundaries(excel_table.ref)
                    bounds = (min_row, min_column, max_row, max_column)
                    table, region = _table_from_range(
                        sheet,
                        sheet.title,
                        bounds,
                        excel_table.displayName,
                        "native_excel_table",
                        excel_table,
                        cached_sheet,
                        on_progress,
                    )
                    tables.append(table)
                    logical_regions.append(region)
                    used_native_bounds.add(bounds)
                    native_table_count += 1
                for bounds in regions:
                    if bounds in used_native_bounds:
                        continue
                    range_label = (
                        f"{get_column_letter(bounds[1])}{bounds[0]}_"
                        f"{get_column_letter(bounds[3])}{bounds[2]}"
                    )
                    table, region = _table_from_range(
                        sheet,
                        sheet.title,
                        bounds,
                        sheet.title if len(regions) == 1 and not tables else f"{sheet.title}_{range_label}",
                        "heuristic_region",
                        cached_sheet=cached_sheet,
                        on_progress=on_progress,
                    )
                    if not table.rows or table.column_count < 2:
                        continue
                    tables.append(table)
                    logical_regions.append(region)
                    logger.info(
                        "Logical region discovered",
                        extra={
                            "event": "excel.region.discovered",
                            "workbook": file_path.name,
                            "sheet": sheet.title,
                            "operation": "detect_region",
                            "range": table.range,
                            "confidence": table.confidence,
                        },
                    )
                for table in tables:
                    if table.review_reasons:
                        warnings.extend(f"Table '{table.name}': {reason}" for reason in table.review_reasons)
                    all_tables.append(table)
                    logger.info(
                        "Workbook table discovered",
                        extra={
                            "event": "excel.table.discovered",
                            "workbook": file_path.name,
                            "sheet": sheet.title,
                            "operation": "profile_table",
                            "table": table.name,
                            "row_count": table.row_count,
                            "column_count": table.column_count,
                            "confidence": table.confidence,
                        },
                    )
                    for column in table.columns:
                        if column.formula_count:
                            patterns = Counter(
                                _ref_pattern(str(sheet.cell(row, table.start_column + table.headers.index(column.name)).value))
                                for row in range(table.data_start_row, table.data_end_row + 1)
                                if sheet.cell(row, table.start_column + table.headers.index(column.name)).data_type == "f"
                            )
                            formula = next(
                                (
                                    str(sheet.cell(row, table.start_column + table.headers.index(column.name)).value)
                                    for row in range(table.data_start_row, table.data_end_row + 1)
                                    if sheet.cell(row, table.start_column + table.headers.index(column.name)).data_type == "f"
                                ),
                                "",
                            )
                            if patterns:
                                column.formula_pattern = patterns.most_common(1)[0][0]
                analytical_views = [
                    view
                    for source_table in tables
                    if (view := _cross_tab_view(source_table)) is not None
                ]
                for view in analytical_views:
                    tables.append(view)
                    all_tables.append(view)
                    logger.info(
                        "Derived chart view discovered",
                        extra={
                            "event": "excel.table.discovered",
                            "workbook": file_path.name,
                            "sheet": sheet.title,
                            "operation": "derive_cross_tab_view",
                            "table": view.name,
                            "source_table": view.derived_from,
                            "row_count": view.row_count,
                            "column_count": view.column_count,
                            "confidence": view.confidence,
                        },
                    )
                source_tables = [table for table in tables if table.derived_from is None]
                formulas = []
                formula_columns = 0
                formula_cells: dict[int, list[str]] = defaultdict(list)
                for cell in sheet._cells.values():
                    if cell.data_type == "f":
                        formula_cells[cell.column].append(str(cell.value))
                formula_columns = sum(bool(values) for values in formula_cells.values())
                sheet_formula_count = sum(len(values) for values in formula_cells.values())
                for column_index, expressions in formula_cells.items():
                    patterns = Counter(_ref_pattern(expression) for expression in expressions)
                    formulas.append({
                        "column": get_column_letter(column_index),
                        "formula_count": len(expressions),
                        "formula_pattern": patterns.most_common(1)[0][0],
                        "formula_patterns": [
                            {
                                "pattern": pattern,
                                "count": count,
                                "coverage": round(count / len(expressions), 4),
                            }
                            for pattern, count in patterns.most_common()
                        ],
                        "functions": sorted({
                            function.upper()
                            for expression in expressions
                            for function in _FORMULA_FUNCTION.findall(expression)
                        }),
                        "formula_kinds": sorted({
                            kind for expression in expressions for kind in _formula_kinds(expression)
                        }),
                    })
                    unsupported_functions = sorted({
                        function.upper()
                        for expression in expressions
                        for function in _FORMULA_FUNCTION.findall(expression)
                        if function.upper() in _UNSUPPORTED_FUNCTIONS or function.upper().startswith("_XLFN.")
                    })
                    if any("_XLFN." in expression.upper() for expression in expressions):
                        unsupported_functions.append("XLFN_FUNCTION")
                    if unsupported_functions:
                        feature = {
                            "feature": "unsupported_formula_function",
                            "location": f"{sheet.title}!{get_column_letter(column_index)}",
                            "severity": "warning",
                        }
                        if feature not in unsupported:
                            unsupported.append(feature)
                    if any(_EXTERNAL_REFERENCE.search(expression) for expression in expressions):
                        feature = {
                            "feature": "external_formula_reference",
                            "location": f"{sheet.title}!{get_column_letter(column_index)}",
                            "severity": "warning",
                        }
                        if feature not in unsupported:
                            unsupported.append(feature)
                clues = []
                if sheet.conditional_formatting:
                    clues.append({
                        "type": "conditional_formatting",
                        "range_count": len(sheet.conditional_formatting),
                        "candidate": "visual_indicator",
                        "confidence": 0.5,
                    })
                for table in source_tables:
                    for column in table.columns:
                        if column.number_format and column.number_format != "General":
                            clues.append({
                                "range": f"{get_column_letter(table.start_column + table.headers.index(column.name))}{table.data_start_row}:{get_column_letter(table.start_column + table.headers.index(column.name))}{table.data_end_row}",
                                "number_format": column.number_format,
                                "candidate": "percentage" if "%" in column.number_format else "currency" if any(symbol in column.number_format for symbol in ("$", "€", "£", "¥")) else "formatted_value",
                                "confidence": 0.65,
                            })
                classification, classification_confidence = _classify_sheet(sheet.title, source_tables)
                populated_cells = [cell for cell in sheet._cells.values() if cell.value is not None]
                used_rows = [cell.row for cell in populated_cells]
                used_columns = [cell.column for cell in populated_cells]
                if not tables and sheet.sheet_state == "visible" and sheet.max_row and sheet.max_column:
                    warnings.append(f"Worksheet '{sheet.title}' has populated cells but no reliable tabular region was detected.")
                sheets.append(SheetProfile(
                    name=sheet.title,
                    order=order,
                    visibility=sheet.sheet_state,
                    classification=classification,
                    classification_confidence=classification_confidence,
                    used_range=sheet.calculate_dimension(),
                    min_row=sheet.min_row or 0,
                    max_row=sheet.max_row or 0,
                    min_column=sheet.min_column or 0,
                    max_column=sheet.max_column or 0,
                    row_count=max(used_rows) - min(used_rows) + 1 if used_rows else 0,
                    column_count=max(used_columns) - min(used_columns) + 1 if used_columns else 0,
                    merged_regions=[str(item) for item in sheet.merged_cells.ranges],
                    hidden_rows=[index for index, dimension in sheet.row_dimensions.items() if dimension.hidden],
                    hidden_columns=[
                        key for key, dimension in sheet.column_dimensions.items() if dimension.hidden
                    ],
                    freeze_panes=str(sheet.freeze_panes) if sheet.freeze_panes else None,
                    tables=tables,
                    logical_regions=logical_regions,
                    formula_count=sheet_formula_count,
                    formula_columns=formula_columns,
                    formulas=formulas,
                    formatting_clues=clues,
                    likely_title_rows=[
                        row for row in range(1, min(sheet.max_row or 0, 10) + 1)
                        if any(
                            isinstance(sheet.cell(row, column).value, str)
                            and sheet.cell(row, column).font.bold
                            for column in range(1, min(sheet.max_column or 0, 20) + 1)
                        )
                        and row not in [table.header_row for table in source_tables]
                    ],
                    likely_header_rows=[table.header_row for table in source_tables],
                ))
                if sheet_formula_count:
                    logger.info(
                        "Formula analysis completed",
                        extra={
                            "event": "excel.formula.analysis.completed",
                            "workbook": file_path.name,
                            "sheet": sheet.title,
                            "operation": "analyze_formulas",
                            "formula_count": sheet_formula_count,
                            "formula_column_count": formula_columns,
                        },
                    )
            source_tables = [table for table in all_tables if table.derived_from is None]
            formula_dependencies = _formula_dependencies(workbook, source_tables)
            relationships = _relationships(source_tables)
            total_formula_count = sum(sheet.formula_count for sheet in sheets)
            if total_formula_count:
                warnings.append(
                    f"Found {total_formula_count} formula cell(s). AURA uses saved formula results when available and does not recalculate formulas; saved results may be stale."
                )
                missing_formula_results = sum(
                    formula_cell.get("cached_value") is None
                    for table in source_tables
                    for formula_cell in table.formula_cells
                )
                if missing_formula_results:
                    warnings.append(
                        f"{missing_formula_results} formula cell(s) have no saved result and will be blank in the semantic model; their expressions are retained in source metadata."
                    )
            if relationships:
                logger.info(
                    "Relationship candidates detected",
                    extra={
                        "event": "excel.relationships.detected",
                        "workbook": file_path.name,
                        "operation": "detect_relationships",
                        "candidate_count": len(relationships),
                    },
                )
            if unsupported:
                logger.warning(
                    "Unsupported workbook features detected",
                    extra={
                        "event": "excel.unsupported.detected",
                        "workbook": file_path.name,
                        "operation": "detect_unsupported_features",
                        "feature_count": len(unsupported),
                    },
                )
            profile = WorkbookProfile(
                workbook={
                    "name": file_path.name,
                    "type": file_path.suffix.casefold().lstrip("."),
                    "sheet_count": len(workbook.worksheets),
                    "visible_sheets": [sheet.title for sheet in workbook.worksheets if sheet.sheet_state == "visible"],
                    "hidden_sheets": [sheet.title for sheet in workbook.worksheets if sheet.sheet_state == "hidden"],
                    "very_hidden_sheets": [sheet.title for sheet in workbook.worksheets if sheet.sheet_state == "veryHidden"],
                    "table_count": len(all_tables),
                    "native_table_count": native_table_count,
                    "analytical_view_count": sum(table.derived_from is not None for table in all_tables),
                    "logical_region_count": sum(len(sheet.logical_regions) for sheet in sheets),
                    "formula_count": total_formula_count,
                    "formula_column_count": sum(sheet.formula_columns for sheet in sheets),
                    "merged_region_count": sum(len(sheet.merged_regions) for sheet in sheets),
                    "potential_relationship_count": len(relationships),
                    "named_ranges": sorted(workbook.defined_names),
                },
                sheets=sheets,
                relationships=relationships,
                formula_dependencies=formula_dependencies,
                unsupported_features=unsupported,
                warnings=warnings,
                status="partial" if warnings or unsupported else "complete",
            )
            logger.info(
                "Workbook profile generation completed",
                extra={
                    "event": "excel.profile.completed",
                    "workbook": file_path.name,
                    "operation": "generate_profile",
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "sheet_count": len(sheets),
                    "table_count": len(all_tables),
                    "formula_count": total_formula_count,
                    "relationship_count": len(relationships),
                    "status": profile.status,
                },
            )
            return profile
        except WorkbookAnalysisCancelled:
            raise
        except Exception:
            logger.exception(
                "Workbook profile generation failed",
                extra={
                    "event": "excel.analysis.failed",
                    "workbook": file_path.name,
                    "operation": "analyze",
                },
            )
            raise
        finally:
            workbook.close()
            cached_workbook.close()
