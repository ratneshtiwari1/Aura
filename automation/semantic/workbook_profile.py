from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from automation.excel import ExcelAnalyzer, WorkbookProfile

logger = logging.getLogger("autobi.excel")


def build_workbook_profile(
    file_path: str | Path,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for callers of the workbook intelligence engine."""
    del metadata
    return ExcelAnalyzer().analyze(file_path).to_dict()


def profile_for_ai(profile: dict[str, Any] | WorkbookProfile) -> dict[str, Any]:
    """Build a serializable profile without raw sample values for AI providers."""
    result = profile.to_dict() if isinstance(profile, WorkbookProfile) else profile
    ai_profile = {
        **result,
        "sheets": [
            {
                **sheet,
                "tables": [
                    {
                        **table,
                        "columns": [
                            {key: value for key, value in column.items() if key != "sample_values"}
                            for column in table.get("columns", [])
                        ],
                    }
                    for table in sheet.get("tables", [])
                ],
            }
            for sheet in result.get("sheets", [])
        ],
    }
    workbook = ai_profile.get("workbook", {})
    if isinstance(workbook, str):
        workbook = {"name": workbook}
    logger.info(
        "AI workbook profile payload generated",
        extra={
            "event": "excel.ai_payload.generated",
            "workbook": workbook.get("name", ""),
            "operation": "build_ai_payload",
            "sheet_count": workbook.get("sheet_count", len(ai_profile.get("sheets", []))),
            "table_count": workbook.get("table_count", 0),
            "formula_count": workbook.get("formula_count", 0),
        },
    )
    return ai_profile
