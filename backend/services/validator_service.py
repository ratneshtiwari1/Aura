from pathlib import Path
from typing import Any

from automation.report_automation import validate_pbir


def validate_generated_report(report_path: str | Path, metadata: dict[str, Any] | None = None, root: Path | None = None) -> dict[str, Any]:
    return validate_pbir(report_path, metadata, root or Path.cwd())
