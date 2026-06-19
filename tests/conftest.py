"""Shared offline test fixtures.

FakeEmbedder produces deterministic vectors via a hashing bag-of-words, so token
overlap drives cosine similarity. This lets retrieval tests run with no network
and no model download.
"""

import hashlib
import math

import pytest

from opendomainmcp.embedding.base import Embedder

_DIM = 64


def _stable_hash(token: str) -> int:
    """Process-independent hash. Builtin hash() is salted per process
    (PYTHONHASHSEED), which made retrieval order — and tests that assert on
    it — flaky across runs."""
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")


class FakeEmbedder(Embedder):
    name = "fake"

    def embed(self, texts):
        vectors = []
        for text in texts:
            vec = [0.0] * _DIM
            for token in text.lower().split():
                vec[_stable_hash(token) % _DIM] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])
        return vectors

    @property
    def dim(self):
        return _DIM


@pytest.fixture
def fake_embedder():
    return FakeEmbedder()


@pytest.fixture
def store(fake_embedder):
    import uuid

    import chromadb

    from opendomainmcp.store import ChromaStore

    # EphemeralClient is process-shared, so isolate each test in its own collection.
    client = chromadb.EphemeralClient()
    return ChromaStore(
        fake_embedder, data_dir=None,
        collection_name=f"test_{uuid.uuid4().hex}", client=client,
    )


class FakeExtractor:
    """Deterministic offline stand-in for ClaudeExtractor."""

    def __init__(self):
        self.calls = 0

    def extract(self, text, kind, language=None):
        from opendomainmcp.models import KnowledgeUnit

        self.calls += 1
        first_word = text.strip().split()[0] if text.strip() else ""
        # Deterministic classification so view/filter tests have stable fixtures.
        knowledge_type = "Code" if kind == "code" else "Feature"
        audience = ["engineering"] if kind == "code" else ["product_manager"]
        return KnowledgeUnit(
            summary=f"about {first_word}",
            concepts=[kind],
            knowledge_type=knowledge_type,
            audience=audience,
            confidence=1.0,
            version="1.0.0",
            entities=[{"name": first_word or kind, "type": "Concept"}],
            typed_relations=[],
        )


@pytest.fixture
def fake_extractor():
    return FakeExtractor()


@pytest.fixture
def pipeline(store, fake_extractor):
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline

    settings = Settings(chunk_size=200, chunk_overlap=20)
    return Pipeline(store, fake_extractor, settings)


class FakeGraphStore:
    """In-memory GraphStoreProtocol implementation for offline tests."""

    def __init__(self):
        self.entities = {}                 # normalized_name -> dict
        self.entity_chunks = {}            # normalized_name -> set(chunk_id)
        self.edges = []                    # list of Edge

    def ensure_schema(self):
        pass

    def upsert_entities(self, entities):
        for e in entities:
            cur = self.entities.get(e.normalized_name)
            conf = max(e.confidence, cur["confidence"]) if cur else e.confidence
            self.entities[e.normalized_name] = {
                "name": e.display_name, "normalized_name": e.normalized_name,
                "type": e.type, "confidence": conf}
            self.entity_chunks.setdefault(e.normalized_name, set()).add(e.chunk_id)

    def upsert_edges(self, edges):
        self.edges.extend(edges)

    def delete_for_chunks(self, chunk_ids):
        ids = set(chunk_ids)
        self.edges = [e for e in self.edges if e.chunk_id not in ids]
        for norm in list(self.entity_chunks):
            self.entity_chunks[norm] -= ids
            if not self.entity_chunks[norm]:
                del self.entity_chunks[norm]
                self.entities.pop(norm, None)

    def get_entity(self, name):
        from opendomainmcp.graph.normalize import normalize_name
        norm = normalize_name(name)
        row = self.entities.get(norm)
        if row is None:
            return None
        return {**row, "aliases": [], "chunk_ids": sorted(self.entity_chunks.get(norm, set()))}

    def neighbors(self, name, relation_type=None, depth=1):
        from opendomainmcp.graph.normalize import normalize_name
        depth = max(1, min(2, depth))
        root = self.get_entity(name)
        if root is None:
            return {"entity": None, "neighbors": []}
        seen = {root["normalized_name"]}
        frontier = [root["normalized_name"]]
        collected = []
        for _ in range(depth):
            nxt = []
            for norm in frontier:
                for e in self.edges:
                    if relation_type and e.relation_type != relation_type:
                        continue
                    if e.src == norm:
                        other, direction = e.dst, "out"
                    elif e.dst == norm:
                        other, direction = e.src, "in"
                    else:
                        continue
                    if other in seen:
                        continue
                    seen.add(other)
                    nxt.append(other)
                    ent = self.get_entity(other)
                    if ent:
                        collected.append({"entity": ent, "relation_type": e.relation_type,
                                          "direction": direction})
            frontier = nxt
        return {"entity": root, "neighbors": collected}


@pytest.fixture
def fake_graph():
    return FakeGraphStore()
