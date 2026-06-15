import uuid

import chromadb

from opendomainmcp.models import Chunk
from opendomainmcp.store import ChromaStore


def _store(client, name, emb):
    return ChromaStore(emb, data_dir=None, collection_name=name, client=client)


def test_collections_are_isolated(fake_embedder):
    client = chromadb.EphemeralClient()
    a = _store(client, f"a_{uuid.uuid4().hex}", fake_embedder)
    b = _store(client, f"b_{uuid.uuid4().hex}", fake_embedder)
    a.upsert([Chunk(text="alpha document about cats", source="a.md", kind="text")])
    b.upsert([Chunk(text="beta document about dogs", source="b.md", kind="text")])

    assert a.stats()["count"] == 1
    assert b.stats()["count"] == 1
    # A's search never returns B's content.
    assert all("b.md" not in r.metadata["source"] for r in a.search("dogs", top_k=5))

    names = {c["name"] for c in a.list_collections()}
    assert a._collection_name in names and b._collection_name in names


def test_create_and_drop_collection(fake_embedder):
    client = chromadb.EphemeralClient()
    s = _store(client, f"base_{uuid.uuid4().hex}", fake_embedder)
    name = f"proj_{uuid.uuid4().hex}"
    s.create_collection(name)
    assert name in {c["name"] for c in s.list_collections()}
    s.drop_collection(name)
    assert name not in {c["name"] for c in s.list_collections()}
