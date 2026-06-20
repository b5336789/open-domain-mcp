"""Integration: the real ``create_app`` wires every feature router/endpoint.

The per-feature router tests build throwaway apps; this guards that the actual
application object exposes them and that RBAC (default OFF) does not block reads.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from opendomainmcp.api.app import create_app
from opendomainmcp.config import Settings
from opendomainmcp.context import Context


@pytest.fixture
def client(store, pipeline, fake_graph, tmp_path):
    settings = Settings(data_dir=tmp_path)
    ctx = Context(settings=settings, store=store, pipeline=pipeline, graph=fake_graph)
    app = create_app(context=ctx, context_factory=lambda **_: ctx)
    return TestClient(app)


def test_health_is_rich(client):
    data = client.get("/api/health").json()
    assert data["status"] == "ok"
    assert "documents" in data and "graph" in data and "version" in data


def test_metrics_endpoint_wired(client):
    data = client.get("/api/metrics").json()
    assert "product" in data and "agent" in data
    assert data["product"]["published_mcps"] == 5  # the five MCP views


def test_sources_endpoint_wired(client):
    data = client.get("/api/sources").json()
    assert "sources" in data and isinstance(data["sources"], list)


def test_advise_endpoint_wired(client):
    data = client.post("/api/advise", json={"action": "deploy a release"}).json()
    for facet in ("workflow", "risks", "permissions", "dependencies", "constraints"):
        assert facet in data


def test_mcp_endpoints_registry_wired(client):
    data = client.get("/api/mcp/endpoints").json()
    views = {e["view"] for e in data}
    assert {"product", "operations", "developer", "support", "architecture"} <= views
    assert all(e["published"] is False for e in data)


def test_graph_endpoint_wired(client):
    # fake_graph returns an empty list; the route must still resolve (200).
    resp = client.get("/api/graph/entities")
    assert resp.status_code == 200
    assert "items" in resp.json()


def test_mcp_view_sse_apps_mounted(client):
    mounted = {r.path for r in client.app.routes if getattr(r, "path", "").startswith("/mcp/")}
    assert "/mcp/developer" in mounted and "/mcp/product" in mounted


def test_rbac_default_off_allows_simulate(client):
    # Auth disabled by default → anonymous full access, simulate works.
    resp = client.post("/api/simulate", json={"view": "product", "query": "hello"})
    assert resp.status_code == 200
