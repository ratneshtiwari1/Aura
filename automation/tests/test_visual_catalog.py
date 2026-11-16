"""Coverage for the extended native Power BI visual catalog."""

import pytest

from automation.report_automation import create_report_spec, validate_report_spec


METADATA = {
    "tables": {
        "Sales": {
            "columns": ["Region", "City"],
            "measures": ["Total Revenue", "Total Units"],
        }
    }
}


@pytest.mark.parametrize(
    ("prompt", "visual_type"),
    [
        ("Create a sales dashboard showing total revenue by region as a waterfall chart", "waterfallChart"),
        ("Create a sales dashboard showing total revenue by region as a funnel chart", "funnel"),
        ("Create a sales dashboard showing total revenue by region as a treemap", "treemap"),
        ("Create a sales dashboard showing total revenue by region as a map", "map"),
        ("Create a sales dashboard showing total revenue by region as a filled map", "filledMap"),
        ("Create a sales dashboard showing total revenue by region as a ribbon chart", "ribbonChart"),
        ("Create a sales dashboard showing total revenue by region as a decomposition tree", "decompositionTree"),
        ("Create a sales dashboard showing total revenue by region as a stacked column chart", "stackedColumnChart"),
        ("Create a sales dashboard showing total revenue by region as a 100% stacked bar chart", "hundredPercentStackedBarChart"),
        ("Create a gauge for total revenue", "gauge"),
        ("Create a KPI for total revenue", "kpi"),
        ("Create a multi-row card for total revenue", "multiRowCard"),
        ("Create a table showing total revenue by region", "table"),
    ],
)
def test_extended_visuals_are_planned_and_validated(prompt: str, visual_type: str) -> None:
    spec = create_report_spec(prompt, METADATA)

    assert spec["visuals"][0]["type"] == visual_type
    validate_report_spec(spec, METADATA)


@pytest.mark.parametrize("visual_type", ["lineClusteredColumnComboChart", "scatterChart"])
def test_multi_measure_visuals_require_and_resolve_two_measures(visual_type: str) -> None:
    readable_name = "combo" if visual_type == "lineClusteredColumnComboChart" else "scatter"
    spec = create_report_spec(
        f"Create a sales dashboard showing total revenue versus total units by region as a {readable_name} chart",
        METADATA,
    )

    assert spec["visuals"][0]["type"] == visual_type
    assert len(spec["visuals"][0]["values"]) == 2
    validate_report_spec(spec, METADATA)


def test_requested_analytical_count_allows_explicit_slicers() -> None:
    spec = create_report_spec(
        "Create a sales dashboard with 1 visual:\n"
        "1. Total revenue by region as a column chart\n"
        "2. Add a slicer for City",
        METADATA,
    )

    assert [visual["type"] for visual in spec["visuals"]] == ["clusteredColumnChart", "slicer"]
