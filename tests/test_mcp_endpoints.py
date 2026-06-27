"""Tests for the dynamic MCP endpoint publishing registry.

We mount the SSE apps and exercise the publish registry, but never open an SSE
stream (that would require a real context / MariaDB). We only assert that the
mounts exist and that the registry behaves correctly.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from opendomainmcp.api.mcp_endpoints import mount_mcp_apps, router
from opendomainmcp.config import Settings
from opendomainmcp.context import Context
from opendomainmcp.views import VIEW_NAMES

VIEWS_EXPECTED = ("product", "operations", "developer", "support", "architecture")


def _make_client(store, pipeline, fake_graph, tmp_path):
    settings = Settings(data_dir=tmp_path)
    ctx = Context(settings=settings, store=store, pipeline=pipeline, graph=fake_graph)
    app = FastAPI()
    app.state.context = ctx
    app.state.contexts = {}
    app.state.context_factory = lambda: ctx
    mounted = mount_mcp_apps(app)
    app.include_router(router)
    return TestClient(app), app, mounted


def test_mounts_exist_for_all_views(store, pipeline, fake_graph, tmp_path):
    _, app, mounted = _make_client(store, pipeline, fake_graph, tmp_path)

    assert set(mounted) == set(VIEW_NAMES)
    route_paths = {getattr(r, "path", None) for r in app.routes}
    for view in VIEWS_EXPECTED:
        assert f"/mcp/{view}" in route_paths


def test_list_endpoints_lists_all_views_unpublished(store, pipeline, fake_graph, tmp_path):
    tc, _, _ = _make_client(store, pipeline, fake_graph, tmp_path)

    data = tc.get("/api/mcp/endpoints").json()
    assert {e["view"] for e in data} == set(VIEWS_EXPECTED)
    for entry in data:
        view = entry["view"]
        assert entry["published"] is False
        assert entry["status"] == "unpublished"
        assert entry["latest_decision"] is None
        assert entry["history"] == []
        assert entry["path"] == f"/mcp/{view}"
        assert entry["url"].endswith(f"/mcp/{view}")
        assert entry["url"].startswith("http")


def test_publish_requires_override_when_quality_not_ready(
    store, pipeline, fake_graph, tmp_path
):
    tc, _, _ = _make_client(store, pipeline, fake_graph, tmp_path)

    resp = tc.post("/api/mcp/endpoints", json={"view": "product"})

    assert resp.status_code == 409
    assert "override reason" in resp.text


def test_publish_with_override_records_decision(store, pipeline, fake_graph, tmp_path):
    tc, _, _ = _make_client(store, pipeline, fake_graph, tmp_path)

    published = tc.post(
        "/api/mcp/endpoints",
        json={"view": "product", "override_reason": "Internal pilot only."},
    ).json()
    assert published["view"] == "product"
    assert published["published"] is True
    assert published["status"] == "published"
    assert published["latest_decision"]["action"] == "publish"
    assert published["latest_decision"]["override_reason"] == "Internal pilot only."
    assert published["history"][0]["action"] == "publish"

    data = {e["view"]: e for e in tc.get("/api/mcp/endpoints").json()}
    assert data["product"]["published"] is True
    assert data["developer"]["published"] is False
    assert data["product"]["latest_decision"]["override_reason"] == "Internal pilot only."


def test_unpublish_records_decision(store, pipeline, fake_graph, tmp_path):
    tc, _, _ = _make_client(store, pipeline, fake_graph, tmp_path)
    tc.post(
        "/api/mcp/endpoints",
        json={"view": "product", "override_reason": "Internal pilot only."},
    )

    unpub = tc.delete("/api/mcp/endpoints/product").json()
    assert unpub["view"] == "product"
    assert unpub["published"] is False
    assert unpub["status"] == "unpublished"
    assert unpub["latest_decision"]["action"] == "unpublish"
    assert unpub["history"][0]["status"] == "unpublished"

    data = {e["view"]: e for e in tc.get("/api/mcp/endpoints").json()}
    assert data["product"]["published"] is False
    assert data["product"]["latest_decision"]["action"] == "unpublish"


def test_publish_unknown_view_returns_404(store, pipeline, fake_graph, tmp_path):
    tc, _, _ = _make_client(store, pipeline, fake_graph, tmp_path)

    resp = tc.post("/api/mcp/endpoints", json={"view": "nope"})
    assert resp.status_code == 404
