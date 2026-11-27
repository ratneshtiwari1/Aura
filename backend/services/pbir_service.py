import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from automation.design_system import DEFAULT_DESIGN, THEME_IMPORT_VERSION, build_theme, register_custom_theme
from automation.report_automation import generate_pbir


def create_pbir_project(report_path: str | Path, report_spec: dict[str, Any], metadata: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    return generate_pbir(report_path, report_spec, metadata, root)


def ensure_report_layout(report_dir: Path, model_relative_path: str = "../Dataset.SemanticModel") -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / ".platform").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {
            "type": "Report",
            "displayName": report_dir.name.removesuffix(".Report"),
        },
        "config": {"version": "2.0", "logicalId": str(uuid4())},
    }, indent=2) + "\n", encoding="utf-8")
    (report_dir / "definition").mkdir(exist_ok=True)
    (report_dir / "definition" / "pages").mkdir(exist_ok=True)
    theme_dir = report_dir / "StaticResources" / "SharedResources" / "BaseThemes"
    theme_dir.mkdir(parents=True, exist_ok=True)
    theme = build_theme(DEFAULT_DESIGN)
    theme_name = theme["name"]
    (theme_dir / f"{theme_name}.json").write_text(
        json.dumps(theme, indent=2) + "\n",
        encoding="utf-8",
    )
    (report_dir / "definition.pbir").write_text(
        json.dumps({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {"byPath": {"path": model_relative_path.replace("\\", "/")}},
        }, indent=2),
        encoding="utf-8",
    )
    report_definition = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.3.0/schema.json",
        "themeCollection": {
            "baseTheme": {
                "name": theme_name,
                "reportVersionAtImport": dict(THEME_IMPORT_VERSION),
                "type": "SharedResources",
            },
        },
        "objects": {
            "section": [{
                "properties": {
                    "verticalAlignment": {"expr": {"Literal": {"Value": "'Top'"}}},
                },
            }],
        },
        "resourcePackages": [{
            "name": "SharedResources",
            "type": "SharedResources",
            "items": [{
                "name": theme_name,
                "path": f"BaseThemes/{theme_name}.json",
                "type": "BaseTheme",
            }],
        }],
        "settings": {
            "useStylableVisualContainerHeader": True,
            "exportDataMode": "AllowSummarized",
            "defaultDrillFilterOtherVisuals": True,
            "allowChangeFilterTypes": True,
            "useEnhancedTooltips": True,
            "useDefaultAggregateDisplayName": True,
        },
    }
    register_custom_theme(report_dir, report_definition, theme)
    (report_dir / "definition" / "report.json").write_text(json.dumps(report_definition, indent=2), encoding="utf-8")
    (report_dir / "definition" / "version.json").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0",
    }, indent=2), encoding="utf-8")
    (report_dir / "definition" / "pages" / "pages.json").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json",
        "pageOrder": [],
        "activePageName": "",
    }, indent=2), encoding="utf-8")
