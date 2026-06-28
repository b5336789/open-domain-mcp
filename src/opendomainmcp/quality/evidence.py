from __future__ import annotations

from ..context import Context
from ..validation import ValidationStore, summarize_validation
from .policy import build_policy_evidence
from .readiness import compute_readiness


def compute_quality_evidence(ctx: Context, tasks: list[dict] | None = None) -> dict:
    readiness = compute_readiness(ctx, tasks=tasks)
    evidence = [
        _coverage_card(readiness),
        _review_card(readiness),
        _articles_card(readiness),
        _retrieval_card(readiness),
        _graph_card(readiness),
        _simulation_card(ctx, readiness),
        build_policy_evidence(ctx.settings),
        _jobs_card(readiness),
    ]
    return {
        "collection": readiness["collection"],
        "status": _overall_status(evidence),
        "score": _overall_score(evidence),
        "next_action": _overall_action(evidence),
        "evidence": evidence,
    }


def _coverage_card(readiness: dict) -> dict:
    health = readiness["source_health"]
    chunks = int(health.get("chunks") or 0)
    failed = int(health.get("failed") or 0)
    stale = int(health.get("stale") or 0)
    if chunks == 0:
        status, score = "blocked", 0
        summary = "No indexed knowledge objects."
        action = "Add sources in Source Intake."
    elif failed:
        status, score = "blocked", 0
        summary = _count_label(failed, "source failure")
        action = "Inspect failed source ingestion."
    elif stale:
        status, score = "needs_review", 70
        summary = _count_label(stale, "stale source")
        action = "Refresh stale sources."
    else:
        status, score = "ready", 100
        summary = f"{chunks} indexed knowledge objects across {health.get('sources', 0)} sources."
        action = "Coverage is sufficient."
    return {
        "id": "coverage",
        "gate": "Coverage",
        "status": status,
        "score": score,
        "summary": summary,
        "details": [
            f"{health.get('sources', 0)} sources",
            f"{chunks} chunks",
            f"{stale} stale",
            f"{failed} failed",
        ],
        "action": action,
    }


def _review_card(readiness: dict) -> dict:
    health = readiness["review_health"]
    total = sum(int(health.get(key) or 0) for key in ("approved", "pending", "rejected", "unset"))
    approved = int(health.get("approved") or 0)
    pending = int(health.get("pending") or 0)
    rejected = int(health.get("rejected") or 0)
    unset = int(health.get("unset") or 0)
    score = round(float(health.get("approved_ratio") or 0) * 100)
    if total == 0:
        status = "blocked"
        summary = "No knowledge objects to review."
        action = "Add sources in Source Intake."
    elif pending or rejected or unset or approved == 0:
        status = "needs_review"
        summary = f"{approved} of {total} knowledge objects are approved."
        action = readiness["next_action"] if readiness["status"] == "needs_review" else "Review knowledge objects."
    else:
        status = "ready"
        summary = f"All {total} knowledge objects are approved."
        action = "Review gate is clear."
    return {
        "id": "review",
        "gate": "Review",
        "status": status,
        "score": score,
        "summary": summary,
        "details": [
            f"{pending} pending",
            f"{rejected} rejected",
            f"{unset} unreviewed",
        ],
        "action": action,
    }


def _articles_card(readiness: dict) -> dict:
    health = readiness["article_health"]
    articles = int(health.get("articles") or 0)
    cross_validated = int(health.get("cross_validated") or 0)
    avg_relevance = float(health.get("avg_relevance") or 0)
    score = round(avg_relevance * 100) if articles else 0
    if articles == 0:
        status = "needs_review"
        summary = "No synthesized articles."
        details = ["0 cross-validated", "average relevance 0%"]
        action = "Synthesize articles."
    elif cross_validated < articles:
        status = "needs_review"
        needs_curation = articles - cross_validated
        summary = f"{articles} synthesized articles, {cross_validated} cross-validated."
        details = [
            f"average relevance {_percent(avg_relevance)}",
            f"{needs_curation} needs curation",
        ]
        action = "Curate synthesized articles."
    else:
        status = "ready"
        summary = f"All {articles} synthesized articles are cross-validated."
        details = [f"average relevance {_percent(avg_relevance)}", "0 needs curation"]
        action = "Article evidence is ready."
    return {
        "id": "articles",
        "gate": "Articles",
        "status": status,
        "score": score,
        "summary": summary,
        "details": details,
        "action": action,
    }


def _retrieval_card(readiness: dict) -> dict:
    health = readiness["retrieval_health"]
    events = int(health.get("events") or 0)
    hit_rate = float(health.get("grounding_hit_rate") or 0)
    avg_score = float(health.get("avg_score") or 0)
    precision = float(health.get("retrieval_precision") or 0)
    score = round(hit_rate * 100) if events else 0
    if events == 0:
        status = "validating"
        summary = "No retrieval evidence recorded."
        action = "Run Advisor or Simulator scenarios."
    elif hit_rate < 0.8 or precision < 0.5:
        status = "needs_review"
        summary = f"{events} retrieval events with {_percent(hit_rate)} grounding hit rate."
        action = "Improve grounding before publishing."
    else:
        status = "ready"
        summary = f"{events} retrieval events with {_percent(hit_rate)} grounding hit rate."
        action = "Keep validating with representative scenarios."
    return {
        "id": "retrieval",
        "gate": "Retrieval",
        "status": status,
        "score": score,
        "summary": summary,
        "details": [
            f"average score {_percent(avg_score)}",
            f"precision {_percent(precision)}",
        ],
        "action": action,
    }


def _graph_card(readiness: dict) -> dict:
    health = readiness["graph_health"]
    available = bool(health.get("available"))
    entities = int(health.get("entities") or 0)
    workflows = int(health.get("workflows") or 0)
    if not available:
        status, score = "blocked", 0
        summary = "Graph store is unavailable."
        action = "Restore graph store connectivity."
    elif entities or workflows:
        status, score = "ready", 100
        summary = f"{entities} entities and {workflows} workflows indexed."
        action = "Graph evidence is ready."
    else:
        status, score = "needs_review", 50
        summary = "No graph entities or workflows indexed."
        action = "Ingest workflow or dependency-rich sources."
    return {
        "id": "graph",
        "gate": "Graph",
        "status": status,
        "score": score,
        "summary": summary,
        "details": [f"{entities} entities", f"{workflows} workflows"],
        "action": action,
    }


def _simulation_card(ctx: Context, readiness: dict) -> dict:
    summary = summarize_validation(
        ValidationStore(ctx.settings.data_dir),
        readiness["collection"],
    )
    scenarios = int(summary.get("scenario_count") or 0)
    latest_runs = int(summary.get("latest_run_count") or 0)
    passed = int(summary.get("passed") or 0)
    failed = int(summary.get("failed") or 0)
    if latest_runs == 0:
        status, score = "validating", 0
        text = "No validation scenarios have been run."
        action = "Run validation scenarios in Agent Simulator."
    elif failed:
        status, score = "blocked", 0
        text = _count_label(failed, "validation scenario failed")
        action = "Inspect failed validation scenarios."
    else:
        status = "ready"
        score = round(float(summary.get("pass_rate") or 0.0) * 100)
        text = _count_label(passed, "validation scenario passed")
        action = "Simulation gate is clear."
    return {
        "id": "simulation",
        "gate": "Simulation",
        "status": status,
        "score": score,
        "summary": text,
        "details": [
            _plain_count(scenarios, "scenario"),
            _plain_count(latest_runs, "latest run"),
            _plain_count(passed, "passed"),
            _plain_count(failed, "failed"),
        ],
        "action": action,
    }


def _jobs_card(readiness: dict) -> dict:
    health = readiness["job_health"]
    queued = int(health.get("queued") or 0)
    running = int(health.get("running") or 0)
    error = int(health.get("error") or 0)
    if error:
        status, score = "blocked", 0
        summary = _count_label(error, "background job failed")
        action = "Inspect failed background jobs."
    elif queued or running:
        status, score = "validating", 50
        summary = f"{queued + running} background jobs are active."
        action = "Wait for background jobs to finish."
    else:
        status, score = "ready", 100
        summary = "No active or failed background jobs."
        action = "Job gate is clear."
    return {
        "id": "jobs",
        "gate": "Jobs",
        "status": status,
        "score": score,
        "summary": summary,
        "details": [
            f"{queued} queued",
            f"{running} running",
            f"{error} failed",
        ],
        "action": action,
    }


def _overall_status(evidence: list[dict]) -> str:
    statuses = [card["status"] for card in evidence]
    for status in ("blocked", "validating", "needs_review"):
        if status in statuses:
            return status
    return "ready"


def _overall_score(evidence: list[dict]) -> int:
    if not evidence:
        return 0
    if any(card["status"] == "blocked" for card in evidence):
        return 0
    return round(sum(int(card.get("score") or 0) for card in evidence) / len(evidence))


def _overall_action(evidence: list[dict]) -> str:
    for status in ("blocked", "validating", "needs_review"):
        for card in evidence:
            if card["status"] == status:
                return card["action"]
    return "Quality evidence is ready."


def _count_label(count: int, singular: str) -> str:
    if count == 1:
        return f"1 {singular}."
    if singular == "background job failed":
        return f"{count} background jobs failed."
    if singular.endswith("failed") or singular.endswith("passed"):
        base = singular.rsplit(" ", 1)[0]
        state = singular.rsplit(" ", 1)[1]
        return f"{count} {base}s {state}."
    return f"{count} {singular}s."


def _plain_count(count: int, singular: str) -> str:
    if singular in {"passed", "failed"}:
        return f"{count} {singular}"
    if count == 1:
        return f"1 {singular}"
    return f"{count} {singular}s"


def _percent(value: float) -> str:
    return f"{round(value * 100)}%"
