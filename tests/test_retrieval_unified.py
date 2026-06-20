from opendomainmcp.config import Settings
from opendomainmcp.models import Article, Chunk
from opendomainmcp.retrieval import search_unified
from opendomainmcp.store import build_where


def _arts(store):
    return store.sibling(f"{store.stats()['collection']}__articles")


def _seed_chunks(store):
    store.upsert([
        Chunk(text="orders over 10k require manager approval", source="rules.md",
              kind="text", start_line=1, end_line=1),
        Chunk(text="def approve(order): ...", source="approve.py", kind="code",
              start_line=1, end_line=2),
    ])


def _seed_article(store):
    _arts(store).upsert([Article(
        title="Order Approval Rule", topic="order approval",
        body="Orders above $10k require manager sign-off [1].",
        source_chunk_ids=["a"], sources=["rules.md:1"])])


def test_fusion_includes_articles_and_chunks(store):
    _seed_chunks(store)
    _seed_article(store)
    results = search_unified(store, "order approval over 10k", top_k=5,
                             mode="hybrid", settings=Settings())
    kinds = {r.metadata.get("kind") for r in results}
    assert "article" in kinds            # the synthesized article competes
    assert kinds & {"code", "text"}      # chunks still present


def test_flag_off_is_identical_to_plain_search(store):
    _seed_chunks(store)
    _seed_article(store)
    s = Settings(retrieve_include_articles=False)
    unified = search_unified(store, "order approval", top_k=5, mode="vector", settings=s)
    plain = store.search("order approval", top_k=5, mode="vector")
    assert [r.id for r in unified] == [r.id for r in plain]
    assert all(r.metadata.get("kind") != "article" for r in unified)


def test_no_articles_is_identical_to_plain_search(store):
    _seed_chunks(store)  # no article seeded → empty sibling
    unified = search_unified(store, "approval", top_k=5, mode="vector", settings=Settings())
    plain = store.search("approval", top_k=5, mode="vector")
    assert [r.id for r in unified] == [r.id for r in plain]


def test_where_filter_forwarded_to_article_search(store):
    """Verify that where filters (e.g., kind=code) reach the article search.

    When kind=code filter is applied, articles (which have kind=article) should be
    excluded even though they match the query text. This proves where is forwarded
    to both chunk and article searches, not silently dropped.
    """
    _seed_chunks(store)
    _seed_article(store)
    # Search with kind=code filter; articles have kind=article so should be excluded
    results = search_unified(store, "order approval", top_k=5, mode="hybrid",
                            settings=Settings(), where=build_where({"kind": "code"}))
    kinds = {r.metadata.get("kind") for r in results}
    # Article should be filtered out
    assert "article" not in kinds
    # But code chunks should still be present
    assert "code" in kinds
