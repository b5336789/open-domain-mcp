from opendomainmcp.models import Chunk, KnowledgeUnit
from opendomainmcp.store import build_where


def _seed(store):
    store.upsert([
        Chunk(text="vector similarity search over embeddings", source="docs/a.md", kind="text"),
        Chunk(text="lazy dog sleeps in the sun", source="docs/b.md", kind="text"),
        Chunk(text="def build_where(filters): return clause", source="src/store.py",
              kind="code", language="python", symbol="build_where"),
    ])


def test_hybrid_returns_results(store):
    _seed(store)
    res = store.search("similarity search embeddings", top_k=2, mode="hybrid")
    assert res and res[0].metadata["source"] == "docs/a.md"


def test_hybrid_exact_symbol_via_bm25(store):
    _seed(store)
    # 'build_where' is a rare identifier; BM25 should pull the code chunk in.
    res = store.search("build_where", top_k=3, mode="hybrid")
    assert any(r.metadata.get("symbol") == "build_where" for r in res)


def test_language_filter(store):
    _seed(store)
    where = build_where({"language": "python"})
    res = store.search("function", top_k=5, where=where, mode="hybrid")
    assert res and all(r.metadata.get("language") == "python" for r in res)


def test_source_contains_filter(store):
    _seed(store)
    res = store.search("sun dog", top_k=5, mode="hybrid", source_contains="b.md")
    assert res and all("b.md" in r.metadata["source"] for r in res)


def test_vector_mode_still_default(store):
    _seed(store)
    res = store.search("embeddings", top_k=1)  # mode defaults to vector
    assert res and 0.0 <= res[0].score <= 1.0


def _seed_classified(store):
    store.upsert([
        Chunk(text="users can export reports as PDF", source="a.md", kind="text",
              knowledge=KnowledgeUnit(summary="export", knowledge_type="Feature",
                                      review_status="approved")),
        Chunk(text="restart the worker pool to clear the queue", source="b.md", kind="text",
              knowledge=KnowledgeUnit(summary="restart", knowledge_type="Runbook",
                                      review_status="pending")),
    ])


def test_knowledge_type_filter(store):
    _seed_classified(store)
    where = build_where({"knowledge_type": "Feature"})
    res = store.search("export reports queue", top_k=5, where=where, mode="hybrid")
    assert res and all(r.metadata.get("knowledge_type") == "Feature" for r in res)


def test_review_status_filter(store):
    _seed_classified(store)
    where = build_where({"review_status": "approved"})
    res = store.search("export restart", top_k=5, where=where, mode="hybrid")
    assert res and all(r.metadata.get("review_status") == "approved" for r in res)
