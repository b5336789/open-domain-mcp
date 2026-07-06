"""Unified retrieval: fuse chunk hits with synthesized-article hits.

Used by `ask` and `search`. The low-level store, MCP views, and the advisor are
intentionally NOT routed through here. When articles are disabled or none exist,
this returns exactly the plain chunk search.
"""
from __future__ import annotations

from ..models import SearchResult
from . import rrf_fuse


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

    if len(ranked_lists) == 1:
        # No siblings contributed — return plain chunk results unchanged.
        return chunk_hits

    fused = rrf_fuse(ranked_lists, top_k=top_k)
    return [pool[_id] for _id, _ in fused if _id in pool]
