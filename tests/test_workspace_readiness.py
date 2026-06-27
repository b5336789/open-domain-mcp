from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from opendomainmcp.api import workspace_routes
from opendomainmcp.config import Settings
from opendomainmcp.context import Context
from opendomainmcp.models import Chunk, KnowledgeUnit
from opendomainmcp.quality import compute_readiness


@pytest.fixture
def ctx(store, pipeline, fake_graph, tmp_path):
    settings = Settings(data_dir=tmp_path)
    return Context(settings=settings, store=store, pipeline=pipeline, graph=fake_graph)


def _chunk(text: str, source: str, status: str) -> Chunk:
    return Chunk(
        text=text,
        source=source,
        knowledge=KnowledgeUnit(summary=text, review_status=status),
    )


def test_empty_collection_is_blocked(ctx):
    readiness = compute_readiness(ctx, tasks=[])

    assert readiness["status"] == "blocked"
    assert readiness["score"] == 0
    assert readiness["blockers"] == ["No indexed knowledge objects."]
    assert readiness["next_action"] == "Add sources in Source Intake."


def test_pending_review_marks_collection_needs_review(ctx):
    ctx.store.upsert(
        [
            _chunk("approved knowledge", "approved.md", "approved"),
            _chunk("pending knowledge", "pending.md", "pending"),
        ]
    )

    readiness = compute_readiness(ctx, tasks=[])

    assert readiness["status"] == "needs_review"
    assert readiness["approved_ratio"] == 0.5
    assert readiness["warnings"] == ["1 knowledge object is pending review."]


def test_failed_jobs_block_readiness(ctx):
    ctx.store.upsert([_chunk("approved knowledge", "approved.md", "approved")])

    readiness = compute_readiness(
        ctx,
        tasks=[{"status": "error"}, {"status": "done"}],
    )

    assert readiness["status"] == "blocked"
    assert readiness["job_health"]["error"] == 1
    assert readiness["blockers"] == ["1 background job failed."]


def test_workspace_readiness_endpoint_returns_context_summary(ctx):
    app = FastAPI()
    app.state.context = ctx
    app.state.contexts = {}
    app.include_router(workspace_routes.router)
    client = TestClient(app)

    resp = client.get("/api/workspace/readiness")

    assert resp.status_code == 200
    data = resp.json()
    assert data["collection"] == ctx.store.stats()["collection"]
    assert data["status"] == "blocked"
    assert data["source_health"]["sources"] == 0
