"""Offline grounding / hallucination-reduction eval harness.

Given an eval set and a ``retrieve`` and/or ``ask`` callable, compute two
deterministic grounding metrics per case:

* **retrieval hit** -- did at least one expected source substring appear among
  the identifiers of the retrieved sources?
* **answer grounding** -- did the answer contain *all* of the expected answer
  substrings? (An ungrounded / hallucinating answer omits or contradicts them.)

The harness only depends on the *shape* of the system under test, matching
:func:`opendomainmcp.query.rag.answer_question` (which returns
``{"answer": str, "citations": [{"source": ..., "symbol": ...}, ...]}``) and
:class:`opendomainmcp.models.SearchResult`. No model or network is used; the
callables are injected, so a "good" grounded system and a "hallucinating" one
can be compared directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


def _source_identifier(item) -> str:
    """Best-effort source identifier for one retrieved item.

    Accepts a plain string, a citation dict (``{"source", "symbol"}`` as emitted
    by ``rag._citations``), a ``SearchResult``-like object (``.metadata`` dict),
    or a metadata dict. Mirrors ``rag._format_sources``' ``path::symbol`` form so
    expectations can target either the path or the symbol.
    """
    if isinstance(item, str):
        return item
    # SearchResult-like: pull from its metadata mapping.
    meta = getattr(item, "metadata", None)
    if isinstance(meta, dict):
        item = meta
    if isinstance(item, dict):
        source = item.get("source") or "?"
        symbol = item.get("symbol")
        return f"{source}::{symbol}" if symbol else str(source)
    raise TypeError(f"cannot derive a source identifier from {item!r}")


def _retrieved_sources(result) -> list[str]:
    """Normalise a retrieve/ask result into a list of source identifiers.

    ``result`` may be the raw list returned by a retrieval callable, or the
    ``answer_question`` dict carrying ``citations``.
    """
    if isinstance(result, dict):
        result = result.get("citations", [])
    return [_source_identifier(item) for item in result]


@dataclass
class CaseResult:
    id: str
    retrieval_hit: Optional[bool] = None
    answer_grounded: Optional[bool] = None
    retrieved_sources: list[str] = field(default_factory=list)
    answer: Optional[str] = None
    missing_sources: list[str] = field(default_factory=list)
    missing_answer: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    cases: list[CaseResult] = field(default_factory=list)
    retrieval_hit_rate: Optional[float] = None
    answer_grounding_rate: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "retrieval_hit_rate": self.retrieval_hit_rate,
            "answer_grounding_rate": self.answer_grounding_rate,
            "cases": [vars(c) for c in self.cases],
        }


def _rate(values: list[bool]) -> Optional[float]:
    """Fraction of ``True`` over scored cases, or ``None`` if nothing scored."""
    if not values:
        return None
    return sum(1 for v in values if v) / len(values)


def run_evals(
    cases,
    retrieve: Optional[Callable] = None,
    ask: Optional[Callable] = None,
) -> EvalReport:
    """Run ``cases`` against the injected ``retrieve`` and/or ``ask`` callables.

    * ``retrieve(query)`` returns retrieved sources (list of strings / dicts /
      ``SearchResult``); used for the retrieval hit metric. If absent, ``ask``'s
      citations are used as a fallback for retrieval scoring.
    * ``ask(query)`` returns an ``answer_question``-shaped dict; used for the
      answer-grounding metric.

    At least one callable is required (fail loud otherwise).
    """
    if retrieve is None and ask is None:
        raise ValueError("run_evals requires a 'retrieve' and/or 'ask' callable")

    case_results: list[CaseResult] = []
    retrieval_flags: list[bool] = []
    answer_flags: list[bool] = []

    for case in cases:
        cr = CaseResult(id=case.id)

        ask_result = ask(case.query) if ask is not None else None

        # Retrieval scoring: prefer the dedicated retriever, else reuse the
        # answer's citations so an ask-only harness still measures grounding.
        if retrieve is not None:
            sources = _retrieved_sources(retrieve(case.query))
        elif ask_result is not None:
            sources = _retrieved_sources(ask_result)
        else:
            sources = None

        if sources is not None and case.expected_sources:
            cr.retrieved_sources = sources
            joined = "\n".join(sources)
            cr.missing_sources = [s for s in case.expected_sources if s not in joined]
            cr.retrieval_hit = len(cr.missing_sources) < len(case.expected_sources)
            retrieval_flags.append(cr.retrieval_hit)

        if ask_result is not None and case.expected_answer:
            answer = ask_result["answer"] if isinstance(ask_result, dict) else str(ask_result)
            cr.answer = answer
            cr.missing_answer = [s for s in case.expected_answer if s not in answer]
            cr.answer_grounded = not cr.missing_answer
            answer_flags.append(cr.answer_grounded)

        case_results.append(cr)

    return EvalReport(
        cases=case_results,
        retrieval_hit_rate=_rate(retrieval_flags),
        answer_grounding_rate=_rate(answer_flags),
    )
