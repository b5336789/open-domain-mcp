"""ChainItem storage + unified retrieval fusion + citations (plan 4B)."""

from opendomainmcp.config import Settings
from opendomainmcp.models import ChainItem


def _item():
    return ChainItem(
        entry="api.Ctl.post", title="Charge flow",
        body="Validates and persists a charge.",
        rules=["amount must not be negative"],
        members=["api.Ctl.post", "svc.A.a"],
        sources=["Ctl.java:10-30", "A.java:5-40"],
        member_chunk_ids=["c1", "c2"],
    )


def test_chain_item_id_stable_and_metadata_flat():
    item = _item()
    assert item.id == ChainItem.id_for_entry("api.Ctl.post")
    meta = item.metadata()
    assert meta["kind"] == "chain" and meta["title"] == "Charge flow"
    assert "api.Ctl.post" in meta["members"]
    assert all(not isinstance(v, (list, dict)) for v in meta.values())
    assert "amount must not be negative" in item.text
    assert "Charge flow" in item.embedding_text()


def test_chain_items_fuse_into_unified_search(store):
    from opendomainmcp.models import Chunk
    from opendomainmcp.retrieval import search_unified

    store.upsert([Chunk(text="def charge(): pass", source="a.py", kind="code")])
    chains = store.sibling(f"{store.stats()['collection']}__chains")
    chains.upsert([_item()])

    hits = search_unified(store, "charge flow rules", top_k=5,
                          settings=Settings(retrieve_include_chains=True))
    kinds = {h.metadata.get("kind") for h in hits}
    assert "chain" in kinds

    hits_off = search_unified(store, "charge flow rules", top_k=5,
                              settings=Settings(retrieve_include_chains=False))
    assert "chain" not in {h.metadata.get("kind") for h in hits_off}


def test_chain_citations_and_source_label():
    from opendomainmcp.models import SearchResult
    from opendomainmcp.query.rag import _citations, _source_label

    r = SearchResult(id=_item().id, text=_item().text, score=0.9,
                     metadata=_item().metadata())
    assert _source_label(r) == "Charge flow"
    cite = _citations([r])[0]
    assert cite["type"] == "chain" and cite["source"] == "Charge flow"


def test_where_filter_forwarded_to_chain_search(store):
    """A where filter must reach the chain sibling search, not be dropped.

    kind=code excludes chain items (kind=chain); if the where clause were
    silently dropped for the chains sibling, the item would leak into results.
    """
    from opendomainmcp.models import Chunk
    from opendomainmcp.retrieval import search_unified
    from opendomainmcp.store import build_where

    store.upsert([Chunk(text="def charge(): pass", source="a.py", kind="code")])
    chains = store.sibling(f"{store.stats()['collection']}__chains")
    chains.upsert([_item()])

    hits = search_unified(store, "charge flow rules", top_k=5,
                          settings=Settings(retrieve_include_chains=True),
                          where=build_where({"kind": "code"}))
    kinds = {h.metadata.get("kind") for h in hits}
    assert "chain" not in kinds
    assert "code" in kinds


def test_retrieve_include_chains_is_editable():
    from opendomainmcp.config import EDITABLE_FIELDS

    assert "retrieve_include_chains" in EDITABLE_FIELDS
