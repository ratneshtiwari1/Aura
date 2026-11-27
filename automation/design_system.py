from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any


_DAR_LIGHT_THEME_PATH = Path(__file__).resolve().parents[1] / "theme" / "DAR_PowerBI_Light.json"

THEME_PRESETS: dict[str, dict[str, Any]] = {
    "dar_light": {
        "primary": "#F40000",
        "accent": "#F40000",
        "background": "#FFFFFF",
        "cardBackground": "#FAF7F0",
        "text": "#1A1A1A",
        "mutedText": "#6B6B6E",
        "gridline": "#E5E5E5",
        "border": "#E5E5E5",
        "fontFamily": "'TCCC Unity Regular', 'Segoe UI', wf_segoe-ui_normal, helvetica, arial, sans-serif",
        "titleFontFamily": "'TCCC Unity Head Bold', 'Segoe UI Bold', wf_segoe-ui_bold, helvetica, arial, sans-serif",
        "chartColors": [
            "#F40000", "#EDD382", "#A1C181", "#C89FA3", "#880000", "#A08E56",
            "#6F8558", "#97686E", "#FF8675", "#6B6B6E", "#C8C8C8", "#F2F3AE",
        ],
    },
    "executive": {
        "primary": "#F50912",
        "accent": "#FB9714",
        "background": "#F3F3F3",
        "cardBackground": "#FFFFFF",
        "text": "#383838",
        "mutedText": "#666666",
        "gridline": "#E7E7E7",
        "border": "#E5E5E5",
        "fontFamily": "Segoe UI",
        "chartColors": ["#F50912", "#FB9714", "#D7D18C", "#70C900", "#6E767A", "#CECECE"],
    },
    "corporate": {
        "primary": "#145DA0",
        "accent": "#2E8BC0",
        "background": "#F3F6F9",
        "cardBackground": "#FFFFFF",
        "text": "#263746",
        "mutedText": "#647587",
        "gridline": "#E1E8EF",
        "border": "#D7E0E8",
        "fontFamily": "Segoe UI",
        "chartColors": ["#145DA0", "#2E8BC0", "#57A773", "#F2B134", "#815AC0", "#D66853"],
    },
    "finance": {
        "primary": "#123B4A",
        "accent": "#168C82",
        "background": "#F1F5F4",
        "cardBackground": "#FFFFFF",
        "text": "#203538",
        "mutedText": "#617575",
        "gridline": "#DDE7E5",
        "border": "#D7E2E0",
        "fontFamily": "Segoe UI",
        "chartColors": ["#123B4A", "#168C82", "#64A59A", "#D4A23A", "#6C7A89", "#A7B7B2"],
    },
    "modern": {
        "primary": "#6654C0",
        "accent": "#16A6A1",
        "background": "#F5F4FA",
        "cardBackground": "#FFFFFF",
        "text": "#302D3D",
        "mutedText": "#716D80",
        "gridline": "#E8E5F0",
        "border": "#E2DEEB",
        "fontFamily": "Segoe UI",
        "chartColors": ["#6654C0", "#16A6A1", "#E77A58", "#D6A438", "#4A84C5", "#9A7CC4"],
    },
    "minimal": {
        "primary": "#414A4C",
        "accent": "#78909C",
        "background": "#F5F5F4",
        "cardBackground": "#FFFFFF",
        "text": "#292D2E",
        "mutedText": "#6C7476",
        "gridline": "#E7E8E7",
        "border": "#E4E5E4",
        "fontFamily": "Segoe UI",
        "chartColors": ["#414A4C", "#78909C", "#9BAA9C", "#C5A66A", "#7A8791", "#BCC3C1"],
    },
    "dark": {
        "primary": "#69B7FF",
        "accent": "#55D6BE",
        "background": "#171B22",
        "cardBackground": "#222832",
        "text": "#F0F3F6",
        "mutedText": "#AAB4C0",
        "gridline": "#39424E",
        "border": "#3B4653",
        "fontFamily": "Segoe UI",
        "chartColors": ["#69B7FF", "#55D6BE", "#F2B36F", "#C19BFF", "#FF8296", "#A9BACB"],
    },
}

LAYOUTS = {
    "kpi_4_plus_2x2",
    "kpi_grid_4_plus_2x2",
    "kpi_trend_comparison",
    "analytics_dashboard",
    "compact_dashboard",
}
VISUAL_STYLES = {"minimal", "standard", "data_dense"}
KPI_STYLES = {"accent_card", "accent_strip", "clean", "outlined"}
SLICER_STYLES = {"compact", "horizontal", "date"}
ICON_IDS = {
    "globe", "finance", "chart", "building", "product", "customer",
    "calendar", "location", "shopping", "currency",
}
ICON_GLYPHS = {
    "globe": "◉",
    "finance": "$",
    "chart": "▥",
    "building": "▤",
    "product": "◇",
    "customer": "○",
    "calendar": "□",
    "location": "⌖",
    "shopping": "▱",
    "currency": "$",
}
_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")
_COLOR_KEYS = {
    "primary", "accent", "background", "cardBackground", "text",
    "mutedText", "gridline", "border",
}

THEME_IMPORT_VERSION = {"visual": "2.12.0", "report": "3.4.0", "page": "2.3.1"}


def register_custom_theme(report_dir: Path, report_definition: dict[str, Any], theme: dict[str, Any]) -> None:
    theme_content = json.dumps(theme, sort_keys=True, ensure_ascii=False).encode("utf-8")
    filename = f"{theme['name']}-{hashlib.sha256(theme_content).hexdigest()[:12]}.json"
    resource_dir = report_dir / "StaticResources" / "RegisteredResources"
    resource_dir.mkdir(parents=True, exist_ok=True)
    embedded_theme = {**theme, "name": filename}
    (resource_dir / filename).write_text(json.dumps(embedded_theme, indent=2) + "\n", encoding="utf-8")

    report_definition.setdefault("themeCollection", {})["customTheme"] = {
        "name": filename,
        "reportVersionAtImport": dict(THEME_IMPORT_VERSION),
        "type": "RegisteredResources",
    }
    packages = report_definition.setdefault("resourcePackages", [])
    package = next((p for p in packages if p.get("type") == "RegisteredResources" and p.get("name") == "RegisteredResources"), None)
    if package is None:
        package = {"name": "RegisteredResources", "type": "RegisteredResources", "items": []}
        packages.append(package)
    package["items"] = [item for item in package.get("items", []) if item.get("type") != "CustomTheme"]
    package["items"].append({"name": filename, "path": filename, "type": "CustomTheme"})


DEFAULT_DESIGN = {
    "theme": "dar_light",
    "layout": "kpi_4_plus_2x2",
    "visual_style": "minimal",
    "kpi_style": "clean",
    "slicer_style": "compact",
    "show_variance": True,
    "show_icons": True,
    "auto_kpi_cards": False,
    # Keep an explicitly requested visual list intact.  Time slicers can be
    # enabled deliberately in a dashboard design, rather than being appended
    # without being requested.
    "auto_time_slicers": False,
}


def normalize_design_spec(spec: dict[str, Any]) -> dict[str, Any]:
    raw = spec.get("design", {})
    if not isinstance(raw, dict):
        raise ValueError("Report design must be a JSON object")
    design = {**DEFAULT_DESIGN, **raw}
    if not isinstance(design["theme"], str) or design["theme"] not in THEME_PRESETS:
        raise ValueError(f"Unsupported theme preset: {design['theme']}")
    if not isinstance(design["layout"], str) or design["layout"] not in LAYOUTS:
        raise ValueError(f"Unsupported dashboard layout: {design['layout']}")
    if not isinstance(design["visual_style"], str) or design["visual_style"] not in VISUAL_STYLES:
        raise ValueError(f"Unsupported visual style: {design['visual_style']}")
    if not isinstance(design["kpi_style"], str) or design["kpi_style"] not in KPI_STYLES:
        raise ValueError(f"Unsupported KPI style: {design['kpi_style']}")
    if not isinstance(design["slicer_style"], str) or design["slicer_style"] not in SLICER_STYLES:
        raise ValueError(f"Unsupported slicer style: {design['slicer_style']}")
    for flag in ("show_variance", "show_icons", "auto_kpi_cards", "auto_time_slicers"):
        if not isinstance(design[flag], bool):
            raise ValueError(f"Design option '{flag}' must be boolean")
    overrides = design.get("colors", {})
    if not isinstance(overrides, dict) or set(overrides) - _COLOR_KEYS:
        raise ValueError("Design colors must use supported color keys")
    if any(not isinstance(color, str) or not _HEX_COLOR.fullmatch(color) for color in overrides.values()):
        raise ValueError("Design colors must be six-digit hex colors")
    icons = spec.get("visuals", [])
    for visual in icons:
        if isinstance(visual, dict) and "icon" in visual and (
            not isinstance(visual["icon"], str) or visual["icon"] not in ICON_IDS
        ):
            raise ValueError(f"Unsupported icon identifier: {visual['icon']}")
    return design


def build_theme(design: dict[str, Any]) -> dict[str, Any]:
    preset = THEME_PRESETS[design["theme"]]
    colors = {**preset, **design.get("colors", {})}
    if design["theme"] == "dar_light":
        theme = json.loads(_DAR_LIGHT_THEME_PATH.read_text(encoding="utf-8"))
        theme["dataColors"][0] = colors["primary"]
        theme["background"] = colors["background"]
        theme["backgroundLight"] = colors["cardBackground"]
        theme["backgroundNeutral"] = colors["border"]
        theme["foreground"] = colors["text"]
        theme["foregroundNeutralSecondary"] = colors["mutedText"]
        theme["tableAccent"] = colors["primary"]
        for text_class in ("callout", "title", "header"):
            theme["textClasses"][text_class]["color"] = colors["text"]
        theme["textClasses"]["label"]["color"] = colors["mutedText"]
        return theme
    color_text = colors["text"]
    title_size = 12 if design["visual_style"] == "data_dense" else 14
    return {
        "name": "AURA",
        "dataColors": preset["chartColors"],
        "background": colors["background"],
        "backgroundLight": colors["cardBackground"],
        "backgroundNeutral": colors["border"],
        "foreground": color_text,
        "foregroundNeutralSecondary": colors["mutedText"],
        "tableAccent": colors["primary"],
        "good": "#70C900" if design["theme"] == "executive" else "#2E9D67",
        "neutral": colors["accent"],
        "bad": "#F50912" if design["theme"] == "executive" else "#D64550",
        "textClasses": {
            "callout": {"fontSize": 27, "fontFace": f"{preset['fontFamily']} Semibold", "color": color_text},
            "title": {"fontSize": title_size, "fontFace": f"{preset['fontFamily']} Semibold", "color": color_text},
            "header": {"fontSize": 12, "fontFace": f"{preset['fontFamily']} Semibold", "color": color_text},
            "label": {"fontSize": 10, "fontFace": preset["fontFamily"], "color": color_text},
        },
        "visualStyles": {
            "*": {
                "*": {
                    "categoryAxis": [{
                        "showAxisTitle": False,
                        "gridlineStyle": "solid",
                        "gridlineColor": {"solid": {"color": colors["gridline"]}},
                        "fontSize": 9,
                        "labelColor": {"solid": {"color": colors["mutedText"]}},
                    }],
                    "valueAxis": [{
                        "showAxisTitle": False,
                        "gridlineStyle": "solid",
                        "gridlineColor": {"solid": {"color": colors["gridline"]}},
                        "fontSize": 9,
                        "labelColor": {"solid": {"color": colors["mutedText"]}},
                    }],
                    "lineStyles": [{"strokeWidth": 2}],
                    "labels": [{"show": False, "fontSize": 10}],
                    "title": [{"show": True, "fontSize": title_size, "titleWrap": True}],
                    "background": [{"show": True, "transparency": 100}],
                    "border": [{"show": False, "width": 0}],
                    "legend": [{"show": False, "position": "Bottom", "showTitle": False}],
                },
            },
            "slicer": {
                "*": {
                    "background": [{"show": False, "transparency": 100}],
                    "border": [{"show": False, "width": 0}],
                    "dropdown": [{
                        "borderColor": {"solid": {"color": colors["accent"]}},
                        "accentBarShow": True,
                        "borderRadius": 6,
                    }],
                    "header": [{"fontSize": 10, "fontColor": {"solid": {"color": colors["mutedText"]}}}],
                },
            },
        },
    }


def layout_positions(
    visuals: list[dict[str, Any]],
    layout: str,
    content_top: int = 88,
) -> tuple[list[dict[str, int]], int, int]:
    margin, gap = 36, 20
    width = 1920
    slicers = [i for i, visual in enumerate(visuals) if visual.get("type") == "slicer"]
    cards = [i for i, visual in enumerate(visuals) if visual.get("type") == "card"]
    charts = [i for i, visual in enumerate(visuals) if visual.get("type") not in {"slicer", "card"}]
    positions: list[dict[str, int] | None] = [None] * len(visuals)
    y = content_top
    if slicers:
        height = 84 if layout != "compact_dashboard" else 76
        columns = min(len(slicers), 4)
        cell_width = (width - margin * 2 - gap * (columns - 1)) // columns
        group_width = cell_width * columns + gap * (columns - 1)
        start_x = (width - group_width) // 2
        for order, index in enumerate(slicers):
            row, column = divmod(order, columns)
            positions[index] = {
                "x": start_x + column * (cell_width + gap),
                "y": y + row * (height + gap),
                "width": cell_width,
                "height": height,
            }
        rows = (len(slicers) + columns - 1) // columns
        y += rows * height + max(rows - 1, 0) * gap + gap
    if cards:
        # DAR cardVisual uses 20px padding plus label, value, and optional
        # reference-label rows.  Give every executive KPI enough room for it.
        height = 180 if layout != "compact_dashboard" else 132
        columns = min(len(cards), 4)
        _place_group(positions, cards, width, y, height, columns, margin, gap)
        y += _group_height(len(cards), columns, height, gap) + gap
    if charts:
        columns = 3 if layout == "analytics_dashboard" and len(charts) > 4 else 2
        columns = min(columns, len(charts))
        rows = (len(charts) + columns - 1) // columns
        if rows <= 2:
            height = max(240, (1080 - margin - y - gap * (rows - 1)) // rows)
            page_height = max(
                1080,
                y + rows * height + gap * (rows - 1) + margin,
            )
        else:
            height = 320 if layout != "compact_dashboard" else 260
            page_height = y + rows * height + gap * (rows - 1) + margin
        _place_group(positions, charts, width, y, height, columns, margin, gap)
    else:
        page_height = max(720, y + margin)
    page_height = max(1080, page_height)
    complete = [position for position in positions if position is not None]
    return complete, width, page_height


def _group_height(count: int, columns: int, height: int, gap: int) -> int:
    rows = (count + columns - 1) // columns
    return rows * height + max(rows - 1, 0) * gap


def _place_group(
    positions: list[dict[str, int] | None],
    indices: list[int],
    canvas_width: int,
    y: int,
    height: int,
    columns: int,
    margin: int,
    gap: int,
) -> None:
    cell_width = (canvas_width - margin * 2 - gap * (columns - 1)) // columns
    for order, index in enumerate(indices):
        row, column = divmod(order, columns)
        positions[index] = {
            "x": margin + column * (cell_width + gap),
            "y": y + row * (height + gap),
            "width": cell_width,
            "height": height,
        }
