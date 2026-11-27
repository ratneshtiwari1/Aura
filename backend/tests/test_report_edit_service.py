import pytest

from automation.report_pages import report_pages
from backend.services.report_edit_service import edit_report_spec


def _metadata() -> dict:
    return {
        "tables": {
            "Revenue": {
                "columns": ["Fiscal Year", "Business Unit", "Region", "Launch Year"],
                "measures": ["Total Net Revenue"],
            }
        }
    }


def _report_spec() -> dict:
    return {
        "page": "Revenue Performance",
        "visuals": [{
            "type": "clusteredColumnChart",
            "category": {"table": "Revenue", "field": "Region", "fieldType": "column"},
            "value": {"table": "Revenue", "field": "Total Net Revenue", "fieldType": "measure"},
            "legend": {"table": "Revenue", "field": "Business Unit", "fieldType": "column"},
        }],
    }


def test_adds_relevant_chart_from_current_report_bindings() -> None:
    revised, changes = edit_report_spec(_report_spec(), "Add one relevant pie chart", _metadata())

    assert [visual["type"] for visual in revised["visuals"]] == ["clusteredColumnChart", "pieChart"]
    assert revised["visuals"][-1]["category"]["field"] == "Region"
    assert revised["visuals"][-1]["value"]["field"] == "Total Net Revenue"
    assert changes == ["Added pieChart for Total Net Revenue by Region"]


def test_adds_chart_for_requested_category_with_current_measure() -> None:
    revised, _ = edit_report_spec(_report_spec(), "Add a pie chart for region", _metadata())

    assert revised["visuals"][-1] == {
        "type": "pieChart",
        "category": {"table": "Revenue", "field": "Region", "fieldType": "column"},
        "value": {"table": "Revenue", "field": "Total Net Revenue", "fieldType": "measure"},
    }


def test_adds_multiple_slicers_when_fields_precede_plural_slicer() -> None:
    revised, changes = edit_report_spec(
        _report_spec(),
        "Create an executive sales dashboard showing total revenue. Include interactive region and year slicers.",
        _metadata(),
    )

    slicers = [visual["field"]["field"] for visual in revised["visuals"] if visual["type"] == "slicer"]
    assert slicers == ["Region", "Fiscal Year"]
    assert changes == ["Added slicer for Region", "Added slicer for Fiscal Year"]


def test_adds_multiple_slicers_from_a_for_clause() -> None:
    revised, changes = edit_report_spec(
        _report_spec(),
        "Add slicers for region and fiscal year",
        _metadata(),
    )

    assert [visual["field"]["field"] for visual in revised["visuals"] if visual["type"] == "slicer"] == [
        "Region",
        "Fiscal Year",
    ]
    assert changes == ["Added slicer for Region", "Added slicer for Fiscal Year"]


def test_removes_requested_slicer_and_preserves_other_visuals() -> None:
    spec = _report_spec()
    spec["visuals"].append({
        "type": "slicer",
        "field": {"table": "Revenue", "field": "Launch Year", "fieldType": "column"},
    })
    spec["visuals"].append({
        "type": "slicer",
        "field": {"table": "Revenue", "field": "Region", "fieldType": "column"},
    })

    revised, changes = edit_report_spec(spec, "remove slicer for Launch year", _metadata())

    assert revised["visuals"] == [spec["visuals"][0], spec["visuals"][2]]
    assert changes == ["Removed slicer for Launch Year"]


def test_removing_missing_slicer_is_reported() -> None:
    with pytest.raises(ValueError, match="No slicer for 'Launch Year' exists"):
        edit_report_spec(_report_spec(), "remove slicer for Launch year", _metadata())


def test_adds_a_named_page_and_preserves_existing_report_pages(monkeypatch) -> None:
    new_visual = {
        "type": "clusteredBarChart",
        "category": {"table": "Revenue", "field": "Business Unit", "fieldType": "column"},
        "value": {"table": "Revenue", "field": "Total Net Revenue", "fieldType": "measure"},
    }
    monkeypatch.setattr(
        "backend.services.report_edit_service.build_report_plan",
        lambda instruction, metadata: {"page": "Generated page", "visuals": [new_visual]},
    )
    spec = _report_spec()

    revised, changes = edit_report_spec(
        spec,
        "Add page 2 called Business Unit with Total Net Revenue by Business Unit as a bar chart",
        _metadata(),
    )

    pages = report_pages(revised)
    assert pages[0] == spec
    assert pages[1] == {"page": "Business Unit", "visuals": [new_visual]}
    assert changes == ["Added page 'Business Unit'"]


def test_adds_slicer_to_new_second_page_without_changing_first_page() -> None:
    spec = _report_spec()
    revised, changes = edit_report_spec(spec, "Add a slicer for Region in second page", _metadata())
    pages = report_pages(revised)
    assert pages[0] == spec
    assert pages[1]["page"] == "Page 2"
    assert pages[1]["visuals"] == [{
        "type": "slicer", "field": {"table": "Revenue", "field": "Region", "fieldType": "column"},
    }]
    assert changes == ["Added page 'Page 2'"]


def test_numbered_existing_page_edits_only_that_page() -> None:
    spec = {**_report_spec(), "additionalPages": [{"page": "Detail", "visuals": [
        {"type": "slicer", "field": {"table": "Revenue", "field": "Launch Year", "fieldType": "column"}},
    ]}]}
    revised, _ = edit_report_spec(spec, "Add a slicer for Region on page 2", _metadata())
    assert report_pages(revised)[0] == report_pages(spec)[0]
    assert [v["field"]["field"] for v in report_pages(revised)[1]["visuals"]] == ["Launch Year", "Region"]


@pytest.mark.parametrize(
    ("instruction", "message"),
    [
        ("Add page 3 called Products: Show revenue by product", "next page number is 2"),
        ("Add page 2 called Revenue Performance: Show revenue by region", "already exists"),
    ],
)
def test_rejects_out_of_order_or_duplicate_new_pages(instruction, message) -> None:
    with pytest.raises(ValueError, match=message):
        edit_report_spec(_report_spec(), instruction, _metadata())
