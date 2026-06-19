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
def pipeline(store, fake_extractor, fake_graph):
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline

    settings = Settings(chunk_size=200, chunk_overlap=20)
    return Pipeline(store, fake_extractor, settings, graph=fake_graph)


class FakeGraphStore:
    """In-memory GraphStoreProtocol implementation for offline tests.

    ``backing`` is an optional shared dict so two instances with different
    ``collection`` values can prove filter-based isolation offline.
    """

    def __init__(self, collection: str = "domain_knowledge", backing=None):
        self._collection = collection
        # backing: {collection: {"entities": {}, "entity_chunks": {}, "edges": []}}
        self._backing = backing if backing is not None else {}

    def _slot(self):
        """Return (or lazily create) the per-collection data slot."""
        if self._collection not in self._backing:
            self._backing[self._collection] = {
                "entities": {},
                "entity_chunks": {},
                "edges": [],
            }
        return self._backing[self._collection]

    def ensure_schema(self):
        pass

    def upsert_entities(self, entities):
        slot = self._slot()
        for e in entities:
            cur = slot["entities"].get(e.normalized_name)
            conf = max(e.confidence, cur["confidence"]) if cur else e.confidence
            slot["entities"][e.normalized_name] = {
                "name": e.display_name, "normalized_name": e.normalized_name,
                "type": e.type, "confidence": conf}
            slot["entity_chunks"].setdefault(e.normalized_name, set()).add(e.chunk_id)

    def upsert_edges(self, edges):
        slot = self._slot()
        # Dedupe by (src, dst, relation_type, chunk_id), keeping max confidence —
        # mirrors MariaDB's ON DUPLICATE KEY UPDATE confidence=GREATEST(...).
        index = {(e.src, e.dst, e.relation_type, e.chunk_id): e for e in slot["edges"]}
        for e in edges:
            key = (e.src, e.dst, e.relation_type, e.chunk_id)
            existing = index.get(key)
            if existing is None or e.confidence > existing.confidence:
                index[key] = e
        slot["edges"] = list(index.values())

    def delete_for_chunks(self, chunk_ids):
        slot = self._slot()
        ids = set(chunk_ids)
        slot["edges"] = [e for e in slot["edges"] if e.chunk_id not in ids]
        for norm in list(slot["entity_chunks"]):
            slot["entity_chunks"][norm] -= ids
            if not slot["entity_chunks"][norm]:
                del slot["entity_chunks"][norm]
                slot["entities"].pop(norm, None)

    def delete_collection(self, name: str):
        """Remove all data for the named collection slice."""
        self._backing.pop(name, None)

    def get_entity(self, name):
        from opendomainmcp.graph.normalize import normalize_name
        norm = normalize_name(name)
        slot = self._slot()
        row = slot["entities"].get(norm)
        if row is None:
            return None
        return {**row, "aliases": [],
                "chunk_ids": sorted(slot["entity_chunks"].get(norm, set()))}

    def neighbors(self, name, relation_type=None, depth=1):
        from opendomainmcp.graph.normalize import normalize_name
        depth = max(1, min(2, depth))
        root = self.get_entity(name)
        if root is None:
            return {"entity": None, "neighbors": []}
        slot = self._slot()
        seen = {root["normalized_name"]}
        frontier = [root["normalized_name"]]
        collected = []
        for _ in range(depth):
            nxt = []
            for norm in frontier:
                for e in slot["edges"]:
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

    def list_entities(self, type=None, q=None, limit=50):
        slot = self._slot()
        rows = []
        for norm, row in sorted(slot["entities"].items()):
            if type and row["type"] != type:
                continue
            if q and q.lower().strip() not in norm:
                continue
            rows.append({"name": row["name"], "normalized_name": norm, "type": row["type"]})
        return rows[:max(1, min(500, limit))]


@pytest.fixture
def fake_graph():
    return FakeGraphStore()
