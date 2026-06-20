from opendomainmcp.models import Article


def test_sibling_shares_client_and_isolates_collection(store):
    sib_name = f"{store.stats()['collection']}__articles"
    articles = store.sibling(sib_name)
    assert articles.stats()["collection"] == sib_name
    art = Article(title="T", topic="billing", body="Orders over $10k need sign-off",
                  source_chunk_ids=["a"], sources=["x.py:1"])
    assert articles.upsert([art]) == 1
    assert store.stats()["count"] == 0          # base collection untouched
    assert articles.stats()["count"] == 1
    hits = articles.search("sign-off orders", top_k=3, mode="vector")
    assert hits and hits[0].metadata["kind"] == "article"
