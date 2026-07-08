import json

import pytest
from fastapi.testclient import TestClient

from opendomainmcp.api.app import create_app
from opendomainmcp.config import Settings
from opendomainmcp.context import Context


@pytest.fixture
def client(store, pipeline, fake_graph, tmp_path):
    settings = Settings(data_dir=tmp_path)
    ctx = Context(settings=settings, store=store, pipeline=pipeline, graph=fake_graph)
    app = create_app(context=ctx, context_factory=lambda: ctx)
    return TestClient(app), ctx, tmp_path


def _corpus(root):
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "notes.md").write_text("Vector databases store embeddings for search.\n")


def test_stats_empty(client):
    tc, _, _ = client
    data = tc.get("/api/stats").json()
    assert data["count"] == 0
    assert data["embedder"] == "fake"


def test_ingest_stream_then_search_and_items(client):
    tc, ctx, tmp_path = client
    src = tmp_path / "src"
    src.mkdir()
    _corpus(src)

    resp = tc.get("/api/ingest/stream", params={"path": str(src)})
    assert resp.status_code == 200
    reports = [
        json.loads(line[len("data:"):].strip())
        for line in resp.text.splitlines()
        if line.startswith("data:") and '"stage": "report"' in line
    ]
    assert reports and reports[0]["files_indexed"] == 2

    results = tc.post("/api/search", json={"query": "add numbers", "kind": "code"}).json()
    assert any(r["metadata"].get("symbol") == "add" for r in results)

    items = tc.get("/api/items").json()
    assert len(items) >= 1
    item_id = items[0]["id"]

    # edit metadata
    patched = tc.patch(
        f"/api/items/{item_id}",
        json={"metadata": {**items[0]["metadata"], "note": "reviewed"}},
    ).json()
    assert patched["metadata"]["note"] == "reviewed"

    # delete
    assert tc.delete(f"/api/items/{item_id}").json()["deleted"] == item_id
    assert tc.get(f"/api/items/{item_id}").status_code == 404


def test_upload(client):
    tc, ctx, _ = client
    resp = tc.post(
        "/api/upload",
        files=[("files", ("a.txt", b"hello world", "text/plain"))],
    ).json()
    assert resp["files"] == ["a.txt"]
    assert (resp["path"]).endswith(resp["path"].split("/")[-1])


def test_ask_stream_endpoint(client, monkeypatch):
    tc, ctx, _ = client
    from opendomainmcp.models import Chunk

    ctx.store.upsert([
        Chunk(text="vector database stores embeddings", source="a.md", kind="text"),
    ])

    import opendomainmcp.query.rag as rag

    def fake_stream(model, system, user, timeout=60.0, max_retries=2):
        yield "Hello "
        yield "world [1]."

    monkeypatch.setattr(rag, "_claude_synthesize_stream", fake_stream)

    resp = tc.get("/api/ask/stream", params={"query": "embeddings", "top_k": 1})
    assert resp.status_code == 200

    deltas, citations = [], None
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            ev = json.loads(line[len("data:"):].strip())
            if ev["type"] == "delta":
                deltas.append(ev["text"])
            elif ev["type"] == "citations":
                citations = ev["citations"]
    assert "".join(deltas) == "Hello world [1]."
    assert citations and citations[0]["source"] == "a.md"


def test_upload_streams_to_disk(client):
    tc, _, _ = client
    payload = b"x" * (3 * 1024 * 1024)  # 3 MB, under the default 50 MB limit
    resp = tc.post(
        "/api/upload",
        files=[("files", ("big.txt", payload, "text/plain"))],
    )
    assert resp.status_code == 200
    staged = resp.json()
    from pathlib import Path

    written = Path(staged["path"]) / "big.txt"
    assert written.read_bytes() == payload  # full content reached disk intact


def test_upload_over_limit_rejected(store, pipeline, fake_graph, tmp_path):
    settings = Settings(data_dir=tmp_path, max_upload_mb=1)
    ctx = Context(settings=settings, store=store, pipeline=pipeline, graph=fake_graph)
    tc = TestClient(create_app(context=ctx, context_factory=lambda: ctx))

    payload = b"y" * (2 * 1024 * 1024)  # 2 MB, over the 1 MB limit
    resp = tc.post(
        "/api/upload",
        files=[("files", ("toobig.txt", payload, "text/plain"))],
    )
    assert resp.status_code == 413
    # the partial file must not linger on disk
    assert not list((tmp_path / "uploads").rglob("toobig.txt"))


def test_collections_endpoint(client):
    tc, _, _ = client
    data = tc.get("/api/collections").json()
    assert "active" in data and isinstance(data["collections"], list)

    name = "apiproj_xyz"
    tc.post("/api/collections", json={"name": name})
    names = {c["name"] for c in tc.get("/api/collections").json()["collections"]}
    assert name in names

    assert tc.delete(f"/api/collections/{name}").json()["deleted"] == name


def test_review_workflow_approve_reject_and_filter(client):
    tc, ctx, _ = client
    from opendomainmcp.models import Chunk, KnowledgeUnit

    ctx.store.upsert([
        Chunk(text="pending feature one", source="a.md", kind="text",
              knowledge=KnowledgeUnit(summary="p1", knowledge_type="Feature",
                                      review_status="pending")),
        Chunk(text="pending feature two", source="b.md", kind="text",
              knowledge=KnowledgeUnit(summary="p2", knowledge_type="Feature",
                                      review_status="pending")),
    ])

    pending = tc.get("/api/items", params={"review_status": "pending"}).json()
    assert len(pending) == 2
    first = pending[0]["id"]

    approved = tc.post(f"/api/items/{first}/approve").json()
    assert approved["metadata"]["review_status"] == "approved"
    rejected = tc.post(f"/api/items/{pending[1]['id']}/reject").json()
    assert rejected["metadata"]["review_status"] == "rejected"

    still_pending = tc.get("/api/items", params={"review_status": "pending"}).json()
    assert still_pending == []
    assert tc.post("/api/items/nope/approve").status_code == 404


def test_manual_add_item_is_approved(client):
    tc, _, _ = client
    created = tc.post("/api/items", json={
        "text": "Customers on the free tier cannot export to PDF.",
        "knowledge_type": "Constraint",
        "audience": ["product_manager"],
        "tags": ["billing"],
    }).json()
    assert created["metadata"]["knowledge_type"] == "Constraint"
    assert created["metadata"]["review_status"] == "approved"
    assert created["metadata"]["audience"] == "product_manager"


def test_search_approved_only_policy(store, pipeline, fake_graph, tmp_path):
    settings = Settings(data_dir=tmp_path, retrieve_approved_only=True)
    ctx = Context(settings=settings, store=store, pipeline=pipeline, graph=fake_graph)
    tc = TestClient(create_app(context=ctx, context_factory=lambda: ctx))
    from opendomainmcp.models import Chunk, KnowledgeUnit

    store.upsert([
        Chunk(text="approved widget knowledge", source="a.md", kind="text",
              knowledge=KnowledgeUnit(summary="a", knowledge_type="Feature",
                                      review_status="approved")),
        Chunk(text="pending widget knowledge", source="b.md", kind="text",
              knowledge=KnowledgeUnit(summary="b", knowledge_type="Feature",
                                      review_status="pending")),
    ])
    results = tc.post("/api/search", json={"query": "widget knowledge"}).json()
    assert results and all(
        r["metadata"].get("review_status") == "approved" for r in results
    )


def test_views_endpoint_lists_all_views(client):
    tc, _, _ = client
    data = tc.get("/api/views").json()
    assert set(data) == {"product", "operations", "developer", "support", "architecture"}
    product_tools = {t["name"] for t in data["product"]["tools"]}
    assert "get_feature" in product_tools


def test_simulate_endpoint_returns_grounding(client):
    tc, ctx, _ = client
    from opendomainmcp.models import Chunk, KnowledgeUnit

    ctx.store.upsert([
        Chunk(text="users can export reports to PDF", source="a.md", kind="text",
              knowledge=KnowledgeUnit(summary="export", knowledge_type="Feature",
                                      audience=["product_manager"])),
    ])
    data = tc.post("/api/simulate", json={
        "view": "product", "query": "export reports", "top_k": 3,
    }).json()
    assert data["view"] == "product"
    assert any(t["results"] for t in data["tools"])
    assert data["grounding"]["hits"] >= 1
    assert "Feature" in data["grounding"]["knowledge_types"]
    assert tc.post("/api/simulate", json={"view": "nope", "query": "x"}).status_code == 404


def test_api_search_returns_article_hit(client, monkeypatch):
    tc, ctx, _ = client
    from opendomainmcp.models import SearchResult

    def fake_unified(store, query, *, top_k, mode, settings, where=None,
                     source_contains=None):
        return [SearchResult(id="art", text="body", score=0.9,
                             metadata={"kind": "article", "title": "Order Rule"})]

    monkeypatch.setattr("opendomainmcp.retrieval.search_unified", fake_unified)
    resp = tc.post("/api/search", json={"query": "approval", "top_k": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert any(item["metadata"].get("kind") == "article" for item in data)


def test_list_articles_returns_mapped_records(client):
    from opendomainmcp.models import Article
    tc, ctx, _ = client
    arts = ctx.store.sibling(f"{ctx.store.stats()['collection']}__articles")
    arts.upsert([Article(
        title="Order Approval Rule", topic="order approval",
        body="Orders above $10k require manager sign-off [1].",
        business_relevance=0.8, source_chunk_ids=["a"],
        sources=["rules.md:1", "approve.py:5"], cross_validated=True,
        critic_verdict={"grounded": True, "business_meaningful": True})])
    data = tc.get("/api/articles").json()
    assert len(data) == 1
    a = data[0]
    assert a["title"] == "Order Approval Rule"
    assert a["topic"] == "order approval"
    assert a["business_relevance"] == 0.8
    assert a["cross_validated"] is True
    assert a["sources"] == ["rules.md:1", "approve.py:5"]
    assert "manager sign-off" in a["body"]


def test_list_articles_empty_when_none(client):
    tc, _, _ = client
    assert tc.get("/api/articles").json() == []


def test_settings_roundtrip_and_validation(client):
    tc, _, _ = client
    assert tc.get("/api/settings").json()["editable"]["chunk_size"] == 1200
    ok = tc.patch("/api/settings", json={"values": {"chunk_size": 777}})
    assert ok.json()["updated"] == ["chunk_size"]
    bad = tc.patch("/api/settings", json={"values": {"data_dir": "/etc"}})
    assert bad.status_code == 400


def _seed_pending(ctx, n=3):
    from opendomainmcp.models import Chunk, KnowledgeUnit
    chunks = [Chunk(text=f"pending {i}", source=f"s{i}.md", kind="text",
                    knowledge=KnowledgeUnit(summary=f"p{i}", knowledge_type="Feature",
                                            review_status="pending"))
              for i in range(n)]
    ctx.store.upsert(chunks)
    return [c.id for c in chunks]


def test_approve_writes_audit_with_note_and_actor(client):
    tc, ctx, _ = client
    ids = _seed_pending(ctx, 1)
    r = tc.post(f"/api/items/{ids[0]}/approve", json={"note": "verified by hand"})
    assert r.json()["metadata"]["review_status"] == "approved"
    hist = tc.get(f"/api/items/{ids[0]}/history").json()
    assert len(hist) == 1
    assert hist[0]["action"] == "approve" and hist[0]["note"] == "verified by hand"
    assert hist[0]["prev_status"] == "pending" and hist[0]["new_status"] == "approved"
    assert hist[0]["actor"] == "local"   # auth off in tests


def test_reject_without_note_defaults_empty(client):
    tc, ctx, _ = client
    ids = _seed_pending(ctx, 1)
    tc.post(f"/api/items/{ids[0]}/reject")
    hist = tc.get(f"/api/items/{ids[0]}/history").json()
    assert hist[0]["action"] == "reject" and hist[0]["note"] == ""


def test_review_batch_applies_and_reports_missing(client):
    tc, ctx, _ = client
    ids = _seed_pending(ctx, 3)
    r = tc.post("/api/items/review-batch",
                json={"ids": ids + ["nope"], "action": "approve", "note": "bulk"})
    body = r.json()
    assert set(body["updated"]) == set(ids) and body["missing"] == ["nope"]
    pending = tc.get("/api/items", params={"review_status": "pending"}).json()
    assert pending == []
    for i in ids:
        h = tc.get(f"/api/items/{i}/history").json()
        assert h[0]["note"] == "bulk" and h[0]["action"] == "approve"


def test_review_batch_rejects_bad_action(client):
    tc, ctx, _ = client
    assert tc.post("/api/items/review-batch",
                   json={"ids": [], "action": "purge"}).status_code == 400


def test_history_newest_first(client):
    tc, ctx, _ = client
    ids = _seed_pending(ctx, 1)
    tc.post(f"/api/items/{ids[0]}/approve")
    tc.post(f"/api/items/{ids[0]}/reject")
    hist = tc.get(f"/api/items/{ids[0]}/history").json()
    assert [h["action"] for h in hist] == ["reject", "approve"]
