"""Unified retrieval: fuse chunk hits with synthesized-article hits.

Used by `ask` and `search`. The low-level store, MCP views, and the advisor are
intentionally NOT routed through here. When articles are disabled or none exist,
this returns exactly the plain chunk search.
"""
from __future__ import annotations

from ..models import SearchResult
from . import rrf_fuse


def _suppress_rule_members(hits: list[SearchResult]) -> list[SearchResult]:
    """When canonical rules are present, drop their constituent chunk members.

    Collects all chunk ids listed in any rule hit's ``member_chunk_ids`` CSV and
    removes those ids from the result list. Rules keep their own ranking; no score
    boosting is applied.
    """
    suppressed: set[str] = set()
    for r in hits:
        if r.metadata.get("kind") == "rule":
            raw = r.metadata.get("member_chunk_ids", "")
            if raw:
                suppressed.update(cid.strip() for cid in raw.split(",") if cid.strip())
    if not suppressed:
        return hits
    return [r for r in hits if r.id not in suppressed]


def search_unified(store, query, *, top_k=5, mode="vector", settings,
                   where=None, source_contains=None) -> list[SearchResult]:
    chunk_hits = store.search(query, top_k=top_k, where=where, mode=mode,
                              source_contains=source_contains)

    # Accumulate result pool and ranked lists for a single rrf_fuse call below.
    pool = {r.id: r for r in chunk_hits}
    ranked_lists = [[h.id for h in chunk_hits]]

    if getattr(settings, "retrieve_include_articles", True) and hasattr(store, "sibling"):
        article_store = store.sibling(f"{store.stats()['collection']}__articles")
        if article_store.stats()["count"] > 0:
            article_hits = article_store.search(query, top_k=top_k, where=where,
                                                mode=mode,
                                                source_contains=source_contains)
            if article_hits:
                pool.update({r.id: r for r in article_hits})
                ranked_lists.append([h.id for h in article_hits])

    if getattr(settings, "retrieve_include_chains", True) and hasattr(store, "sibling"):
        chain_store = store.sibling(f"{store.stats()['collection']}__chains")
        if chain_store.stats()["count"] > 0:
            chain_hits = chain_store.search(query, top_k=top_k, where=where,
                                            mode=mode,
                                            source_contains=source_contains)
            if chain_hits:
                pool.update({r.id: r for r in chain_hits})
                ranked_lists.append([h.id for h in chain_hits])

    prefer_rules = getattr(settings, "retrieve_prefer_rules", True)

    if len(ranked_lists) == 1:
        # No siblings contributed — return plain chunk results (with suppression if on).
        return _suppress_rule_members(chunk_hits) if prefer_rules else chunk_hits

    fused = rrf_fuse(ranked_lists, top_k=top_k)
    results = [pool[_id] for _id, _ in fused if _id in pool]
    return _suppress_rule_members(results) if prefer_rules else results
