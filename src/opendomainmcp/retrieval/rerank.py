"""Optional cross-encoder re-ranking.

After dense + lexical fusion produces a candidate set, a cross-encoder scores
each (query, document) pair jointly, which is more accurate than independent
embeddings. This yields a single relevance score for every result — including
lexical-only hits that otherwise carried no dense score.

Re-ranking is off by default; the model is downloaded by fastembed on first use.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class CrossEncoderReranker:
    def __init__(self, model: str = "Xenova/ms-marco-MiniLM-L-6-v2"):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._encoder = TextCrossEncoder(model_name=model)
        self.name = model

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Relevance score for each document against the query (higher = better)."""
        return [float(s) for s in self._encoder.rerank(query, documents)]


def get_reranker(settings):
    """Return a reranker when enabled, else ``None`` (no re-ranking)."""
    if not getattr(settings, "rerank_enabled", False):
        return None
    return CrossEncoderReranker(getattr(settings, "rerank_model", "Xenova/ms-marco-MiniLM-L-6-v2"))
