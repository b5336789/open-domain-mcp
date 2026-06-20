"""Tests for the source registry: store aggregation + management routes."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from opendomainmcp.api import source_routes
from opendomainmcp.config import Settings
from opendomainmcp.context import Context
from opendomainmcp.models import Chunk, KnowledgeUnit


def _seed_two_sources(store):
    """Insert chunks from two sources with mixed kinds and review statuses."""
    store.upsert([
        Chunk(text="alpha one", source="a.md", kind="text",
              knowledge=KnowledgeUnit(summary="a1", review_status="approved")),
        Chunk(text="alpha two", source="a.md", kind="code",
              knowledge=KnowledgeUnit(summary="a2", review_status="pending")),
        Chunk(text="alpha three", source="a.md", kind="text"),  # unset
        Chunk(text="beta one", source="b.md", kind="text",
              knowledge=KnowledgeUnit(summary="b1", review_status="rejected")),
    ])


# -- store level ---------------------------------------------------------

def test_list_sources_aggregates_counts_kinds_and_review(store):
    _seed_two_sources(store)

    sources = store.list_sources()

    assert [s["source"] for s in sources] == ["a.md", "b.md"]
    a, b = sources
    assert a["chunks"] == 3
    assert a["kinds"] == ["code", "text"]
    assert a["review"] == {"approved": 1, "pending": 1, "rejected": 0, "unset": 1}
    assert b["chunks"] == 1
    assert b["kinds"] == ["text"]
    assert b["review"] == {"approved": 0, "pending": 0, "rejected": 1, "unset": 0}


def test_delete_by_source_removes_only_that_source(store):
    _seed_two_sources(store)

    deleted = store.delete_by_source("a.md")

    assert deleted == 3
    remaining = store.list_sources()
    assert [s["source"] for s in remaining] == ["b.md"]
    assert remaining[0]["chunks"] == 1


def test_delete_by_source_fails_loud_on_empty(store):
    with pytest.raises(ValueError):
        store.delete_by_source("")


# -- API level -----------------------------------------------------------

@pytest.fixture
def api(store, pipeline, fake_graph, tmp_path):
    settings = Settings(data_dir=tmp_path)
    ctx = Context(settings=settings, store=store, pipeline=pipeline, graph=fake_graph)
    app = FastAPI()
    app.state.context = ctx
    app.include_router(source_routes.router)
    return TestClient(app), ctx


def test_get_sources_returns_registry(api):
    tc, ctx = api
    _seed_two_sources(ctx.store)

    data = tc.get("/api/sources").json()

    names = {s["source"] for s in data["sources"]}
    assert names == {"a.md", "b.md"}


def test_delete_source_removes_one_and_404s_on_unknown(api):
    tc, ctx = api
    _seed_two_sources(ctx.store)

    resp = tc.request("DELETE", "/api/sources", json={"source": "a.md"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"deleted": 3, "source": "a.md"}

    remaining = {s["source"] for s in tc.get("/api/sources").json()["sources"]}
    assert remaining == {"b.md"}

    missing = tc.request("DELETE", "/api/sources", json={"source": "ghost.md"})
    assert missing.status_code == 404
