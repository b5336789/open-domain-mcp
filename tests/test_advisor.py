"""Tests for the Pre-Execution Advisor aggregator (TASKS #5.1)."""

from types import SimpleNamespace

import pytest

from opendomainmcp.advisor import advise
from opendomainmcp.config import Settings
from opendomainmcp.models import Chunk, KnowledgeUnit


def _ctx(store, graph, **settings):
    return SimpleNamespace(store=store, graph=graph, settings=Settings(**settings))


def _seed(store):
    store.upsert([
        Chunk(text="deploy service workflow: build then ship the release",
              source="wf.md", kind="text",
              knowledge=KnowledgeUnit(summary="deploy workflow",
                                      knowledge_type="Workflow")),
        Chunk(text="deploy release runbook restart the worker pool",
              source="rb.md", kind="text",
              knowledge=KnowledgeUnit(summary="deploy runbook",
                                      knowledge_type="Runbook")),
        Chunk(text="deploy release requires the deployer role permission",
              source="pm.md", kind="text",
              knowledge=KnowledgeUnit(summary="deploy permission",
                                      knowledge_type="Permission")),
        Chunk(text="deploy release constraint: cannot deploy during freeze window",
              source="ct.md", kind="text",
              knowledge=KnowledgeUnit(summary="deploy constraint",
                                      knowledge_type="Constraint")),
        Chunk(text="deploy release error: connection refused on rollout",
              source="er.md", kind="text",
              knowledge=KnowledgeUnit(summary="deploy error",
                                      knowledge_type="Error")),
    ])


def test_advise_buckets_results_into_facets(store, fake_graph):
    _seed(store)
    ctx = _ctx(store, fake_graph)

    result = advise(ctx, "deploy release", top_k=5)

    assert result["action"] == "deploy release"
    assert {r["metadata"]["knowledge_type"] for r in result["workflow"]} <= {
        "Workflow", "Runbook"}
    assert any(r["metadata"]["knowledge_type"] == "Workflow" for r in result["workflow"])
    assert any(r["metadata"]["knowledge_type"] == "Runbook" for r in result["workflow"])
    assert all(r["metadata"]["knowledge_type"] == "Permission"
               for r in result["permissions"])
    assert all(r["metadata"]["knowledge_type"] == "Constraint"
               for r in result["constraints"])
    assert {r["metadata"]["knowledge_type"] for r in result["risks"]} <= {
        "Error", "Troubleshooting", "Constraint"}


def test_summary_counts_match_facets(store, fake_graph):
    _seed(store)
    ctx = _ctx(store, fake_graph)

    result = advise(ctx, "deploy release", top_k=5)
    counts = result["summary"]["counts"]

    for facet in ("workflow", "risks", "permissions", "dependencies", "constraints"):
        assert counts[facet] == len(result[facet])
    assert "Permission" in result["summary"]["knowledge_types"]
    assert result["summary"]["knowledge_types"] == sorted(
        result["summary"]["knowledge_types"])


def test_facets_dedupe_by_id(store, fake_graph):
    # Constraint appears in both the risks and constraints facets, but each facet
    # must not contain the same chunk id twice.
    _seed(store)
    ctx = _ctx(store, fake_graph)

    result = advise(ctx, "deploy release", top_k=5)

    risk_ids = [r["id"] for r in result["risks"]]
    assert len(risk_ids) == len(set(risk_ids))


def test_retrieve_approved_only_filters_unapproved(store, fake_graph):
    store.upsert([
        Chunk(text="deploy approved permission grant", source="ok.md", kind="text",
              knowledge=KnowledgeUnit(summary="ok", knowledge_type="Permission",
                                      review_status="approved")),
        Chunk(text="deploy pending permission grant", source="no.md", kind="text",
              knowledge=KnowledgeUnit(summary="no", knowledge_type="Permission",
                                      review_status="pending")),
    ])
    ctx = _ctx(store, fake_graph, retrieve_approved_only=True)

    result = advise(ctx, "deploy permission grant", top_k=5)

    assert result["permissions"]
    assert all(r["metadata"]["review_status"] == "approved"
               for r in result["permissions"])


def test_empty_action_fails_loud(store, fake_graph):
    ctx = _ctx(store, fake_graph)
    with pytest.raises(ValueError):
        advise(ctx, "   ", top_k=5)


def test_dependencies_empty_graph_yields_empty(store, fake_graph):
    _seed(store)
    ctx = _ctx(store, fake_graph)

    result = advise(ctx, "deploy release", top_k=5)

    # No graph entities/edges and no Architecture knowledge were seeded.
    assert result["dependencies"] == []
    assert result["graph_workflow"] is None
