from pathlib import Path
import json
import pytest
from automation.report_automation import validate_report_spec,generate_pbir
from automation.visual_registry import visual_capabilities
from automation.semantic.semantic_validator import normalize_ai_table_bindings, validate_ai_plan

M={'tables':{'Sales':{'columns':['Month','Region'],'measures':['Revenue','Target','Maximum','Minimum','Units']}}}
def measure(n):return {'table':'Sales','field':n,'fieldType':'measure'}
def column(n):return {'table':'Sales','field':n,'fieldType':'column'}

@pytest.mark.parametrize('visual,roles',[
    ({'type':'gauge','value':measure('Revenue'),'target':measure('Target'),'maximum':measure('Maximum'),'minimum':measure('Minimum')},['Y','TargetValue','MaxValue','MinValue']),
    ({'type':'kpi','value':measure('Revenue'),'target':measure('Target'),'trend':column('Month')},['Indicator','Goal','TrendLine']),
    ({'type':'scatterChart','category':column('Region'),'values':[measure('Revenue'),measure('Target')],'size':measure('Units')},['Category','X','Y','Size']),
    ({'type':'pivotTable','rows':[column('Region')],'values':[measure('Revenue')],'columns':[column('Month')]},['Rows','Values','Columns']),
    ({'type':'lineChart','category':column('Month'),'values':[measure('Revenue'),measure('Target')],'tooltips':[measure('Units')]},['Category','Y','Tooltips']),
])
def test_extra_bindings_validate_and_are_emitted(tmp_path,visual,roles):
    spec={'page':'Test','visuals':[visual]}
    validate_report_spec(spec,M)
    from backend.services.pbir_service import ensure_report_layout
    (tmp_path/'Sales.SemanticModel').mkdir()
    ensure_report_layout(tmp_path/'Report','../Sales.SemanticModel')
    generate_pbir(tmp_path/'Report',spec,M,root=tmp_path)
    files=list((tmp_path/'Report').rglob('visual.json'))
    charts=[json.loads(f.read_text()) for f in files]
    target=next(c for c in charts if c.get('visual',{}).get('visualType') not in {'textbox','shape','image'})
    query=target['visual']['query']['queryState']
    assert set(roles)<=set(query)


def test_incompatible_role_rejected():
    with pytest.raises(ValueError,match='target binding'):
        validate_report_spec({'page':'T','visuals':[{'type':'card','value':measure('Revenue'),'target':measure('Target')}]},M)


def test_planner_sees_catalog():
    catalog=visual_capabilities()
    assert len(catalog['visuals'])==28
    assert next(v for v in catalog['visuals'] if v['type']=='gauge')['optional'][-3:]==['minimum','maximum','target']


@pytest.mark.parametrize("visual", [
    {"type": "table", "columns": [column("Region")], "values": [measure("Revenue")]},
    {"type": "table", "columns": [column("Region"), measure("Revenue")]},
    {"type": "table", "category": column("Region"), "value": measure("Revenue")},
])
def test_table_role_normalization_preserves_explicit_fields(visual):
    spec = {"page": "Overview", "visuals": [visual]}
    assert normalize_ai_table_bindings(spec, M)
    validate_ai_plan(spec, M, None)
    assert visual["rows"] == [column("Region")]
    assert visual["values"] == [measure("Revenue")]
    assert "columns" not in visual


def test_table_role_normalization_does_not_guess_or_discard_bindings():
    visual = {"type": "table", "columns": [column("Region"), measure("Revenue")], "values": [measure("Target")]}
    spec = {"page": "Overview", "visuals": [visual]}
    assert normalize_ai_table_bindings(spec, M) == []
    with pytest.raises(ValueError, match="must include rows"):
        validate_ai_plan(spec, M, None)
    visual = {"type": "table", "columns": [column("Region"), measure("Revenue")], "value": measure("Target")}
    assert normalize_ai_table_bindings({"page": "Overview", "visuals": [visual]}, M) == [{"type": "table", "from": "value", "to": "values"}]
    assert "columns" in visual and "rows" not in visual
    visual["columns"] = [column("Missing")]
    spec = {"page": "Overview", "visuals": [visual]}
    assert normalize_ai_table_bindings(spec, M) == []
    with pytest.raises(ValueError, match="must include rows"):
        validate_ai_plan(spec, M, None)


def test_matrix_columns_remain_column_groups():
    visual = {"type": "pivotTable", "rows": [column("Region")], "values": [measure("Revenue")], "columns": [column("Month")]}
    spec = {"page": "Overview", "visuals": [visual]}
    assert normalize_ai_table_bindings(spec, M) == []
    validate_ai_plan(spec, M, None)
    assert visual["columns"] == [column("Month")]


@pytest.mark.parametrize("visual", [
    {"type": "table", "rows": [column("Region")], "values": [column("Month")]},
    {"type": "table", "values": [column("Region"), column("Month")]},
    {"type": "table", "columns": [column("Region"), column("Month")]},
    {"type": "table", "category": column("Region"), "value": column("Month")},
])
def test_column_only_table_normalizes_and_generates_pbir(tmp_path, visual):
    spec = {"page": "Details", "visuals": [visual]}
    normalize_ai_table_bindings(spec, M)
    validate_ai_plan(spec, M, None)
    assert visual["rows"] == [column("Region"), column("Month")]
    assert not visual.get("values")
    from backend.services.pbir_service import ensure_report_layout
    (tmp_path / "Sales.SemanticModel").mkdir()
    ensure_report_layout(tmp_path / "Report", "../Sales.SemanticModel")
    generate_pbir(tmp_path / "Report", spec, M, root=tmp_path)
    charts = [json.loads(path.read_text()) for path in (tmp_path / "Report").rglob("visual.json")]
    table = next(chart for chart in charts if chart.get("visual", {}).get("visualType") == "tableEx")
    projections = table["visual"]["query"]["queryState"]["Values"]["projections"]
    assert [item["field"]["Column"]["Property"] for item in projections] == ["Region", "Month"]


def test_table_mixed_columns_and_measures_are_preserved():
    visual = {"type": "table", "rows": [column("Region")], "values": [column("Month"), measure("Revenue")]}
    spec = {"page": "Details", "visuals": [visual]}
    normalize_ai_table_bindings(spec, M)
    validate_ai_plan(spec, M, None)
    assert visual["rows"] == [column("Region"), column("Month")]
    assert visual["values"] == [measure("Revenue")]


def test_table_unknown_or_ambiguous_fields_are_not_invented():
    visual = {"type": "table", "rows": [column("Region")], "values": [column("Missing")]}
    spec = {"page": "Details", "visuals": [visual]}
    assert normalize_ai_table_bindings(spec, M) == []
    with pytest.raises(ValueError, match="values must use measure"):
        validate_ai_plan(spec, M, None)
    visual["values"] = [measure("Missing")]
    assert normalize_ai_table_bindings(spec, M) == []
    with pytest.raises(ValueError, match="Unknown value field"):
        validate_ai_plan(spec, M, None)
    ambiguous_metadata = {"tables": {"Sales": {"columns": ["Region", "Revenue"], "measures": ["Revenue"]}}}
    visual["values"] = [{"table": "Sales", "field": "Revenue"}]
    assert normalize_ai_table_bindings(spec, ambiguous_metadata) == []
    with pytest.raises(ValueError, match="values must use measure"):
        validate_ai_plan(spec, ambiguous_metadata, None)


def test_table_field_types_recover_only_unambiguous_schema_fields():
    visual = {
        "type": "table",
        "rows": [{"table": "Sales", "field": "Region"}],
        "values": [measure("Month"), {"table": "Sales", "field": "Revenue", "fieldType": "column"}],
    }
    spec = {"page": "Details", "visuals": [visual]}
    normalize_ai_table_bindings(spec, M)
    validate_ai_plan(spec, M, None)
    assert visual["rows"] == [column("Region"), column("Month")]
    assert visual["values"] == [measure("Revenue")]
