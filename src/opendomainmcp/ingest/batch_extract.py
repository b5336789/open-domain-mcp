"""Whole-corpus extraction via the Anthropic Message Batches API (50% cheaper).

``BatchExtractor`` submits one batch for all chunk texts, polls to completion,
and parses results into ``KnowledgeUnit``s, reusing the same ``_SYSTEM`` prompt
and ``_parse`` as the synchronous ``ClaudeExtractor`` so output is identical.
``CachedExtractor`` lets the pipeline run its unchanged per-file loop against the
pre-computed results, falling back to a live call on a miss (Fail Loud).
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

from ..models import KnowledgeUnit

logger = logging.getLogger(__name__)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class BatchItem:
    text_hash: str
    text: str
    kind: str
    language: str | None = None


class CachedExtractor:
    """Extractor that serves pre-computed results; falls back to a live call."""

    def __init__(self, cache: dict[str, KnowledgeUnit], fallback):
        self._cache = cache
        self._fallback = fallback

    def extract(self, text: str, kind: str, language=None) -> KnowledgeUnit:
        hit = self._cache.get(_text_hash(text))
        if hit is not None:
            return hit
        return self._fallback.extract(text, kind, language)
