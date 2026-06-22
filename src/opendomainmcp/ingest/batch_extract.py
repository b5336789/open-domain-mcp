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


class BatchExtractor:
    def __init__(self, client, model: str, max_tokens: int = 900,
                 poll_interval: float = 10.0):
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._poll_interval = poll_interval

    def _request(self, item: BatchItem) -> dict:
        from ..extract.knowledge import _SYSTEM

        label = item.kind + (f" ({item.language})" if item.language else "")
        return {
            "custom_id": item.text_hash,
            "params": {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "system": _SYSTEM,
                "messages": [{
                    "role": "user",
                    "content": f"Snippet type: {label}\n\n{item.text}",
                }],
            },
        }

    def extract_many(self, items: list[BatchItem], progress=None) -> dict:
        from ..extract.knowledge import _parse

        if not items:
            return {}
        batches = self._client.messages.batches
        batch = batches.create(requests=[self._request(i) for i in items])
        while True:
            status = batches.retrieve(batch.id)
            if progress is not None:
                c = getattr(status, "request_counts", None)
                if c is not None:
                    progress(f"{c.succeeded} done, {c.processing} processing, "
                             f"{c.errored} errored")
            if status.processing_status == "ended":
                break
            time.sleep(self._poll_interval)

        out: dict[str, KnowledgeUnit] = {}
        for res in batches.results(batch.id):
            if res.result.type != "succeeded":
                logger.warning("batch extraction failed for %s: %s",
                               res.custom_id, res.result.type)
                continue
            raw = "".join(b.text for b in res.result.message.content
                          if b.type == "text")
            try:
                out[res.custom_id] = _parse(raw)
            except Exception as exc:  # malformed output: omit -> live fallback
                logger.warning("batch parse failed for %s: %r", res.custom_id, exc)
        return out


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
