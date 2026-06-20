"""Retrieval/QA metrics collection.

A small, dependency-free recorder that appends search/ask events to an
append-only JSONL file under the data directory (one JSON object per line) and
can read them back into simple aggregates. An in-memory mode (``data_dir=None``)
keeps everything in a list so tests run without touching the filesystem.

This module is intentionally standalone (standard library only). The recorder
and ``record_retrieval`` are wired into the web API's ``/api/search``,
``/api/ask`` and ``/api/simulate`` handlers, and the aggregates are served by
``/api/metrics`` (see ``api/insight_routes.py``).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# A hit counts as "relevant" for the retrieval-precision proxy when its score is
# strictly greater than this threshold. The recorder is score-source agnostic
# (scores may be cosine similarities, reranker scores, etc.), so we pick a
# conservative default of 0.0: any positive relevance signal counts. Callers
# that want a stricter bar can compute precision themselves; this proxy is meant
# to be simple and explainable, not a tuned IR metric.
RELEVANCE_THRESHOLD = 0.0


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

    def agent_metrics(self) -> dict:
        """Agent-quality metrics derived from recorded search/ask events.

        Thin wrapper around :func:`agent_metrics` over this recorder's events.
        """
        return agent_metrics(self.read_events())


def agent_metrics(events: list[MetricEvent]) -> dict:
    """Compute agent-quality metrics from a list of recorded events.

    Returns:
        - ``total_events``: number of events considered.
        - ``grounding_hit_rate``: fraction of events that returned at least one
          hit (``hits > 0``). A high rate means the agent rarely answers with no
          retrieved grounding. ``0.0`` when there are no events.
        - ``avg_hits``: mean number of hits per event.
        - ``avg_score``: mean relevance score across all hits.
        - ``retrieval_precision``: a proxy for precision, computed as the mean
          over events of ``(scores above RELEVANCE_THRESHOLD) / max(hits, 1)``.
          Using ``max(hits, 1)`` avoids division by zero for zero-hit events
          (which then contribute 0.0). ``0.0`` when there are no events.
    """
    total = len(events)
    if total == 0:
        return {
            "total_events": 0,
            "grounding_hit_rate": 0.0,
            "avg_hits": 0.0,
            "avg_score": 0.0,
            "retrieval_precision": 0.0,
        }

    grounded = sum(1 for event in events if event.hits > 0)
    total_hits = sum(event.hits for event in events)
    all_scores = [score for event in events for score in event.scores]

    per_event_precision = [
        sum(1 for score in event.scores if score > RELEVANCE_THRESHOLD)
        / max(event.hits, 1)
        for event in events
    ]

    return {
        "total_events": total,
        "grounding_hit_rate": grounded / total,
        "avg_hits": total_hits / total,
        "avg_score": (sum(all_scores) / len(all_scores)) if all_scores else 0.0,
        "retrieval_precision": sum(per_event_precision) / total,
    }


def count_distinct_sources(items: list[dict]) -> int:
    """Count distinct ``metadata["source"]`` values across item dicts.

    Items missing a ``metadata`` mapping, missing the ``source`` key, or with an
    empty/whitespace-only source are ignored. Useful for computing the "Indexed
    Sources" product metric from ``store.get_items(...)``.
    """
    sources: set[str] = set()
    for item in items:
        metadata = item.get("metadata") or {}
        source = metadata.get("source")
        if isinstance(source, str) and source.strip():
            sources.add(source)
    return len(sources)


def product_metrics(
    *,
    knowledge_objects: int,
    indexed_sources: int,
    published_mcps: int,
) -> dict:
    """Assemble product-level metrics into a clean, explicit dict.

    Inputs are computed by the caller (e.g. the API): knowledge objects from
    ``store.stats()["count"]``, indexed sources from
    :func:`count_distinct_sources`, and published MCPs from the number of MCP
    views (e.g. ``len(VIEWS)``).
    """
    return {
        "published_mcps": published_mcps,
        "knowledge_objects": knowledge_objects,
        "indexed_sources": indexed_sources,
    }
