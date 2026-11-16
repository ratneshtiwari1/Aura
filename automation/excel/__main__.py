from __future__ import annotations

import argparse
import sys
from pathlib import Path

from automation.excel import ExcelAnalyzer


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze an Excel workbook and print its JSON profile.")
    parser.add_argument("workbook", type=Path, help="Path to an .xlsx or .xlsm workbook")
    args = parser.parse_args()
    try:
        profile = ExcelAnalyzer().analyze(args.workbook)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(profile.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
