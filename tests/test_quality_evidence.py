from __future__ import annotations

from fastapi.testclient import TestClient

from opendomainmcp.api.app import create_app
from opendomainmcp.config import Settings
from opendomainmcp.context import Context
from opendomainmcp.graph.models import Entity, WorkflowStep
from opendomainmcp.metrics import MetricsRecorder
from opendomainmcp.models import Article, Chunk, KnowledgeUnit
from opendomainmcp.quality import compute_quality_evidence


def _ctx(store, pipeline, fake_graph, tmp_path) -> Context:
    return Context(
        settings=Settings(data_dir=tmp_path),
        store=store,
        pipeline=pipeline,
        graph=fake_graph,
    )


def _chunk(text: str, source: str, status: str) -> Chunk:
    return Chunk(
        text=text,
        source=source,
        knowledge=KnowledgeUnit(summary=text, review_status=status),
    )


def _card(payload: dict, card_id: str) -> dict:
    cards = {card["id"]: card for card in payload["evidence"]}
    return cards[card_id]


def test_quality_evidence_returns_gate_cards_for_empty_collection(
    store, pipeline, fake_graph, tmp_path
):
    ctx = _ctx(store, pipeline, fake_graph, tmp_path)

    payload = compute_quality_evidence(ctx, tasks=[])

    assert payload["collection"] == store.stats()["collection"]
    assert payload["status"] == "blocked"
    assert payload["score"] == 0
    assert payload["next_action"] == "Add sources in Source Intake."
    assert [card["id"] for card in payload["evidence"]] == [
        "coverage",
        "review",
        "articles",
        "retrieval",
        "graph",
        "simulation",
        "policy",
        "jobs",
    ]
    assert _card(payload, "coverage") == {
        "id": "coverage",
        "gate": "Coverage",
        "status": "blocked",
        "score": 0,
        "summary": "No indexed knowledge objects.",
        "details": ["0 sources", "0 chunks", "0 stale", "0 failed"],
        "action": "Add sources in Source Intake.",
    }
    assert _card(payload, "articles")["status"] == "needs_review"
    assert _card(payload, "articles")["summary"] == "No synthesized articles."
    assert _card(payload, "retrieval")["status"] == "validating"
    assert _card(payload, "retrieval")["summary"] == "No retrieval evidence recorded."
    assert _card(payload, "simulation") == {
        "id": "simulation",
        "gate": "Simulation",
        "status": "validating",
        "score": 0,
        "summary": "No validation scenarios have been run.",
        "details": ["0 scenarios", "0 latest runs", "0 passed", "0 failed"],
        "action": "Run validation scenarios in Agent Simulator.",
    }


def test_policy_gate_is_ready_for_approved_only_supported_search_mode(
    store, pipeline, fake_graph, tmp_path
):
    ctx = Context(
        settings=Settings(
            data_dir=tmp_path,
            retrieve_approved_only=True,
            search_mode="hybrid",
            rerank_enabled=True,
        ),
        store=store,
        pipeline=pipeline,
        graph=fake_graph,
    )

    policy = _card(compute_quality_evidence(ctx, tasks=[]), "policy")

    assert policy == {
        "id": "policy",
        "gate": "Policy",
        "status": "ready",
        "score": 100,
        "summary": "Published MCP views use approved-only hybrid retrieval.",
        "details": [
            "approved-only on",
            "search mode hybrid",
            "rerank on",
            "auth disabled",
        ],
        "action": "Policy gate is clear.",
    }


def test_policy_gate_needs_review_when_approved_only_is_disabled(
    store, pipeline, fake_graph, tmp_path
):
    ctx = Context(
        settings=Settings(
            data_dir=tmp_path,
            retrieve_approved_only=False,
            search_mode="hybrid",
        ),
        store=store,
        pipeline=pipeline,
        graph=fake_graph,
    )

    policy = _card(compute_quality_evidence(ctx, tasks=[]), "policy")

    assert policy["status"] == "needs_review"
    assert policy["score"] == 60
    assert policy["summary"] == "Published MCP views may include unapproved knowledge."
    assert policy["action"] == "Enable approved-only retrieval before publishing."


def test_policy_gate_needs_review_for_unsupported_search_mode(
    store, pipeline, fake_graph, tmp_path
):
    ctx = Context(
        settings=Settings(
            data_dir=tmp_path,
            retrieve_approved_only=True,
            search_mode="keyword",
        ),
        store=store,
        pipeline=pipeline,
        graph=fake_graph,
    )

    policy = _card(compute_quality_evidence(ctx, tasks=[]), "policy")

    assert policy["status"] == "needs_review"
    assert policy["score"] == 70
    assert policy["summary"] == "Search mode keyword is not publish-safe."
    assert policy["action"] == "Select hybrid or vector search mode."


def test_policy_gate_reports_auth_enabled_key_scope(
    store, pipeline, fake_graph, tmp_path
):
    ctx = Context(
        settings=Settings(
            data_dir=tmp_path,
            retrieve_approved_only=True,
            auth_enabled=True,
            api_keys="admin:admin:*,dev:developer:developer|architecture",
        ),
        store=store,
        pipeline=pipeline,
        graph=fake_graph,
    )

    details = _card(compute_quality_evidence(ctx, tasks=[]), "policy")["details"]

    assert "auth enabled" in details
    assert "2 API keys configured" in details
    assert "view scopes configured" in details


def test_quality_evidence_summarizes_review_articles_retrieval_and_graph(
    store, pipeline, fake_graph, tmp_path
):
    ctx = _ctx(store, pipeline, fake_graph, tmp_path)
    store.upsert(
        [
            _chunk("approved deployment knowledge", "docs/deploy.md", "approved"),
            _chunk("pending rollout knowledge", "docs/deploy.md", "pending"),
        ]
    )
    articles = store.sibling(f"{store.stats()['collection']}__articles")
    articles.upsert(
        [
            Article(
                title="Deployments",
                topic="deployments",
                body="Deployment article [1]",
                business_relevance=0.9,
                sources=["docs/deploy.md"],
                cross_validated=True,
            ),
            Article(
                title="Rollbacks",
                topic="rollbacks",
                body="Rollback article [1]",
                business_relevance=0.7,
                sources=["docs/rollback.md"],
                cross_validated=False,
            ),
        ]
    )
    MetricsRecorder(ctx.settings.data_dir).record_search(
        "deploy", hits=2, scores=[0.8, 0.6], knowledge_types=["Runbook"]
    )
    fake_graph.upsert_entities([Entity("svc", "Service", "Service", "c1")])
    fake_graph.upsert_workflow(
        "Deploy",
        "c1",
        0,
        [WorkflowStep(1, "Ship release")],
        [],
    )

    payload = compute_quality_evidence(ctx, tasks=[{"status": "done"}])

    assert payload["status"] == "validating"
    assert _card(payload, "coverage")["status"] == "ready"
    assert _card(payload, "review") == {
        "id": "review",
        "gate": "Review",
        "status": "needs_review",
        "score": 50,
        "summary": "1 of 2 knowledge objects are approved.",
        "details": ["1 pending", "0 rejected", "0 unreviewed"],
        "action": "Review pending knowledge objects.",
    }
    assert _card(payload, "articles") == {
        "id": "articles",
        "gate": "Articles",
        "status": "needs_review",
        "score": 80,
        "summary": "2 synthesized articles, 1 cross-validated.",
        "details": ["average relevance 80%", "1 needs curation"],
        "action": "Curate synthesized articles.",
    }
    assert _card(payload, "retrieval") == {
        "id": "retrieval",
        "gate": "Retrieval",
        "status": "ready",
        "score": 100,
        "summary": "1 retrieval events with 100% grounding hit rate.",
        "details": ["average score 70%", "precision 100%"],
        "action": "Keep validating with representative scenarios.",
    }
    assert _card(payload, "graph")["status"] == "ready"
    assert _card(payload, "jobs")["status"] == "ready"


def test_quality_evidence_top_level_status_reflects_unready_gates(
    store, pipeline, fake_graph, tmp_path
):
    ctx = _ctx(store, pipeline, fake_graph, tmp_path)
    store.upsert([_chunk("approved knowledge", "approved.md", "approved")])

    payload = compute_quality_evidence(ctx, tasks=[])

    assert _card(payload, "coverage")["status"] == "ready"
    assert _card(payload, "review")["status"] == "ready"
    assert _card(payload, "articles")["status"] == "needs_review"
    assert _card(payload, "retrieval")["status"] == "validating"
    assert _card(payload, "graph")["status"] == "needs_review"
    assert _card(payload, "simulation")["status"] == "validating"
    assert _card(payload, "policy")["status"] == "needs_review"
    assert payload["status"] == "validating"
    assert payload["score"] == 51
    assert payload["next_action"] == "Run Advisor or Simulator scenarios."


def test_quality_evidence_summarizes_validation_runs(
    store, pipeline, fake_graph, tmp_path
):
    from opendomainmcp.validation import ValidationStore, build_run, build_scenario

    ctx = _ctx(store, pipeline, fake_graph, tmp_path)
    scenario = build_scenario(
        collection=store.stats()["collection"],
        view="product",
        name="Rollback",
        query="rollback",
    )
    validation = ValidationStore(tmp_path)
    validation.append_scenario(scenario)
    validation.append_run(
        build_run(
            collection=store.stats()["collection"],
            scenario=scenario,
            result={
                "view": "product",
                "grounding": {
                    "hits": 2,
                    "avg_score": 0.8,
                    "knowledge_types": ["Runbook"],
                },
                "tools": [{"tool": "search_features", "results": [{"id": "1"}]}],
            },
        )
    )

    payload = compute_quality_evidence(ctx, tasks=[])

    assert _card(payload, "simulation") == {
        "id": "simulation",
        "gate": "Simulation",
        "status": "ready",
        "score": 100,
        "summary": "1 validation scenario passed.",
        "details": ["1 scenario", "1 latest run", "1 passed", "0 failed"],
        "action": "Simulation gate is clear.",
    }


def test_quality_evidence_blocks_on_failed_validation_run(
    store, pipeline, fake_graph, tmp_path
):
    from opendomainmcp.validation import ValidationStore, build_run, build_scenario

    ctx = _ctx(store, pipeline, fake_graph, tmp_path)
    scenario = build_scenario(
        collection=store.stats()["collection"],
        view="product",
        name="Rollback",
        query="rollback",
    )
    validation = ValidationStore(tmp_path)
    validation.append_scenario(scenario)
    validation.append_run(
        build_run(
            collection=store.stats()["collection"],
            scenario=scenario,
            result={"view": "product", "grounding": {"hits": 0}, "tools": []},
        )
    )

    payload = compute_quality_evidence(ctx, tasks=[])

    assert _card(payload, "simulation")["status"] == "blocked"
    assert _card(payload, "simulation")["summary"] == "1 validation scenario failed."
    assert payload["status"] == "blocked"


def test_quality_evidence_api_contract(store, pipeline, fake_graph, tmp_path):
    ctx = _ctx(store, pipeline, fake_graph, tmp_path)
    app = create_app(context=ctx, context_factory=lambda **_: ctx)
    client = TestClient(app)

    response = client.get("/api/quality/evidence")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "collection",
        "status",
        "score",
        "next_action",
        "evidence",
    }
    assert _card(payload, "coverage")["gate"] == "Coverage"
