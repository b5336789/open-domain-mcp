from opendomainmcp.models import Chunk, KnowledgeUnit


def _chunk(text, source="f.txt", **kw):
    return Chunk(text=text, source=source, **kw)


def test_upsert_search_and_idempotency(store):
    chunks = [
        _chunk("python decorators wrap functions", source="a.md"),
        _chunk("database indexes speed up queries", source="b.md"),
        _chunk("react hooks manage component state", source="c.md"),
    ]
    assert store.upsert(chunks) == 3
    assert store.stats()["count"] == 3

    # Re-upserting identical chunks must not grow the collection.
    store.upsert(chunks)
    assert store.stats()["count"] == 3

    results = store.search("how do decorators wrap functions", top_k=1)
    assert results and results[0].metadata["source"] == "a.md"
    assert 0.0 <= results[0].score <= 1.0


def test_get_update_delete(store):
    k = KnowledgeUnit(summary="indexing", concepts=["index"])
    c = _chunk("database indexes speed up queries", source="b.md", knowledge=k)
    store.upsert([c])

    fetched = store.get_item(c.id)
    assert fetched["metadata"]["summary"] == "indexing"

    assert store.update_metadata(c.id, {"source": "b.md", "summary": "edited"})
    assert store.get_item(c.id)["metadata"]["summary"] == "edited"

    assert store.delete_item(c.id) is True
    assert store.get_item(c.id) is None
    assert store.delete_item("missing") is False


def test_get_items_pagination(store):
    store.upsert([_chunk(f"doc number {i}", source=f"{i}.md") for i in range(5)])
    page = store.get_items(limit=2, offset=0)
    assert len(page) == 2
