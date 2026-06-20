"""Multi-tenancy: tenant-namespaced collection resolution at the API boundary."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from opendomainmcp.api.deps import get_ctx, tenant_collection
from opendomainmcp.config import Settings


def tenant_collection_namespaces_name():
    # Arrange / Act
    result = tenant_collection("acme", "domain_knowledge")
    # Assert
    assert result == "acme::domain_knowledge"


def _app(monkeypatch, *, multi_tenant: bool):
    """A tiny app whose context_factory records the collection it was asked for."""
    monkeypatch.setenv("ODM_MULTI_TENANT", "true" if multi_tenant else "false")
    seen: list[str | None] = []

    def factory(collection=None):
        seen.append(collection)

        class _Ctx:  # minimal stand-in; the route only echoes the collection
            pass

        return _Ctx()

    app = FastAPI()
    app.state.context = None
    app.state.contexts = {}
    app.state.context_factory = factory

    @app.get("/whoami")
    def whoami(ctx=Depends(get_ctx)):
        return {"collection": seen[-1]}

    return TestClient(app), seen


def test_single_tenant_uses_plain_collection(monkeypatch):
    # Arrange
    client, seen = _app(monkeypatch, multi_tenant=False)
    # Act
    resp = client.get("/whoami", headers={"X-Collection": "kb"})
    # Assert
    assert resp.status_code == 200
    assert resp.json()["collection"] == "kb"


def test_multi_tenant_namespaces_collection(monkeypatch):
    # Arrange
    client, seen = _app(monkeypatch, multi_tenant=True)
    # Act
    resp = client.get("/whoami", headers={"X-Collection": "kb", "X-Tenant": "acme"})
    # Assert
    assert resp.status_code == 200
    assert resp.json()["collection"] == "acme::kb"


def test_multi_tenant_isolates_two_tenants(monkeypatch):
    # Arrange
    client, _ = _app(monkeypatch, multi_tenant=True)
    # Act
    a = client.get("/whoami", headers={"X-Collection": "kb", "X-Tenant": "acme"}).json()
    b = client.get("/whoami", headers={"X-Collection": "kb", "X-Tenant": "globex"}).json()
    # Assert — same logical collection, different physical namespaces
    assert a["collection"] == "acme::kb"
    assert b["collection"] == "globex::kb"


def test_multi_tenant_requires_tenant_header(monkeypatch):
    # Arrange
    client, _ = _app(monkeypatch, multi_tenant=True)
    # Act
    resp = client.get("/whoami", headers={"X-Collection": "kb"})
    # Assert — fail loud rather than leak into a shared default
    assert resp.status_code == 400


def test_settings_multi_tenant_defaults_off():
    assert Settings().multi_tenant is False
