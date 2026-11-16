import json
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from automation.report_automation import create_pbip_project_file as write_pbip_project_file
from automation.report_automation import package_pbip


def create_pbip_project_file(report_path: str | Path, root: Path | None = None) -> Path:
    workspace = (root or Path.cwd()).resolve()
    project_path = write_pbip_project_file(report_path, workspace)
    validate_pbip_project_file(project_path, workspace)
    return project_path


def zip_report(report_path: str | Path, root: Path | None = None) -> Path:
    workspace = (root or Path.cwd()).resolve()
    archive_path = package_pbip(report_path, workspace)
    try:
        validate_pbip_archive(archive_path)
    except (OSError, ValueError, zipfile.BadZipFile):
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path


def _inside_workspace(path: Path, workspace: Path) -> bool:
    resolved = path.resolve()
    return resolved != workspace and workspace in resolved.parents


def validate_pbip_project_file(project_path: str | Path, root: Path | None = None) -> dict[str, Any]:
    """Validate a PBIP pointer and every local path it resolves before launch.

    The project file is treated as untrusted persisted state: paths must remain
    within the configured workspace and resolve to the report/model generated
    beside the project. This validation is schema-driven and independent of
    workbook field names.
    """
    workspace = (root or Path.cwd()).resolve()
    project = Path(project_path).resolve()
    if not _inside_workspace(project, workspace) or not project.is_file():
        raise ValueError("PBIP project file is missing or outside the configured workspace")
    try:
        payload = json.loads(project.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"PBIP project file is invalid JSON: {exc}") from exc
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
    if not isinstance(artifacts, list) or len(artifacts) != 1:
        raise ValueError("PBIP project must contain exactly one report artifact")
    report_entry = artifacts[0].get("report") if isinstance(artifacts[0], dict) else None
    report_reference = report_entry.get("path") if isinstance(report_entry, dict) else None
    if not isinstance(report_reference, str) or not report_reference.strip():
        raise ValueError("PBIP project is missing its report artifact path")
    report_path = (project.parent / report_reference).resolve()
    if not _inside_workspace(report_path, workspace) or not report_path.is_dir():
        raise ValueError("PBIP report artifact is missing or outside the configured workspace")
    definition_path = report_path / "definition.pbir"
    if not definition_path.is_file():
        raise ValueError("PBIP report artifact is missing definition.pbir")
    try:
        report_definition = json.loads(definition_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"PBIP report definition is invalid JSON: {exc}") from exc
    model_reference = report_definition.get("datasetReference", {}).get("byPath", {}).get("path")
    if not isinstance(model_reference, str) or not model_reference.strip():
        raise ValueError("PBIP report definition is missing its semantic-model path")
    model_path = (report_path / model_reference).resolve()
    if not _inside_workspace(model_path, workspace) or not model_path.is_dir():
        raise ValueError("PBIP semantic model is missing or outside the configured workspace")
    return {
        "projectPath": project,
        "reportPath": report_path,
        "modelPath": model_path,
        "definition": payload,
    }


def validate_pbip_archive(archive_path: str | Path) -> dict[str, Any]:
    """Reject corrupt or unsafe PBIP download archives before serving them."""
    archive_file = Path(archive_path)
    if not archive_file.is_file():
        raise ValueError("PBIP archive was not created")
    with zipfile.ZipFile(archive_file) as archive:
        names = archive.namelist()
        if not names or len(names) != len(set(names)):
            raise ValueError("PBIP archive is empty or contains duplicate entries")
        for name in names:
            path = PurePosixPath(name.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("PBIP archive contains an unsafe path")
        corrupt_entry = archive.testzip()
        if corrupt_entry is not None:
            raise ValueError(f"PBIP archive contains a corrupt entry: {corrupt_entry}")
        project_names = [name for name in names if PurePosixPath(name).parent == PurePosixPath(".") and name.endswith(".pbip")]
        if len(project_names) != 1:
            raise ValueError("PBIP archive must contain exactly one project pointer")
        try:
            project = json.loads(archive.read(project_names[0]))
        except json.JSONDecodeError as exc:
            raise ValueError(f"PBIP archive project pointer is invalid JSON: {exc}") from exc
        artifacts = project.get("artifacts") if isinstance(project, dict) else None
        report_entry = artifacts[0].get("report") if isinstance(artifacts, list) and len(artifacts) == 1 and isinstance(artifacts[0], dict) else None
        report_reference = report_entry.get("path") if isinstance(report_entry, dict) else None
        if not isinstance(report_reference, str) or f"{report_reference}/definition.pbir" not in names:
            raise ValueError("PBIP archive does not contain the report referenced by its project pointer")
    return {"archivePath": archive_file, "project": project_names[0], "entryCount": len(names)}
