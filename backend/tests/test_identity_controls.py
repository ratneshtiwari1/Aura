from types import SimpleNamespace

import pytest

from backend.services import identity_service


def test_shared_mode_requires_server_mapped_bearer_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        identity_service,
        "get_settings",
        lambda: SimpleNamespace(auth_required=True, api_tokens={"token-a": "analyst@example.com"}),
    )

    assert identity_service.authenticate("Bearer token-a") == "analyst@example.com"
    with pytest.raises(PermissionError, match="valid AURA bearer token"):
        identity_service.authenticate(None, "spoofed@example.com")


def test_resource_owner_must_match_authenticated_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        identity_service,
        "get_settings",
        lambda: SimpleNamespace(auth_required=True, api_tokens={}),
    )
    token = identity_service.identity_context.set("owner-a")
    try:
        identity_service.assert_owner("owner-a", "Source")
        with pytest.raises(PermissionError, match="not owned"):
            identity_service.assert_owner("owner-b", "Source")
    finally:
        identity_service.identity_context.reset(token)
