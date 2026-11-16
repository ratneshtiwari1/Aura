from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import DEFAULT_MAX_UPLOAD_BYTES, SESSION_ID, get_session_data_dir, get_settings
from backend.main import app


@pytest.fixture(autouse=True)
def runtime_defaults(monkeypatch) -> None:
    monkeypatch.setenv("USE_AI", "false")
    for name in ("APP_DATA_DIR", "CORS_ORIGINS", "CORS_ALLOW_CREDENTIALS", "MAX_UPLOAD_BYTES"):
        monkeypatch.delenv(name, raising=False)


def test_runtime_settings_have_safe_local_defaults() -> None:
    settings = get_settings()

    assert settings.app_data_dir == Path(__file__).resolve().parents[1] / ".app_data"
    assert settings.cors_origins == ("http://localhost:5173", "http://127.0.0.1:5173")
    assert settings.cors_allow_credentials is False
    assert settings.max_upload_bytes == DEFAULT_MAX_UPLOAD_BYTES


def test_runtime_settings_are_configurable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CORS_ORIGINS", "https://reports.example.com, https://admin.example.com/")
    monkeypatch.setenv("CORS_ALLOW_CREDENTIALS", "true")
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "4096")

    settings = get_settings()

    assert settings.app_data_dir == tmp_path
    assert settings.cors_origins == ("https://reports.example.com", "https://admin.example.com")
    assert settings.cors_allow_credentials is True
    assert settings.max_upload_bytes == 4096


def test_session_data_directory_is_timestamped_under_configured_root(tmp_path) -> None:
    session_dir = get_session_data_dir(tmp_path)

    assert session_dir == tmp_path / "sessions" / SESSION_ID
    assert len(SESSION_ID) == len("2026-11-04_15-05-12-123456")


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_upload_limit_must_be_positive(monkeypatch, value) -> None:
    monkeypatch.setenv("MAX_UPLOAD_BYTES", value)
    with pytest.raises(ValueError, match="MAX_UPLOAD_BYTES"):
        get_settings()


def test_credentials_cannot_be_combined_with_wildcard_origin(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "*")
    monkeypatch.setenv("CORS_ALLOW_CREDENTIALS", "true")
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        get_settings()


def test_excel_upload_rejects_non_xlsx_content_and_removes_partial_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    response = TestClient(app).post(
        "/api/datasource/excel",
        files={"file": ("not-a-workbook.xlsx", b"plain text", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "The uploaded file is not a valid .xlsx workbook"
    assert list((tmp_path / "uploads").glob("*.xlsx")) == []


def test_excel_upload_enforces_size_limit_and_removes_partial_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "8")
    response = TestClient(app).post(
        "/api/datasource/excel",
        files={"file": ("too-large.xlsx", b"PK\x03\x04more than eight bytes", "application/octet-stream")},
    )

    assert response.status_code == 413
    assert "upload limit" in response.json()["detail"]
    assert list((tmp_path / "uploads").glob("*.xlsx")) == []
