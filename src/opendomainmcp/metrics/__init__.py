"""Retrieval/QA metrics collection.

A small, dependency-free recorder that appends search/ask events to an
append-only JSONL file under the data directory (one JSON object per line) and
can read them back into simple aggregates. An in-memory mode (``data_dir=None``)
keeps everything in a list so tests run without touching the filesystem.

This module is intentionally standalone: it is not wired into the API or the
pipeline yet (see later tasks). It only depends on the standard library.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class MetricEvent:
    """A single recorded retrieval/QA event.

    ``kind`` is ``"search"`` or ``"ask"``. ``scores`` are the per-hit relevance
    scores; ``knowledge_types`` are the knowledge types of the returned hits
    (flattened across results). ``ts`` is a Unix timestamp.
    """

    kind: str
    query: str
    hits: int
    scores: list[float] = field(default_factory=list)
    knowledge_types: list[str] = field(default_factory=list)
    ts: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "MetricEvent":
        return cls(
            kind=data.get("kind", ""),
            query=data.get("query", ""),
            hits=data.get("hits", 0),
            scores=list(data.get("scores", [])),
            knowledge_types=list(data.get("knowledge_types", [])),
            ts=data.get("ts", 0.0),
        )


class MetricsRecorder:
    """Records retrieval/QA events to a JSONL file (or memory for tests).

    When ``data_dir`` is given, events are appended to ``metrics.jsonl`` under
    it. When ``data_dir`` is ``None``, events are kept in memory only. Both
    modes also retain a live in-memory copy so aggregates are cheap to compute.
    """

    FILENAME = "metrics.jsonl"

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self._events: list[MetricEvent] = []

    @property
    def path(self) -> Optional[Path]:
        if self.data_dir is None:
            return None
        return self.data_dir / self.FILENAME

    def record_search(
        self,
        query: str,
        hits: int,
        scores: Optional[list[float]] = None,
        knowledge_types: Optional[list[str]] = None,
    ) -> MetricEvent:
        """Record a search/retrieval event."""
        return self._record("search", query, hits, scores, knowledge_types)

    def record_ask(
        self,
        query: str,
        hits: int,
        scores: Optional[list[float]] = None,
        knowledge_types: Optional[list[str]] = None,
    ) -> MetricEvent:
        """Record an ask/QA event (hits = supporting passages used)."""
        return self._record("ask", query, hits, scores, knowledge_types)

    def _record(
        self,
        kind: str,
        query: str,
        hits: int,
        scores: Optional[list[float]],
        knowledge_types: Optional[list[str]],
    ) -> MetricEvent:
        event = MetricEvent(
            kind=kind,
            query=query,
            hits=hits,
            scores=list(scores or []),
            knowledge_types=list(knowledge_types or []),
            ts=time.time(),
        )
        self._events.append(event)
        if self.path is not None:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(event.to_json() + "\n")
        return event

    def read_events(self) -> list[MetricEvent]:
        """Return all events. Reads from disk when persisting, else memory.

        Fail loud: a corrupt JSONL line raises rather than being silently
        skipped, so data problems surface immediately.
        """
        if self.path is None:
            return list(self._events)
        if not self.path.exists():
            return []
        events: list[MetricEvent] = []
        for line_no, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(MetricEvent.from_dict(json.loads(line)))
            except (json.JSONDecodeError, AttributeError) as exc:
                raise ValueError(
                    f"Corrupt metrics line {line_no} in {self.path}: {exc}"
                ) from exc
        return events

    def aggregate(self) -> dict:
        """Summarize recorded events into counts and averages.

        Returns total events, a per-kind event count, the average number of
        hits per event, the average score across all hits, and per-knowledge-
        type hit counts.
        """
        events = self.read_events()
        total = len(events)
        by_kind: dict[str, int] = {}
        per_type: dict[str, int] = {}
        total_hits = 0
        all_scores: list[float] = []
        for event in events:
            by_kind[event.kind] = by_kind.get(event.kind, 0) + 1
            total_hits += event.hits
            all_scores.extend(event.scores)
            for kt in event.knowledge_types:
                per_type[kt] = per_type.get(kt, 0) + 1
        return {
            "total_events": total,
            "by_kind": by_kind,
            "avg_hits": (total_hits / total) if total else 0.0,
            "avg_score": (sum(all_scores) / len(all_scores)) if all_scores else 0.0,
            "per_type_hits": per_type,
        }
