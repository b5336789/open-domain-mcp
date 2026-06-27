from __future__ import annotations

from collections import Counter

from ..context import Context
from ..metrics import MetricsRecorder

JOB_STATUSES = ("queued", "running", "done", "error", "cancelled")
REVIEW_STATUSES = ("approved", "pending", "rejected", "unset")


def compute_readiness(ctx: Context, tasks: list[dict] | None = None) -> dict:
    stats = ctx.store.stats()
    total = int(stats.get("count") or 0)
    sources = ctx.store.list_sources()
    review = _review_counts(sources, total)
    jobs = _job_counts(tasks or [])

    approved = review["approved"]
    pending = review["pending"]
    approved_ratio = round(approved / total, 4) if total else 0
    blockers: list[str] = []
    warnings: list[str] = []
    active_jobs = bool(jobs["queued"] or jobs["running"])

    if jobs["error"]:
        blockers.append(_count_text(jobs["error"], "background job failed"))
    if total == 0 and not active_jobs:
        blockers.append("No indexed knowledge objects.")
    if pending:
        warnings.append(_count_text(pending, "knowledge object is pending review"))
    if review["rejected"]:
        warnings.append(_count_text(review["rejected"], "knowledge object was rejected"))
    if review["unset"]:
        warnings.append(_count_text(review["unset"], "knowledge object is unreviewed"))
    if total and approved == 0:
        warnings.append("No approved knowledge objects.")

    if blockers:
        status = "blocked"
    elif jobs["queued"] or jobs["running"]:
        status = "validating"
    elif warnings:
        status = "needs_review"
    else:
        status = "ready"

    return {
        "collection": stats.get("collection") or "",
        "status": status,
        "score": 0 if blockers else round(approved_ratio * 100),
        "next_action": _next_action(status, blockers, warnings),
        "blockers": blockers,
        "warnings": warnings,
        "stats": {
            "count": total,
            "embedder": stats.get("embedder"),
            "dim": int(stats.get("dim") or 0),
        },
        "source_health": {
            "sources": len(sources),
            "chunks": total,
            "stale": _sum_source_flag(sources, "stale"),
            "failed": _sum_source_flag(sources, "failed"),
        },
        "review_health": {
            "approved": approved,
            "pending": pending,
            "rejected": review["rejected"],
            "unset": review["unset"],
            "approved_ratio": approved_ratio,
        },
        "article_health": _article_health(ctx),
        "retrieval_health": _retrieval_health(ctx),
        "job_health": {status: jobs[status] for status in JOB_STATUSES},
        "graph_health": _graph_health(ctx),
    }


def _review_counts(sources: list[dict], total: int) -> Counter:
    review: Counter = Counter()
    for source in sources:
        buckets = source.get("review") or {}
        for status in REVIEW_STATUSES:
            review[status] += int(buckets.get(status) or 0)
    counted = sum(review[status] for status in REVIEW_STATUSES)
    if total > counted:
        review["unset"] += total - counted
    return review


def _job_counts(tasks: list[dict]) -> Counter:
    jobs: Counter = Counter({status: 0 for status in JOB_STATUSES})
    for task in tasks:
        status = (task or {}).get("status")
        if status in JOB_STATUSES:
            jobs[status] += 1
    return jobs


def _sum_source_flag(sources: list[dict], key: str) -> int:
    return sum(int(source.get(key) or 0) for source in sources)


def _article_health(ctx: Context) -> dict:
    stats = ctx.store.stats()
    try:
        articles = ctx.store.sibling(f"{stats['collection']}__articles")
        rows = articles.get_items(limit=500, offset=0)
    except Exception:  # noqa: BLE001 - article collection is optional evidence
        return _empty_article_health()

    relevance: list[float] = []
    cross_validated = 0
    for row in rows:
        meta = row.get("metadata") or {}
        relevance.append(_float(meta.get("business_relevance")))
        if _truthy(meta.get("cross_validated")):
            cross_validated += 1
    return {
        "articles": len(rows),
        "cross_validated": cross_validated,
        "avg_relevance": round(sum(relevance) / len(relevance), 4) if relevance else 0,
    }


def _retrieval_health(ctx: Context) -> dict:
    metrics = MetricsRecorder(ctx.settings.data_dir).agent_metrics()
    return {
        "events": int(metrics.get("total_events") or 0),
        "grounding_hit_rate": round(float(metrics.get("grounding_hit_rate") or 0), 4),
        "avg_score": round(float(metrics.get("avg_score") or 0), 4),
        "retrieval_precision": round(float(metrics.get("retrieval_precision") or 0), 4),
    }


def _graph_health(ctx: Context) -> dict:
    try:
        entities = ctx.graph.list_entities(limit=500) or []
        workflows = ctx.graph.list_workflows(limit=500) or []
    except Exception:  # noqa: BLE001 - readiness should degrade if graph is down
        return {"available": False, "entities": 0, "workflows": 0}
    return {
        "available": True,
        "entities": len(entities),
        "workflows": len(workflows),
    }


def _count_text(count: int, singular: str) -> str:
    if count == 1:
        return f"1 {singular}."
    if singular.endswith("is pending review"):
        return f"{count} knowledge objects are pending review."
    if singular.endswith("was rejected"):
        return f"{count} knowledge objects were rejected."
    if singular.endswith("is unreviewed"):
        return f"{count} knowledge objects are unreviewed."
    if singular == "background job failed":
        return f"{count} background jobs failed."
    return f"{count} {singular}s."


def _empty_article_health() -> dict:
    return {
        "articles": 0,
        "cross_validated": 0,
        "avg_relevance": 0,
    }


def _float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _next_action(status: str, blockers: list[str], warnings: list[str]) -> str:
    if any("background job" in blocker and "failed" in blocker for blocker in blockers):
        return "Inspect failed background jobs."
    if "No indexed knowledge objects." in blockers:
        return "Add sources in Source Intake."
    if "blocked" == status:
        return "Inspect failed background jobs."
    if "validating" == status:
        return "Wait for background jobs to finish."
    if "No approved knowledge objects." in warnings:
        return "Review and approve knowledge objects."
    if any("rejected" in warning or "unreviewed" in warning for warning in warnings):
        return "Review rejected or unclassified knowledge objects."
    if "needs_review" == status:
        return "Review pending knowledge objects."
    return "Workspace is ready."
