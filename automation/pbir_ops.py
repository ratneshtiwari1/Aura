import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


def _report_directory(report_path: str, root: Path = WORKSPACE_ROOT) -> Path:
    workspace_root = root.resolve()
    report_dir = (workspace_root / report_path).resolve()
    if report_dir == workspace_root or workspace_root not in report_dir.parents:
        raise ValueError("Report path must point inside the configured workspace")
    if not (report_dir / "definition" / "report.json").is_file():
        raise FileNotFoundError(f"PBIR report definition not found: {report_path}")
    return report_dir


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _page_file(definition_dir: Path, page_name: str) -> Path:
    if not isinstance(page_name, str) or page_name in {".", ".."} or "/" in page_name or "\\" in page_name:
        raise ValueError("Page ID must be a single directory name")
    pages_dir = (definition_dir / "pages").resolve()
    page_file = (pages_dir / page_name / "page.json").resolve()
    if pages_dir not in page_file.parents:
        raise ValueError("Page path must remain inside the report pages directory")
    return page_file


def inspect_report(report_path: str, root: Path = WORKSPACE_ROOT) -> dict[str, Any]:
    report_dir = _report_directory(report_path, root)
    definition_dir = report_dir / "definition"
    report = _read_json(definition_dir / "report.json")
    pages_metadata = _read_json(definition_dir / "pages" / "pages.json")
    pages = []

    for page_name in pages_metadata.get("pageOrder", []):
        page_file = _page_file(definition_dir, page_name)
        page = _read_json(page_file)
        visuals_dir = page_file.parent / "visuals"
        visual_count = sum(1 for _ in visuals_dir.rglob("visual.json")) if visuals_dir.exists() else 0
        pages.append(
            {
                "name": page["name"],
                "displayName": page.get("displayName"),
                "width": page.get("width"),
                "height": page.get("height"),
                "visualCount": visual_count,
            }
        )

    return {"reportPath": report_path, "settings": report.get("settings", {}), "pages": pages}


def update_page_metadata(
    report_path: str,
    page_name: str,
    display_name: str,
    width: int,
    height: int,
    root: Path = WORKSPACE_ROOT,
) -> dict[str, Any]:
    if not display_name.strip():
        raise ValueError("Page display name cannot be empty")
    if not 320 <= width <= 4096 or not 320 <= height <= 4096:
        raise ValueError("Page width and height must be between 320 and 4096 pixels")

    report_dir = _report_directory(report_path, root)
    definition_dir = report_dir / "definition"
    pages_metadata = _read_json(definition_dir / "pages" / "pages.json")
    if page_name not in pages_metadata.get("pageOrder", []):
        raise ValueError(f"Page is not listed in this report: {page_name}")

    page_file = _page_file(definition_dir, page_name)
    page = _read_json(page_file)
    page.update({"displayName": display_name.strip(), "width": width, "height": height})

    with NamedTemporaryFile("w", encoding="utf-8", dir=page_file.parent, delete=False) as file:
        json.dump(page, file, ensure_ascii=True, indent=2)
        file.write("\n")
        temporary_path = Path(file.name)
    os.replace(temporary_path, page_file)

    return {"name": page["name"], "displayName": page["displayName"], "width": width, "height": height}