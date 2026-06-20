"""HTTP routes for the Pre-Execution Advisor and metrics (TASKS #5.3/#5.5/#5.6).

A thin HTTP wrapper over the pure aggregation in
:mod:`opendomainmcp.advisor` and :mod:`opendomainmcp.metrics`. No business
logic lives here:

* ``POST /api/advise`` exposes :func:`opendomainmcp.advisor.advise`.
* ``GET /api/metrics`` assembles product-level metrics from the store/views and
  agent-quality metrics from the recorded :file:`metrics.jsonl`.

It also exports :func:`record_retrieval`, a best-effort helper the main app can
call from its search/ask/simulate handlers to log retrieval events. Recording is
non-fatal by design: a failure there must never break the originating request.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..advisor import advise
from ..context import Context
from ..metrics import MetricsRecorder, count_distinct_sources, product_metrics
from .deps import get_ctx

logger = logging.getLogger(__name__)

router = APIRouter()

# Upper bound on items scanned when counting distinct indexed sources. Large
# enough to cover realistic collections without an unbounded full scan.
MAX_ITEMS_FOR_SOURCE_COUNT = 10_000

# Recordable retrieval event kinds mapped to the recorder methods that log them.
_SEARCH_KIND = "search"
_ASK_KIND = "ask"


class AdviseRequest(BaseModel):
    """Body for ``POST /api/advise``."""

    action: str
    top_k: int = 5


@router.post("/api/advise")
def post_advise(req: AdviseRequest, ctx: Context = Depends(get_ctx)) -> dict:
    """Return the faceted pre-execution advice for ``req.action``.

    Fail loud on an empty/blank action: :func:`advise` raises ``ValueError``,
    which we surface as a 422 so callers learn the input was invalid.
    """
    try:
        return advise(ctx, req.action, req.top_k)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/metrics")
def get_metrics(ctx: Context = Depends(get_ctx)) -> dict:
    """Return product-level and agent-quality metrics.

    Product metrics come from the store (knowledge objects, distinct indexed
    sources) and the MCP view registry (published MCPs). Agent metrics are read
    from the recorded events file; an absent/empty file yields zeroed fields.
    """
    from ..views import VIEWS

    stats = ctx.store.stats()
    items = ctx.store.get_items(limit=MAX_ITEMS_FOR_SOURCE_COUNT, offset=0)
    product = product_metrics(
        knowledge_objects=stats["count"],
        indexed_sources=count_distinct_sources(items),
        published_mcps=len(VIEWS),
    )
    agent = MetricsRecorder(ctx.settings.data_dir).agent_metrics()
    return {"product": product, "agent": agent}


def record_retrieval(ctx: Context, kind: str, query: str, results: list[dict]) -> None:
    """Record a search/ask retrieval event; never raise on failure.

    Builds a :class:`MetricEvent` from ``results`` (hits, per-result scores, and
    non-empty ``metadata["knowledge_type"]`` values) and appends it via a
    :class:`MetricsRecorder`. Any failure is logged and swallowed so the calling
    request always completes.

    Args:
        ctx: runtime ``Context`` exposing ``settings.data_dir``.
        kind: ``"search"`` or ``"ask"``.
        query: the originating query string.
        results: list of ``SearchResult.to_dict()``-style dicts.
    """
    try:
        scores = [score for score in (r.get("score") for r in results) if score is not None]
        knowledge_types = [
            ktype
            for ktype in (
                (r.get("metadata") or {}).get("knowledge_type") for r in results
            )
            if ktype
        ]
        recorder = MetricsRecorder(ctx.settings.data_dir)
        if kind == _ASK_KIND:
            recorder.record_ask(query, len(results), scores, knowledge_types)
        else:
            recorder.record_search(query, len(results), scores, knowledge_types)
    except Exception as exc:  # noqa: BLE001 - recording must never break the request
        logger.warning("metric recording failed (kind=%s): %r", kind, exc)
