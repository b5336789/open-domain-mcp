"""Tests for API-key parsing and MCP-view-level access control."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from opendomainmcp.api.auth import (
    auth_dependency,
    principal_allows_view,
    require_view_access,
)
from opendomainmcp.config import Settings


# --- parsed_api_keys -------------------------------------------------------

def test_parsed_api_keys_parses_valid_spec():
    # Arrange
    spec = "k1:admin:*,k2:dev:developer|architecture"

    # Act
    parsed = Settings(api_keys=spec).parsed_api_keys()

    # Assert
    assert parsed == {
        "k1": {"role": "admin", "views": ("*",)},
        "k2": {"role": "dev", "views": ("developer", "architecture")},
    }


def test_parsed_api_keys_empty_spec_is_empty():
    assert Settings(api_keys="").parsed_api_keys() == {}


def test_parsed_api_keys_tolerates_whitespace_and_trailing_comma():
    parsed = Settings(api_keys=" k1:admin:* , ").parsed_api_keys()
    assert parsed == {"k1": {"role": "admin", "views": ("*",)}}


@pytest.mark.parametrize(
    "bad_spec",
    [
        "k1:admin",            # too few fields
        "k1:admin:*:extra",    # too many fields
        ":admin:*",            # empty key
        "k1::*",               # empty role
        "k1:admin:",           # empty views
        "k1:admin:nope",       # unknown view
        "k1:admin:developer|ghost",  # one unknown view
    ],
)
def test_parsed_api_keys_raises_on_malformed(bad_spec):
    with pytest.raises(ValueError):
        Settings(api_keys=bad_spec).parsed_api_keys()


# --- auth_dependency via a tiny app ---------------------------------------

def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/whoami")
    def whoami(principal: dict = Depends(auth_dependency)) -> dict:
        return principal

    return app


def test_auth_disabled_yields_anonymous_full_access(monkeypatch):
    # Arrange: auth off (default) -- be explicit for isolation.
    monkeypatch.delenv("ODM_AUTH_ENABLED", raising=False)
    client = TestClient(_make_app())

    # Act
    resp = client.get("/whoami")

    # Assert
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "anonymous"
    assert body["views"] == ["*"]
    assert body["key"] is None


def test_auth_enabled_missing_key_is_401(monkeypatch):
    monkeypatch.setenv("ODM_AUTH_ENABLED", "true")
    monkeypatch.setenv("ODM_API_KEYS", "secret:dev:developer")
    client = TestClient(_make_app())

    resp = client.get("/whoami")

    assert resp.status_code == 401


def test_auth_enabled_wrong_key_is_401(monkeypatch):
    monkeypatch.setenv("ODM_AUTH_ENABLED", "true")
    monkeypatch.setenv("ODM_API_KEYS", "secret:dev:developer")
    client = TestClient(_make_app())

    resp = client.get("/whoami", headers={"X-API-Key": "nope"})

    assert resp.status_code == 401


def test_auth_enabled_valid_key_returns_principal(monkeypatch):
    monkeypatch.setenv("ODM_AUTH_ENABLED", "true")
    monkeypatch.setenv("ODM_API_KEYS", "secret:dev:developer|architecture")
    client = TestClient(_make_app())

    resp = client.get("/whoami", headers={"X-API-Key": "secret"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "dev"
    assert body["views"] == ["developer", "architecture"]
    assert body["key"] == "secret"


# --- per-view scoping helpers ---------------------------------------------

def test_principal_allows_view_wildcard():
    principal = {"role": "admin", "views": ("*",), "key": "k"}
    assert principal_allows_view(principal, "developer") is True
    assert principal_allows_view(principal, "support") is True


def test_principal_allows_view_explicit():
    principal = {"role": "dev", "views": ("developer",), "key": "k"}
    assert principal_allows_view(principal, "developer") is True
    assert principal_allows_view(principal, "support") is False


def test_require_view_access_allows_and_forbids():
    principal = {"role": "dev", "views": ("developer",), "key": "k"}

    # Allowed view: no exception.
    require_view_access(principal, "developer")

    # Disallowed view: 403.
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        require_view_access(principal, "support")
    assert exc.value.status_code == 403


def test_require_view_access_enforced_on_route(monkeypatch):
    """End-to-end: a scoped key gets 403 on a disallowed view route."""
    monkeypatch.setenv("ODM_AUTH_ENABLED", "true")
    monkeypatch.setenv("ODM_API_KEYS", "secret:dev:developer")

    app = FastAPI()

    @app.get("/views/{view}")
    def read_view(view: str, principal: dict = Depends(auth_dependency)) -> dict:
        require_view_access(principal, view)
        return {"view": view}

    client = TestClient(app)
    headers = {"X-API-Key": "secret"}

    assert client.get("/views/developer", headers=headers).status_code == 200
    assert client.get("/views/support", headers=headers).status_code == 403
