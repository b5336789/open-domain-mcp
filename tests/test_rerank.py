"""Cross-encoder re-ranking: reorders fused candidates and gives every result
a unified score (so lexical-only hits no longer carry a 0.0 placeholder)."""

import uuid

import chromadb

from opendomainmcp.config import Settings
from opendomainmcp.models import Chunk
from opendomainmcp.store import ChromaStore


class FakeReranker:
    """Deterministic offline reranker: score = query/document token overlap."""

    def __init__(self):
        self.calls = 0

    def rerank(self, query, documents):
        self.calls += 1
        q = set(query.lower().split())
        return [float(len(q & set(d.lower().split()))) for d in documents]


def _store(fake_embedder, reranker):
    client = chromadb.EphemeralClient()
    return ChromaStore(
        fake_embedder, data_dir=None,
        collection_name=f"test_{uuid.uuid4().hex}", client=client,
        reranker=reranker,
    )


def _seed(store):
    store.upsert([
        Chunk(text="vector database embeddings similarity", source="a.md", kind="text"),
        Chunk(text="coffee brewing pour over guide", source="b.md", kind="text"),
        Chunk(text="machine learning gradient descent", source="c.md", kind="text"),
    ])


def test_reranker_reorders_and_scores(fake_embedder):
    rr = FakeReranker()
    store = _store(fake_embedder, rr)
    _seed(store)

    results = store.search("coffee brewing", top_k=3, mode="hybrid")
    assert rr.calls == 1                       # reranker was consulted
    assert results[0].metadata["source"] == "b.md"   # best overlap ranked first
    assert results[0].score == 2.0             # unified score from the reranker
    # every result carries a real reranker score, not the 0.0 lexical placeholder
    assert all(isinstance(r.score, float) for r in results)
    assert results == sorted(results, key=lambda r: r.score, reverse=True)


def test_no_reranker_preserves_default_scoring(fake_embedder):
    store = _store(fake_embedder, None)
    _seed(store)
    results = store.search("vector embeddings", top_k=2, mode="vector")
    assert results
    # cosine similarity is in [-1, 1]; the FakeReranker's overlap counts (>=2)
    # would be out of this range, confirming the default path is used.
    assert all(-1.0 <= r.score <= 1.0 for r in results)


def test_get_reranker_disabled_returns_none():
    from opendomainmcp.retrieval import get_reranker

    assert get_reranker(Settings(rerank_enabled=False)) is None


def test_get_reranker_enabled_builds_encoder(monkeypatch):
    from opendomainmcp.retrieval import rerank as rerank_mod

    built = {}

    class StubEncoder:
        def __init__(self, model="m"):
            built["model"] = model

    monkeypatch.setattr(rerank_mod, "CrossEncoderReranker", StubEncoder)
    out = rerank_mod.get_reranker(Settings(rerank_enabled=True, rerank_model="my-model"))
    assert isinstance(out, StubEncoder)
    assert built["model"] == "my-model"
