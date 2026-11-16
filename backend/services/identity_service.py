"""Request identity and ownership checks for single- and multi-user deployments."""
from __future__ import annotations

import re
from contextvars import ContextVar

from backend.config import get_settings


_IDENTITY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,127}\Z")
identity_context: ContextVar[str] = ContextVar("autobi_identity", default="demo-user")


def authenticate(authorization: str | None, forwarded_user: str | None = None) -> str:
    """Resolve a trusted identity from a configured bearer token.

    Forwarded identities are accepted only in single-user/development mode. In
    shared mode the token-to-user mapping is server-controlled.
    """
    settings = get_settings()
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        user_id = settings.api_tokens.get(token)
        if user_id:
            return user_id
    if settings.auth_required:
        raise PermissionError("A valid AURA bearer token is required")
    if forwarded_user and _IDENTITY_PATTERN.fullmatch(forwarded_user):
        return forwarded_user
    return "demo-user"


def current_user_id() -> str:
    return identity_context.get()


def assert_owner(owner_id: object, resource: str) -> None:
    settings = get_settings()
    if isinstance(owner_id, str) and owner_id == current_user_id():
        return
    if owner_id is None and not settings.auth_required:
        return
    raise PermissionError(f"{resource} is not owned by the authenticated user")
