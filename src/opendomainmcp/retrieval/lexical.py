"""Lexical (BM25) retrieval and rank fusion.

Complements dense vector search with exact-token matching — important for
symbol names and identifiers that embeddings often blur. The index is built
lazily from the collection's documents and rebuilt when the store marks it
dirty (after upsert/delete).
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


class LexicalIndex:
    def __init__(self):
        self._ids: list[str] = []
        self._bm25 = None

    def build(self, ids: list[str], documents: list[str]) -> None:
        from rank_bm25 import BM25Okapi

        self._ids = list(ids)
        corpus = [tokenize(d) for d in documents]
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, query: str, top_k: int) -> list[str]:
        """Return ids ranked best-first; drops zero-score (no token overlap)."""
        if self._bm25 is None or not self._ids:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        order = sorted(range(len(self._ids)), key=lambda i: scores[i], reverse=True)
        return [self._ids[i] for i in order[:top_k] if scores[i] > 0]


def rrf_fuse(rankings: list[list[str]], top_k: int, k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion. Each input is a best-first list of ids; returns
    ``(id, fused_score)`` ordered best-first."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, _id in enumerate(ranking):
            scores[_id] = scores.get(_id, 0.0) + 1.0 / (k + rank + 1)
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return ordered[:top_k]
