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


def test_prefer_rules_suppresses_member_chunks(store):
    from opendomainmcp.config import Settings
    from opendomainmcp.models import Chunk, RuleItem
    from opendomainmcp.retrieval import search_unified

    member = Chunk(text="if (amt < 0) throw new Error('negative amount')",
                   source="Billing.java", kind="code", language="java")
    store.upsert([member])
    rule = RuleItem(statement="amount must not be negative",
                    member_chunk_ids=[member.id],
                    sources=["Billing.java:1-1"])
    store.upsert([rule])

    hits = search_unified(store, "negative amount rule", top_k=5,
                          settings=Settings(retrieve_prefer_rules=True,
                                            retrieve_include_articles=False,
                                            retrieve_include_chains=False))
    kinds = [h.metadata.get("kind") for h in hits]
    assert "rule" in kinds
    assert member.id not in [h.id for h in hits]

    hits_off = search_unified(store, "negative amount rule", top_k=5,
                              settings=Settings(retrieve_prefer_rules=False,
                                                retrieve_include_articles=False,
                                                retrieve_include_chains=False))
    assert member.id in [h.id for h in hits_off]


def test_chain_chunk_ids_not_suppressed_in_retrieval(store):
    """A chunk whose id appears only in chain_chunk_ids (not member_chunk_ids)
    must NOT be suppressed when retrieve_prefer_rules=True.

    This is Fix 2: the retrieval suppressor reads RuleItem.member_chunk_ids which
    must contain only chunk-origin ids; chain-origin ids live in chain_chunk_ids.
    """
    from opendomainmcp.config import Settings
    from opendomainmcp.models import Chunk, RuleItem
    from opendomainmcp.retrieval import search_unified

    # A chunk that is a CHAIN member but not a direct chunk member of the rule.
    chain_member = Chunk(text="negative amount rule chain member",
                         source="Chain.java", kind="code", language="java")
    # A direct chunk member of the rule.
    chunk_member = Chunk(text="negative amount rule direct chunk member",
                         source="Billing.java", kind="code", language="java")
    store.upsert([chunk_member, chain_member])

    rule = RuleItem(
        statement="negative amount rule",
        member_chunk_ids=[chunk_member.id],     # suppression set: chunk-origin only
        chain_chunk_ids=[chain_member.id],      # chain-origin: must NOT be suppressed
        sources=["Billing.java:1-1"],
    )
    store.upsert([rule])

    hits = search_unified(store, "negative amount rule", top_k=5,
                          settings=Settings(retrieve_prefer_rules=True,
                                            retrieve_include_articles=False,
                                            retrieve_include_chains=False))
    hit_ids = [h.id for h in hits]
    # Direct chunk member is suppressed (it's in member_chunk_ids).
    assert chunk_member.id not in hit_ids
    # Chain member is NOT suppressed (it's only in chain_chunk_ids).
    assert chain_member.id in hit_ids


def test_prefer_rules_suppression_does_not_underfill_top_k(store):
    """Suppression must not shrink the result list below top_k when enough other
    candidates match: search_unified over-fetches, suppresses, then slices."""
    from opendomainmcp.config import Settings
    from opendomainmcp.models import Chunk, RuleItem
    from opendomainmcp.retrieval import search_unified

    # Token geometry (FakeEmbedder bag-of-words): member matches the query
    # exactly (rank 1), the rule ranks 2nd, the six filler chunks trail. Without
    # over-fetching, the top-3 fetch is {member, rule, filler} and suppression
    # under-fills the result to 2.
    member = Chunk(text="negative amount rule negative amount rule",
                   source="Billing.java", kind="code", language="java")
    others = [
        Chunk(text=f"note about negative amount rule number {i} plus unrelated filler words",
              source=f"notes{i}.md", kind="text", start_line=1, end_line=1)
        for i in range(6)
    ]
    store.upsert([member] + others)
    rule = RuleItem(statement="negative amount rule",
                    member_chunk_ids=[member.id],
                    sources=["Billing.java:1-1"])
    store.upsert([rule])

    hits = search_unified(store, "negative amount rule", top_k=3,
                          settings=Settings(retrieve_prefer_rules=True,
                                            retrieve_include_articles=False,
                                            retrieve_include_chains=False))
    assert len(hits) == 3                       # not under-filled by suppression
    assert member.id not in [h.id for h in hits]
