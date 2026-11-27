import json

import pytest

from backend.services.package_service import validate_pbip_project_file


def test_pbip_project_validation_resolves_report_and_model_inside_workspace(tmp_path) -> None:
    report = tmp_path / "Example.Report"
    model = tmp_path / "Example.SemanticModel"
    report.mkdir()
    model.mkdir()
    (report / "definition.pbir").write_text(json.dumps({
        "datasetReference": {"byPath": {"path": "../Example.SemanticModel"}},
    }), encoding="utf-8")
    project = tmp_path / "Example.pbip"
    project.write_text(json.dumps({
        "artifacts": [{"report": {"path": "Example.Report"}}],
    }), encoding="utf-8")

    result = validate_pbip_project_file(project, tmp_path)

    assert result["reportPath"] == report.resolve()
    assert result["modelPath"] == model.resolve()


def test_pbip_project_validation_rejects_artifacts_outside_workspace(tmp_path) -> None:
    project = tmp_path / "Unsafe.pbip"
    project.write_text(json.dumps({
        "artifacts": [{"report": {"path": "../Outside.Report"}}],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="outside"):
        validate_pbip_project_file(project, tmp_path)
