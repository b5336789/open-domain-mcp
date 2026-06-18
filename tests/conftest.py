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
