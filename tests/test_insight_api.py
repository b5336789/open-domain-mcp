"""Tests for the Advisor + Metrics HTTP API (TASKS #5.3/#5.5/#5.6).

Builds a throwaway FastAPI app pinned to a fake ``Context`` (mirroring
``tests/test_api.py``) and exercises the insight router directly.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from opendomainmcp.api.insight_routes import record_retrieval, router
from opendomainmcp.config import Settings
from opendomainmcp.context import Context
from opendomainmcp.models import Chunk, KnowledgeUnit
from opendomainmcp.views import VIEWS


@pytest.fixture
def insight(store, pipeline, fake_graph, tmp_path):
    settings = Settings(data_dir=tmp_path)
    ctx = Context(settings=settings, store=store, pipeline=pipeline, graph=fake_graph)
    app = FastAPI()
    app.state.context = ctx
    app.state.contexts = {}
    app.state.context_factory = lambda **k: ctx
    app.include_router(router)
    return TestClient(app), ctx, tmp_path


def _seed_varied_knowledge(ctx):
    """Insert a few approved chunks of varying knowledge_type."""
    ctx.store.upsert([
        Chunk(text="deploy the service to production", source="ops.md", kind="text",
              knowledge=KnowledgeUnit(summary="deploy steps", knowledge_type="Workflow",
                                      review_status="approved")),
        Chunk(text="deploy may fail when credentials expire", source="ops.md", kind="text",
              knowledge=KnowledgeUnit(summary="deploy error", knowledge_type="Error",
                                      review_status="approved")),
        Chunk(text="deploy requires the admin permission", source="iam.md", kind="text",
              knowledge=KnowledgeUnit(summary="deploy perm", knowledge_type="Permission",
                                      review_status="approved")),
        Chunk(text="free tier deploy is capped at one region", source="limits.md", kind="text",
              knowledge=KnowledgeUnit(summary="deploy limit", knowledge_type="Constraint",
                                      review_status="approved")),
    ])


def test_advise_returns_faceted_structure(insight):
    tc, ctx, _ = insight
    _seed_varied_knowledge(ctx)

    data = tc.post("/api/advise", json={"action": "deploy", "top_k": 5}).json()

    assert data["action"] == "deploy"
    for facet in ("workflow", "risks", "permissions", "dependencies", "constraints"):
        assert facet in data and isinstance(data[facet], list)
    assert "summary" in data and "counts" in data["summary"]
    # at least one facet retrieved something for the seeded action
    assert sum(data["summary"]["counts"].values()) > 0


def test_advise_empty_action_errors(insight):
    tc, _, _ = insight
    resp = tc.post("/api/advise", json={"action": "   "})
    assert resp.status_code == 422


def test_metrics_product_counts_and_zeroed_agent(insight):
    tc, ctx, _ = insight
    _seed_varied_knowledge(ctx)

    data = tc.get("/api/metrics").json()

    assert data["product"]["knowledge_objects"] == ctx.store.stats()["count"]
    assert data["product"]["published_mcps"] == len(VIEWS)
    assert data["product"]["indexed_sources"] >= 1  # ops.md/iam.md/limits.md

    agent = data["agent"]
    assert agent["total_events"] == 0
    assert agent["grounding_hit_rate"] == 0.0
    assert agent["avg_hits"] == 0.0
    assert agent["retrieval_precision"] == 0.0


def test_record_retrieval_is_reflected_in_metrics(insight):
    tc, ctx, _ = insight

    results = [
        {"id": "1", "score": 0.9, "metadata": {"knowledge_type": "Workflow"}},
        {"id": "2", "score": 0.5, "metadata": {"knowledge_type": "Error"}},
    ]
    record_retrieval(ctx, "search", "deploy", results)

    agent = tc.get("/api/metrics").json()["agent"]
    assert agent["total_events"] == 1
    assert agent["grounding_hit_rate"] > 0
    assert agent["avg_hits"] == 2.0


def test_record_retrieval_never_raises(insight):
    _, ctx, _ = insight
    # Malformed results (missing keys, None metadata) must not raise.
    record_retrieval(ctx, "ask", "q", [{}, {"metadata": None, "score": None}])
    # And a broken ctx is swallowed too (fail-safe contract).
    record_retrieval(object(), "search", "q", [{"id": "x"}])
