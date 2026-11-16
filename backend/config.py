from __future__ import annotations

import os
import math
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
SESSION_ID = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S-%f")
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


def _parse_bool(name: str, value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"{name} must be one of true, false, 1, 0, yes, or no")


def _parse_positive_int(name: str, value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def _parse_cors_origins(value: str | None) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_CORS_ORIGINS
    origins = tuple(dict.fromkeys(origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()))
    if not origins:
        raise ValueError("CORS_ORIGINS must contain at least one origin")
    invalid = [origin for origin in origins if origin != "*" and not origin.startswith(("http://", "https://"))]
    if invalid:
        raise ValueError("CORS_ORIGINS entries must be HTTP(S) origins or '*'")
    return origins


def _app_data_dir(value: str | None) -> Path:
    if not value or not value.strip():
        return Path(__file__).resolve().parent / ".app_data"
    configured = Path(value.strip()).expanduser()
    return configured.resolve() if configured.is_absolute() else (Path.cwd() / configured).resolve()


@dataclass(frozen=True)
class Settings:
    app_data_dir: Path
    cors_origins: tuple[str, ...]
    cors_allow_credentials: bool
    max_upload_bytes: int
    use_ai: bool
    ai_provider: str
    azure_openai_api_key: str | None
    azure_ai_foundry_project_endpoint: str | None
    azure_openai_endpoint: str | None
    azure_openai_deployment: str | None
    azure_openai_api_version: str
    ai_timeout_seconds: float
    auth_required: bool = False
    api_tokens: dict[str, str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "api_tokens", self.api_tokens or {})


def _api_tokens(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("AUTOBI_API_TOKENS must be a JSON object mapping tokens to user IDs") from error
    if not isinstance(parsed, dict) or any(
        not isinstance(token, str) or not token or not isinstance(user, str) or not user.strip()
        for token, user in parsed.items()
    ):
        raise ValueError("AUTOBI_API_TOKENS must map non-empty tokens to non-empty user IDs")
    return {token: user.strip()[:128] for token, user in parsed.items()}


def get_settings() -> Settings:
    app_data_dir = _app_data_dir(os.getenv("APP_DATA_DIR"))
    cors_origins = _parse_cors_origins(os.getenv("CORS_ORIGINS"))
    cors_allow_credentials = _parse_bool(
        "CORS_ALLOW_CREDENTIALS",
        os.getenv("CORS_ALLOW_CREDENTIALS"),
        default=False,
    )
    if cors_allow_credentials and "*" in cors_origins:
        raise ValueError("CORS_ORIGINS cannot contain '*' when CORS_ALLOW_CREDENTIALS is enabled")
    max_upload_bytes = _parse_positive_int(
        "MAX_UPLOAD_BYTES",
        os.getenv("MAX_UPLOAD_BYTES"),
        DEFAULT_MAX_UPLOAD_BYTES,
    )
    auth_required = _parse_bool("AUTH_REQUIRED", os.getenv("AUTH_REQUIRED"))
    api_tokens = _api_tokens(os.getenv("AURA_API_TOKENS") or os.getenv("AUTOBI_API_TOKENS"))
    if auth_required and not api_tokens:
        raise ValueError("AUTOBI_API_TOKENS must be configured when AUTH_REQUIRED is enabled")
    use_ai = _parse_bool("USE_AI", os.getenv("USE_AI"), default=True)
    if not use_ai:
        return Settings(
            app_data_dir=app_data_dir,
            cors_origins=cors_origins,
            cors_allow_credentials=cors_allow_credentials,
            max_upload_bytes=max_upload_bytes,
            use_ai=False,
            ai_provider="azure_openai",
            azure_openai_api_key=None,
            azure_ai_foundry_project_endpoint=None,
            azure_openai_endpoint=None,
            azure_openai_deployment=None,
            azure_openai_api_version="2024-10-21",
            ai_timeout_seconds=30.0,
            auth_required=auth_required,
            api_tokens=api_tokens,
        )
    try:
        timeout = float(os.getenv("AI_TIMEOUT_SECONDS", "30"))
    except ValueError as error:
        raise ValueError("AI_TIMEOUT_SECONDS must be a positive number") from error
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("AI_TIMEOUT_SECONDS must be a positive number")
    provider = os.getenv("AI_PROVIDER", "azure_openai").strip().casefold()
    if provider not in {"azure_openai", "mock"}:
        raise ValueError("AI_PROVIDER must be either 'azure_openai' or 'mock'")
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "").strip() or None
    project_endpoint = os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "").strip().rstrip("/") or None
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/") or None
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip() or None
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21").strip()
    if provider == "azure_openai":
        for name, value in (
            ("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", project_endpoint),
            ("AZURE_OPENAI_ENDPOINT", endpoint),
        ):
            if value and not value.startswith("https://"):
                raise ValueError(f"{name} must be an HTTPS endpoint when AI_PROVIDER is 'azure_openai'")
        if not api_version:
            raise ValueError("AZURE_OPENAI_API_VERSION must not be empty when AI_PROVIDER is 'azure_openai'")
    return Settings(
        app_data_dir=app_data_dir,
        cors_origins=cors_origins,
        cors_allow_credentials=cors_allow_credentials,
        max_upload_bytes=max_upload_bytes,
        use_ai=True,
        ai_provider=provider,
        azure_openai_api_key=api_key,
        azure_ai_foundry_project_endpoint=project_endpoint,
        azure_openai_endpoint=endpoint,
        azure_openai_deployment=deployment,
        azure_openai_api_version=api_version,
        ai_timeout_seconds=timeout,
        auth_required=auth_required,
        api_tokens=api_tokens,
    )


def get_session_data_dir(app_data_dir: Path | None = None) -> Path:
    base_dir = app_data_dir if app_data_dir is not None else get_settings().app_data_dir
    return base_dir / "sessions" / SESSION_ID
