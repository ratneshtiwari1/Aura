from backend.services.calculation_validation_service import incompatible_slicer_indexes, validate_calculation_accuracy


def test_calculation_gate_checks_dax_totals_filters_and_joins(tmp_path) -> None:
    model = tmp_path / "Report.SemanticModel"
    definition = model / "definition"
    tables = definition / "tables"
    tables.mkdir(parents=True)
    (tables / "Revenue.tmdl").write_text(
        "table 'Revenue'\n\tmeasure 'Total Net Revenue' = SUM('Revenue'[Net Revenue])\n",
        encoding="utf-8",
    )
    (tables / "Scenario.tmdl").write_text("table 'Scenario'\n", encoding="utf-8")
    (definition / "relationships.tmdl").write_text(
        "relationship 'AURA_Relationship_1'\n"
        "\tfromColumn: 'Revenue'.'Scenario'\n"
        "\ttoColumn: 'Scenario'.'Scenario'\n",
        encoding="utf-8",
    )
    metadata = {
        "tables": {
            "Revenue": {
                "columns": ["Scenario", "Net Revenue"],
                "measures": ["Total Net Revenue"],
                "measureDefinitions": {"Total Net Revenue": {"column": "Net Revenue", "aggregation": "SUM"}},
            },
            "Scenario": {"columns": ["Scenario"], "measures": [], "measureDefinitions": {}},
        },
        "relationships": [{
            "source_table": "Revenue", "source_column": "Scenario",
            "target_table": "Scenario", "target_column": "Scenario", "confidence": 1.0,
        }],
    }
    source = {"metadata": {"tables": {
        "Revenue": {"rows": [{"Scenario": "Actual", "Net Revenue": 100}, {"Scenario": "Budget", "Net Revenue": 125}]},
        "Scenario": {"rows": [{"Scenario": "Actual"}, {"Scenario": "Budget"}]},
    }}}
    spec = {"page": "Executive", "visuals": [
        {"type": "card", "value": {"table": "Revenue", "field": "Total Net Revenue", "fieldType": "measure"}},
        {"type": "slicer", "field": {"table": "Scenario", "field": "Scenario", "fieldType": "column"}},
    ]}
    preview = {"visuals": [
        {"data": [{"label": "Total Net Revenue", "value": 225}]},
        {"data": [], "options": ["Actual", "Budget"]},
    ]}

    result = validate_calculation_accuracy(spec, metadata, source, model, preview)

    assert result["passed"] is True
    assert {check["type"] for check in result["checks"]} >= {
        "measure", "total", "filter", "join", "filterPropagation",
    }


def test_slicer_checks_include_column_only_table(tmp_path) -> None:
    metadata = {"tables": {"KPI": {"columns": ["Name"], "measures": []},
                           "Regions": {"columns": ["Region"], "measures": []}},
                "relationships": []}
    spec = {"page": "Overview", "visuals": [
        {"type": "slicer", "field": {"table": "Regions", "field": "Region", "fieldType": "column"}},
        {"type": "table", "rows": [{"table": "KPI", "field": "Name", "fieldType": "column"}]},
    ]}
    source = {"metadata": {"tables": {"Regions": {"rows": [{"Region": "North"}]}, "KPI": {"rows": [{"Name": "Sales"}]}}}}
    preview = {"visuals": [{"options": ["North"]}, {"data": [], "tableRows": [["Sales"]]}]}
    result = validate_calculation_accuracy(spec, metadata, source, tmp_path, preview)
    assert incompatible_slicer_indexes(spec, metadata) == [0]
    assert not result["passed"]
    assert any("cannot filter table KPI" in error for error in result["errors"])
