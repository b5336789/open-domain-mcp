"""Risk-ordered review queue scoring.

Lower score = higher risk = sorts first: conflicted rules (#5), then
unverified evidence (#2), then low-confidence extractions. Deterministic and
computed at query time from metadata already on the item — no new storage."""

from __future__ import annotations

_LOW_CONFIDENCE = 0.5


def priority_score(meta: dict) -> int:
    if meta.get("trust") == "conflicted":
        return 0
    if meta.get("evidence_status") == "unverified":
        return 1
    try:
        if float(meta.get("confidence", 1.0)) < _LOW_CONFIDENCE:
            return 2
    except (TypeError, ValueError):
        pass
    return 3


def order_by_priority(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda it: priority_score(it.get("metadata", {})))
