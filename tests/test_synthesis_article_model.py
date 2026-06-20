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


def test_article_id_is_stable_and_order_independent():
    a1 = _article(source_chunk_ids=["a", "b"])
    a2 = _article(source_chunk_ids=["b", "a"])
    assert a1.id == a2.id  # sorted member ids → idempotent regardless of order
    assert _article(topic="other").id != a1.id


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
