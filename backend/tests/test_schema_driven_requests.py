import pytest

from backend.services.report_planner import build_report_plan
from backend.services.quality_analysis import quality_analysis


def test_comparisons_use_uploaded_header_names():
    columns = ["Division", "Booked Hours", "Target Hours", "Booked Units", "Target Units"]
    metadata = {"tables": {"Operations": {"columns": columns,
        "measures": ["Total " + c for c in columns[1:]], "columnTypes": {},
        "columnRoles": {"Division": "dimension"}}}}
    plan = build_report_plan("Create a dashboard comparing Booked versus Target Hours and Units by Division.", metadata)
    assert [[m["field"] for m in v["values"]] for v in plan["visuals"]] == [
        ["Total Booked Hours", "Total Target Hours"], ["Total Booked Units", "Total Target Units"]]


def test_missing_slicer_is_not_a_successful_dashboard():
    metadata = {"tables": {"Orders":{"columns":["Market","Amount"], "measures":["Total Amount"]}}}
    with pytest.raises(ValueError, match="requested slicers"):
        build_report_plan("Create a dashboard showing total amount by market. Include interactive market and period slicers.",metadata)


def test_quality_analysis_on_unrelated_field_names():
    meta = {"tables":{"Observations":{"columns":["Event","Team","Duration","Manager"],
            "columnRoles":{"Duration":"measure","Event":"identifier"},"columnTypes":{}}}}
    raw = {"Observations":{"rows":[{"Event":str(i),"Team":"A","Manager":None if i==2 else "B",
                                    "Duration": v} for i,v in enumerate([8,9,10,11,100])]}}
    answer = quality_analysis(meta, raw, "Analyze duration by team. Highlight unusual observations and missing manager information.")
    assert answer["findings"][0]["records"] == ["2"]
    assert answer["findings"][-1]["outliers"] == [{"record":"4","value":100}]
