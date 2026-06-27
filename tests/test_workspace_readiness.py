from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from opendomainmcp.api import workspace_routes
from opendomainmcp.api.app import create_app
from opendomainmcp.config import Settings
from opendomainmcp.context import Context
from opendomainmcp.graph.models import Entity, WorkflowStep
from opendomainmcp.models import Chunk, KnowledgeUnit
from opendomainmcp.quality import compute_readiness
from opendomainmcp.tasks.store import TaskStore


EXPECTED_KEYS = {
    "collection",
    "status",
    "score",
    "next_action",
    "blockers",
    "warnings",
    "stats",
    "source_health",
    "review_health",
    "job_health",
    "graph_health",
}

EMPTY_JOB_HEALTH = {
    "queued": 0,
    "running": 0,
    "done": 0,
    "error": 0,
    "cancelled": 0,
}


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

    assert set(readiness) == EXPECTED_KEYS
    assert readiness["status"] == "blocked"
    assert readiness["score"] == 0
    assert readiness["blockers"] == ["No indexed knowledge objects."]
    assert readiness["next_action"] == "Add sources in Source Intake."
    assert readiness["stats"] == {"count": 0, "embedder": "fake", "dim": 64}
    assert readiness["source_health"] == {
        "sources": 0,
        "chunks": 0,
        "stale": 0,
        "failed": 0,
    }
    assert readiness["review_health"] == {
        "approved": 0,
        "pending": 0,
        "rejected": 0,
        "unset": 0,
        "approved_ratio": 0,
    }
    assert readiness["job_health"] == EMPTY_JOB_HEALTH
    assert readiness["graph_health"] == {
        "available": True,
        "entities": 0,
        "workflows": 0,
    }


def test_empty_collection_with_running_job_is_validating(ctx):
    readiness = compute_readiness(ctx, tasks=[{"status": "running"}])

    assert readiness["status"] == "validating"
    assert readiness["next_action"] == "Wait for background jobs to finish."
    assert readiness["job_health"] == {
        **EMPTY_JOB_HEALTH,
        "running": 1,
    }


def test_empty_collection_with_queued_job_is_validating(ctx):
    readiness = compute_readiness(ctx, tasks=[{"status": "queued"}])

    assert readiness["status"] == "validating"
    assert readiness["next_action"] == "Wait for background jobs to finish."
    assert readiness["job_health"] == {
        **EMPTY_JOB_HEALTH,
        "queued": 1,
    }


def test_empty_collection_with_failed_job_prioritizes_failed_job_action(ctx):
    readiness = compute_readiness(ctx, tasks=[{"status": "error"}])

    assert readiness["status"] == "blocked"
    assert readiness["next_action"] == "Inspect failed background jobs."
    assert "1 background job failed." in readiness["blockers"]
    assert "No indexed knowledge objects." in readiness["blockers"]


def test_pending_review_marks_collection_needs_review(ctx):
    ctx.store.upsert(
        [
            _chunk("approved knowledge", "approved.md", "approved"),
            _chunk("pending knowledge", "pending.md", "pending"),
        ]
    )

    readiness = compute_readiness(ctx, tasks=[])

    assert readiness["status"] == "needs_review"
    assert readiness["review_health"]["approved_ratio"] == 0.5
    assert readiness["warnings"] == ["1 knowledge object is pending review."]


def test_readiness_response_contains_full_contract(ctx):
    ctx.store.upsert(
        [
            _chunk("approved knowledge", "a.md", "approved"),
            _chunk("pending knowledge", "a.md", "pending"),
            _chunk("rejected knowledge", "b.md", "rejected"),
            _chunk("unset knowledge", "c.md", "unset"),
        ]
    )
    ctx.graph.upsert_entities([Entity("svc", "Svc", "Service", "c1")])
    ctx.graph.upsert_workflow(
        "Deploy",
        "c1",
        0,
        [WorkflowStep(1, "Ship release")],
        [],
    )

    readiness = compute_readiness(
        ctx,
        tasks=[{"status": "done"}, {"status": "cancelled"}],
    )

    assert set(readiness) == EXPECTED_KEYS
    assert readiness["collection"] == ctx.store.stats()["collection"]
    assert readiness["stats"] == {"count": 4, "embedder": "fake", "dim": 64}
    assert readiness["source_health"] == {
        "sources": 3,
        "chunks": 4,
        "stale": 0,
        "failed": 0,
    }
    assert readiness["review_health"] == {
        "approved": 1,
        "pending": 1,
        "rejected": 1,
        "unset": 1,
        "approved_ratio": 0.25,
    }
    assert readiness["job_health"] == {
        **EMPTY_JOB_HEALTH,
        "done": 1,
        "cancelled": 1,
    }
    assert readiness["graph_health"] == {
        "available": True,
        "entities": 1,
        "workflows": 1,
    }


def test_graph_health_degrades_when_graph_store_fails(ctx):
    def fail_graph_call(**_kwargs):
        raise RuntimeError("graph down")

    ctx.store.upsert([_chunk("approved knowledge", "approved.md", "approved")])
    ctx.graph = SimpleNamespace(
        list_entities=fail_graph_call,
        list_workflows=fail_graph_call,
    )

    readiness = compute_readiness(ctx, tasks=[])

    assert readiness["graph_health"] == {
        "available": False,
        "entities": 0,
        "workflows": 0,
    }


def test_readiness_does_not_fetch_full_documents(ctx, monkeypatch):
    ctx.store.upsert([_chunk("approved knowledge", "approved.md", "approved")])

    def fail_get_items(*args, **kwargs):
        raise AssertionError("readiness must not fetch full documents")

    monkeypatch.setattr(ctx.store, "get_items", fail_get_items)

    readiness = compute_readiness(ctx, tasks=[])

    assert readiness["status"] == "ready"
    assert readiness["review_health"]["approved"] == 1


def test_job_health_zero_fills_known_statuses_and_validates_active_jobs(ctx):
    ctx.store.upsert([_chunk("approved knowledge", "approved.md", "approved")])

    readiness = compute_readiness(ctx, tasks=[{"status": "running"}])

    assert readiness["status"] == "validating"
    assert readiness["next_action"] == "Wait for background jobs to finish."
    assert readiness["job_health"] == {
        **EMPTY_JOB_HEALTH,
        "running": 1,
    }


def test_collection_with_no_approved_knowledge_needs_review(ctx):
    ctx.store.upsert(
        [
            _chunk("rejected knowledge", "rejected.md", "rejected"),
            _chunk("unset knowledge", "unset.md", "unset"),
        ]
    )

    readiness = compute_readiness(ctx, tasks=[])

    assert readiness["status"] == "needs_review"
    assert readiness["score"] == 0
    assert readiness["blockers"] == []
    assert "No approved knowledge objects." in readiness["warnings"]
    assert readiness["next_action"] == "Review and approve knowledge objects."


def test_mixed_rejected_knowledge_needs_review(ctx):
    ctx.store.upsert(
        [
            _chunk("approved knowledge", "approved.md", "approved"),
            _chunk("rejected knowledge", "rejected.md", "rejected"),
        ]
    )

    readiness = compute_readiness(ctx, tasks=[])

    assert readiness["status"] == "needs_review"
    assert readiness["warnings"] == ["1 knowledge object was rejected."]
    assert readiness["next_action"] == "Review rejected or unclassified knowledge objects."


def test_mixed_unset_knowledge_needs_review(ctx):
    ctx.store.upsert(
        [
            _chunk("approved knowledge", "approved.md", "approved"),
            _chunk("unset knowledge", "unset.md", "unset"),
        ]
    )

    readiness = compute_readiness(ctx, tasks=[])

    assert readiness["status"] == "needs_review"
    assert readiness["warnings"] == ["1 knowledge object is unreviewed."]
    assert readiness["next_action"] == "Review rejected or unclassified knowledge objects."


def test_failed_jobs_block_readiness(ctx):
    ctx.store.upsert([_chunk("approved knowledge", "approved.md", "approved")])

    readiness = compute_readiness(
        ctx,
        tasks=[{"status": "error"}, {"status": "done"}],
    )

    assert readiness["status"] == "blocked"
    assert readiness["job_health"] == {
        **EMPTY_JOB_HEALTH,
        "done": 1,
        "error": 1,
    }
    assert readiness["blockers"] == ["1 background job failed."]


def test_multiple_failed_jobs_use_plural_blocker_text(ctx):
    ctx.store.upsert([_chunk("approved knowledge", "approved.md", "approved")])

    readiness = compute_readiness(
        ctx,
        tasks=[{"status": "error"}, {"status": "error"}],
    )

    assert readiness["blockers"] == ["2 background jobs failed."]


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


def test_create_app_mounts_workspace_readiness_endpoint(ctx):
    app = create_app(context=ctx, context_factory=lambda **_kwargs: ctx)
    client = TestClient(app)

    resp = client.get("/api/workspace/readiness")

    assert resp.status_code == 200
    assert set(resp.json()) == EXPECTED_KEYS


def test_workspace_readiness_uses_app_task_store_and_filters_collection(ctx):
    app = FastAPI()
    app.state.context = ctx
    app.state.contexts = {}
    app.state.task_store = TaskStore(ctx.settings.data_dir / "app-tasks")
    current_collection = ctx.store.stats()["collection"]
    current = app.state.task_store.create(
        "ingest",
        "Current collection ingest",
        current_collection,
        {},
    )
    other = app.state.task_store.create(
        "ingest",
        "Other collection ingest",
        "other_collection",
        {},
    )
    app.state.task_store.update(current.id, status="running")
    app.state.task_store.update(other.id, status="error")
    ctx.store.upsert([_chunk("approved knowledge", "approved.md", "approved")])
    app.include_router(workspace_routes.router)
    client = TestClient(app)

    resp = client.get("/api/workspace/readiness")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "validating"
    assert data["job_health"] == {
        **EMPTY_JOB_HEALTH,
        "running": 1,
    }
    assert data["blockers"] == []


def test_workspace_readiness_degrades_when_task_store_cannot_load(ctx):
    app = FastAPI()
    app.state.context = ctx
    app.state.contexts = {}
    (ctx.settings.data_dir / "tasks.json").write_text("{not-json", encoding="utf-8")
    app.include_router(workspace_routes.router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/workspace/readiness")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "blocked"
    assert data["job_health"] == {
        **EMPTY_JOB_HEALTH,
        "error": 1,
    }
    assert "1 background job failed." in data["blockers"]


def test_workspace_readiness_degrades_when_task_store_list_fails(ctx):
    class BrokenTaskStore:
        def list(self):
            raise RuntimeError("task history unavailable")

    app = FastAPI()
    app.state.context = ctx
    app.state.contexts = {}
    app.state.task_store = BrokenTaskStore()
    app.include_router(workspace_routes.router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/workspace/readiness")

    assert resp.status_code == 200
    data = resp.json()
    assert data["job_health"] == {
        **EMPTY_JOB_HEALTH,
        "error": 1,
    }
    assert "1 background job failed." in data["blockers"]


def test_workspace_readiness_degrades_when_task_row_conversion_fails(ctx):
    class BrokenTask:
        def to_dict(self):
            raise RuntimeError("bad task row")

    class BrokenTaskStore:
        def list(self):
            return [BrokenTask()]

    app = FastAPI()
    app.state.context = ctx
    app.state.contexts = {}
    app.state.task_store = BrokenTaskStore()
    app.include_router(workspace_routes.router)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/workspace/readiness")

    assert resp.status_code == 200
    data = resp.json()
    assert data["job_health"] == {
        **EMPTY_JOB_HEALTH,
        "error": 1,
    }
    assert "1 background job failed." in data["blockers"]
