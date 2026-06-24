from opendomainmcp.models import Article


def _article(**kw):
    base = dict(
        title="Order Approval Rule", topic="order approval",
        body="Orders over $10k require manager sign-off [1].",
        business_relevance=0.8, source_chunk_ids=["b", "a"],
        sources=["billing.py:42", "policy.md:5"], cross_validated=True,
        critic_verdict={"grounded": True, "business_meaningful": True, "note": ""},
    )
    base.update(kw)
    return Article(**base)


def test_article_id_depends_only_on_topic():
    # Same topic, completely different member chunks → same id, so re-synthesis
    # under a shifting corpus overwrites one article per topic instead of
    # accumulating a new row each time the retrieval set moves.
    a1 = _article(source_chunk_ids=["a", "b"])
    a2 = _article(source_chunk_ids=["x", "y", "z"])
    assert a1.id == a2.id
    assert _article(topic="other").id != a1.id


def test_id_for_topic_matches_article_id():
    a = _article(topic="billing engine")
    assert Article.id_for_topic("billing engine") == a.id


def test_article_duck_types_chunk_storage_interface():
    a = _article()
    assert a.text == a.body
    et = a.embedding_text()
    assert "Order Approval Rule" in et and "order approval" in et and a.body in et
    meta = a.metadata()
    assert meta["kind"] == "article"
    assert meta["topic"] == "order approval"
    assert meta["business_relevance"] == 0.8
    assert meta["cross_validated"] is True
    assert meta["grounded"] is True
    assert meta["business_meaningful"] is True
    assert meta["sources"] == "billing.py:42 | policy.md:5"
    # No None/empty values leak into Chroma metadata.
    assert all(v is not None and v != "" for v in meta.values())
