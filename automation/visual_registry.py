"""Supported native Power BI visuals and their report-spec requirements."""

STANDARD_CHART_TYPES = frozenset({
    "clusteredColumnChart", "clusteredBarChart", "stackedColumnChart", "stackedBarChart",
    "hundredPercentStackedColumnChart", "hundredPercentStackedBarChart",
    "lineChart", "areaChart", "stackedAreaChart",
    "pieChart", "donutChart", "lineClusteredColumnComboChart", "scatterChart",
    "lineStackedColumnComboChart", "waterfallChart", "funnel", "treemap",
    "map", "filledMap", "ribbonChart", "decompositionTree",
})
MULTI_MEASURE_TYPES = frozenset({
    "lineClusteredColumnComboChart", "lineStackedColumnComboChart", "scatterChart",
})
CATEGORY_VALUE_TYPES = STANDARD_CHART_TYPES
SPECIAL_VISUAL_TYPES = frozenset({
    "card", "multiRowCard", "kpi", "gauge", "slicer", "pivotTable", "table",
})
ALL_VISUAL_TYPES = STANDARD_CHART_TYPES | SPECIAL_VISUAL_TYPES

# PBIR visual type identifiers differ from the user-facing report-spec names
# for the two visuals below. The remaining native identifiers are the PBIR IDs.
PBIR_VISUAL_TYPES = {
    "table": "tableEx",
    "decompositionTree": "decompositionTreeVisual",
    # Power BI's public visual names distinguish clustered and stacked bars,
    # while PBIR uses the native IDs barChart and columnChart for the stacked
    # variants. Emitting the user-facing name makes Desktop treat it as a
    # missing custom visual (CustomVisualNotFound).
    "stackedBarChart": "barChart",
    "stackedColumnChart": "columnChart",
}

VISUAL_LABELS = {
    "lineClusteredColumnComboChart": "Combo Chart",
    "scatterChart": "Scatter Chart",
    "waterfallChart": "Waterfall Chart",
    "funnel": "Funnel Chart",
    "treemap": "Treemap",
    "filledMap": "Filled Map",
    "gauge": "Gauge",
    "kpi": "KPI",
    "multiRowCard": "Multi-row Card",
    "table": "Table",
    "map": "Map",
    "ribbonChart": "Ribbon Chart",
    "decompositionTree": "Decomposition Tree",
}


def visual_capabilities():
    """Planner-visible supported schema, not an assertion of Desktop certification."""
    entries=[]
    for kind in sorted(ALL_VISUAL_TYPES):
        required = ["category", "values"] if kind in CATEGORY_VALUE_TYPES else ["value"]
        optional = ["title"]
        if kind in CATEGORY_VALUE_TYPES:optional += ["tooltips"]
        if kind in CATEGORY_VALUE_TYPES: optional += ["legend", "categoryHierarchy"]
        if kind == "slicer":required=["field"]
        if kind == "table":required=["rows"]; optional += ["values"]
        if kind == "pivotTable":required=["rows", "values"]
        if kind == "pivotTable":optional += ["columns"]
        if kind == "gauge":optional += ["minimum", "maximum", "target"]
        if kind == "kpi":optional += ["target", "trend"]
        if kind == "scatterChart":optional += ["size"]
        entries.append({"type":kind,"required":required,"optional":optional,
                        "minimumMeasures":0 if kind in {"slicer", "table"} else 2 if kind in MULTI_MEASURE_TYPES else 1})
    return {"visuals":entries,"crossTableBindings":False,
            "limitations":["custom visuals are not implemented",
                           "advanced interaction settings require native Power BI rendering verification",
                           "preview may not display every native role"]}
