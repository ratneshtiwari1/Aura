"""Shared product limits used by report planning and generation.

These values are compatibility policies rather than implementation details.
Keeping them in one dependency-light module prevents the planner, validator,
and generator from silently drifting apart.
"""

MAX_VISUALS_PER_PAGE = 12
MIN_RELATIONSHIP_CONFIDENCE = 0.8

# Canonical fields for the optional chart-ready view derived from a wide
# cross-tab. Their names are a versioned internal contract; semantic_fields in
# metadata tells consumers what each field means so they do not depend on the
# literals themselves.
CROSS_TAB_SEGMENT_FIELD = "Segment"
CROSS_TAB_SERIES_FIELD = "Scenario"
CROSS_TAB_COMBINED_SERIES_FIELD = "ScenarioSegment"
CROSS_TAB_TIME_FIELD = "Year"
CROSS_TAB_METRIC_FIELD = "Metric"
CROSS_TAB_VALUE_FIELD = "Value"

