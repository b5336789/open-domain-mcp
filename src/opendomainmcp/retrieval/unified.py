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
    if not getattr(settings, "retrieve_include_articles", True):
        return chunk_hits

    article_store = store.sibling(f"{store.stats()['collection']}__articles")
    if article_store.stats()["count"] == 0:
        return chunk_hits

    article_hits = article_store.search(query, top_k=top_k, where=where, mode=mode,
                                        source_contains=source_contains)
    if not article_hits:
        return chunk_hits

    # Merge chunk and article results by id. Both use sha256 content hashes for ids,
    # so id collision is not a real concern; rrf_fuse already keys by id.
    pool = {r.id: r for r in chunk_hits}
    pool.update({r.id: r for r in article_hits})
    fused = rrf_fuse([[h.id for h in chunk_hits], [h.id for h in article_hits]],
                     top_k=top_k)
    return [pool[_id] for _id, _ in fused if _id in pool]
