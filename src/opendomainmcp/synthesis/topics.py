from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

_BUSINESS_TYPES = {"feature", "workflow", "permission", "constraint"}
_BUSINESS_AUDIENCE = {"product_manager", "solutions_architect"}


@dataclass
class TopicCandidate:
    name: str
    chunk_ids: list[str] = field(default_factory=list)
    in_code: bool = False
    in_docs: bool = False
    business_hits: int = 0

    @property
    def cross_validated(self) -> bool:
        return self.in_code and self.in_docs

    @property
    def rank_key(self) -> tuple:
        # Strongest first: cross-validated, then business support, then breadth.
        return (self.cross_validated, self.business_hits, len(self.chunk_ids))


def _concepts(meta: dict) -> list[str]:
    return [c.strip() for c in str(meta.get("concepts", "")).split(",") if c.strip()]


def _is_business(meta: dict) -> bool:
    if str(meta.get("knowledge_type", "")).strip().lower() in _BUSINESS_TYPES:
        return True
    aud = {a.strip().lower() for a in str(meta.get("audience", "")).split(",")}
    return bool(aud & _BUSINESS_AUDIENCE)


def gather_topics(items: list[dict], extra_topics: Iterable[str] = ()) -> list[TopicCandidate]:
    """Aggregate candidate topics from stored chunk metadata and apply the
    structural gate. Topic names are normalized to lowercase for dedup; the
    first-seen surface form is not preserved (kept simple, deterministic)."""
    cand: dict[str, TopicCandidate] = {}

    def _ensure(name: str) -> TopicCandidate | None:
        key = name.strip().lower()
        if not key:
            return None
        tc = cand.get(key)
        if tc is None:
            tc = TopicCandidate(name=key)
            cand[key] = tc
        return tc

    for item in items:
        meta = item.get("metadata") or {}
        is_code = str(meta.get("kind", "")).lower() == "code"
        business = _is_business(meta)
        # Dedupe concepts by normalized key within each item to ensure each chunk
        # contributes at most once per distinct (normalized, case-insensitive) concept.
        seen_keys: set[str] = set()
        for name in _concepts(meta):
            key = name.strip().lower()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            tc = _ensure(name)
            if tc is None:
                continue
            tc.chunk_ids.append(item["id"])
            if is_code:
                tc.in_code = True
            else:
                tc.in_docs = True
            if business:
                tc.business_hits += 1

    for name in extra_topics:  # graph entities widen the candidate set only
        _ensure(name)

    gated = [tc for tc in cand.values() if tc.cross_validated or tc.business_hits > 1]
    return sorted(gated, key=lambda t: t.rank_key, reverse=True)
