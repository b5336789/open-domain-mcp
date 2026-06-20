"""OpenAI-compatible embedder backend (e.g. LM Studio serving a local model).

A local model's output dimension is not known from a hardcoded table, so the
embedder must learn it from the first response — mirroring LocalEmbedder.
"""

import pytest

from opendomainmcp.embedding.cloud import OpenAIEmbedder


def _fake_client(dim, calls=None):
    class Embeddings:
        def create(self, model, input):
            if calls is not None:
                calls.append({"model": model, "input": input})
            data = [type("D", (), {"embedding": [0.0] * dim}) for _ in input]
            return type("R", (), {"data": data})

    return type("Client", (), {"embeddings": Embeddings()})()


def test_openai_embedder_learns_dim_from_response():
    calls = []
    emb = OpenAIEmbedder("text-embedding-qwen3-embedding-0.6b",
                         client=_fake_client(1024, calls))
    vectors = emb.embed(["some code"])

    assert vectors == [[0.0] * 1024]
    assert emb.dim == 1024
    assert calls[0]["model"] == "text-embedding-qwen3-embedding-0.6b"


def test_openai_embedder_dim_probes_when_unknown():
    # dim accessed before any embed() call -> it should probe once and learn 1024.
    emb = OpenAIEmbedder("text-embedding-qwen3-embedding-0.6b",
                         client=_fake_client(1024))
    assert emb.dim == 1024


def test_openai_embedder_known_model_dim_without_calling():
    # A known OpenAI model keeps its mapped dim and must not hit the network.
    class Boom:
        class embeddings:
            @staticmethod
            def create(*a, **k):
                raise AssertionError("should not call the API for a known model")

    emb = OpenAIEmbedder("text-embedding-3-small", client=Boom())
    assert emb.dim == 1536
