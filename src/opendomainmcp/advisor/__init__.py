"""Pre-Execution Advisor (PRD Phase 4, TASKS #5.1).

Before an agent acts on an intent ``X``, it should know what the knowledge base
already records about it. :func:`advise` aggregates that into five facets --
Workflow, Risks, Permissions, Dependencies, Constraints -- by running filtered
retrieval over the shared store (the same ``build_where`` + ``retrieve_approved_only``
behaviour as :func:`opendomainmcp.views.run_view_tool`) and a best-effort read of
the knowledge graph for dependencies.

This module is pure aggregation: no LLM calls and no project-external
dependencies. Each facet is a list of ``SearchResult.to_dict()`` dicts,
deduplicated by id within the facet.
"""

from __future__ import annotations

from typing import Iterable

# knowledge_type vocabulary buckets per facet (see views module docstring).
_WORKFLOW_TYPES = ("Workflow", "Runbook")
_RISK_TYPES = ("Error", "Troubleshooting", "Constraint")
_PERMISSION_TYPES = ("Permission",)
_CONSTRAINT_TYPES = ("Constraint",)
_ARCHITECTURE_TYPES = ("Architecture",)

# Graph relation types that express a dependency between entities.
_DEPENDENCY_RELATIONS = ("imports", "depends_on")


def _approved_filters(ctx) -> dict:
    """Base filters honouring the ``retrieve_approved_only`` setting.

    Mirrors :func:`opendomainmcp.views.run_view_tool` exactly: when the setting
    is on, results are restricted to approved knowledge and unreviewed (legacy)
    chunks are intentionally excluded.
    """
    filters: dict = {}
    if getattr(ctx.settings, "retrieve_approved_only", False):
        filters["review_status"] = "approved"
    return filters


def _search_types(ctx, action: str, knowledge_types: Iterable[str], top_k: int) -> list[dict]:
    """Retrieve results across several ``knowledge_type`` values for ``action``.

    Chroma equality filters cannot express OR over a field, so we query once per
    type and merge, deduplicating by id and keeping the first (highest-ranked)
    occurrence. Results are trimmed to ``top_k``.
    """
    from ..store import build_where

    seen: set[str] = set()
    merged: list[dict] = []
    for knowledge_type in knowledge_types:
        filters = _approved_filters(ctx)
        filters["knowledge_type"] = knowledge_type
        where = build_where(filters)
        results = ctx.store.search(
            action, top_k=top_k, where=where, mode=ctx.settings.search_mode
        )
        for result in results:
            if result.id in seen:
                continue
            seen.add(result.id)
            merged.append(result.to_dict())
    return merged[:top_k]


def _graph_dependencies(ctx, action: str) -> list[dict]:
    """Best-effort dependency entities pulled from the knowledge graph.

    Looks up the action as a workflow (its prerequisites and step entities) and
    as an entity (its ``imports``/``depends_on`` neighbours). A Null or empty
    graph yields an empty list rather than raising; every graph call is guarded.
    """
    graph = getattr(ctx, "graph", None)
    if graph is None:
        return []

    seeds: list[str] = [action]
    seeds.extend(_workflow_seed_names(graph, action))

    deps: list[dict] = []
    seen: set[str] = set()
    for name in seeds:
        for relation in _DEPENDENCY_RELATIONS:
            for neighbour in _safe_neighbors(graph, name, relation):
                entity = neighbour.get("entity") or {}
                key = entity.get("normalized_name") or entity.get("name")
                if not key or key in seen:
                    continue
                seen.add(key)
                deps.append({
                    "name": entity.get("name"),
                    "type": entity.get("type"),
                    "relation_type": neighbour.get("relation_type"),
                    "direction": neighbour.get("direction"),
                    "source": "graph",
                })
    return deps


def _workflow_seed_names(graph, action: str) -> list[str]:
    """Workflow names related to ``action`` to use as graph dependency seeds."""
    names: list[str] = []
    try:
        for row in graph.list_workflows(q=action) or []:
            name = row.get("name")
            if name and name not in names:
                names.append(name)
    except Exception:  # noqa: BLE001 - graph is best-effort here
        pass
    return names


def _safe_neighbors(graph, name: str, relation_type: str) -> list[dict]:
    """Return graph neighbours for one relation, swallowing graph errors."""
    try:
        result = graph.neighbors(name, relation_type=relation_type) or {}
    except Exception:  # noqa: BLE001 - Null/empty graph must not break advice
        return []
    return result.get("neighbors", [])


def _graph_workflow(ctx, action: str) -> dict | None:
    """Best-effort ordered workflow (prerequisites + steps) for ``action``."""
    graph = getattr(ctx, "graph", None)
    if graph is None:
        return None
    try:
        return graph.get_workflow(action)
    except Exception:  # noqa: BLE001 - graph is best-effort
        return None


def advise(ctx, action: str, top_k: int = 5) -> dict:
    """Aggregate what an agent should know BEFORE performing ``action``.

    Returns a structured, JSON-serialisable dict with five retrieval facets
    (workflow, risks, permissions, dependencies, constraints), a best-effort
    graph workflow, and a summary of counts and observed knowledge types.

    Args:
        ctx: a runtime ``Context`` (or compatible object) exposing ``settings``,
            ``store`` and ``graph``.
        action: the intent/action the agent is about to take.
        top_k: max results per facet.

    Raises:
        ValueError: if ``action`` is empty or not a string.
    """
    if not isinstance(action, str) or not action.strip():
        raise ValueError("action must be a non-empty string")

    workflow = _search_types(ctx, action, _WORKFLOW_TYPES, top_k)
    risks = _search_types(ctx, action, _RISK_TYPES, top_k)
    permissions = _search_types(ctx, action, _PERMISSION_TYPES, top_k)
    constraints = _search_types(ctx, action, _CONSTRAINT_TYPES, top_k)

    architecture = _search_types(ctx, action, _ARCHITECTURE_TYPES, top_k)
    dependencies = _dedupe_dependencies(_graph_dependencies(ctx, action) + architecture)

    facets = {
        "workflow": workflow,
        "risks": risks,
        "permissions": permissions,
        "dependencies": dependencies,
        "constraints": constraints,
    }
    return {
        "action": action,
        **facets,
        "graph_workflow": _graph_workflow(ctx, action),
        "summary": _summarize(facets),
    }


def _dedupe_dependencies(items: list[dict]) -> list[dict]:
    """Deduplicate mixed graph/architecture dependency entries.

    Graph entries are keyed by ``name``; retrieval entries by ``id``. The first
    occurrence wins so graph-derived dependencies rank ahead of architecture
    chunks.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        key = item.get("id") or item.get("name") or ""
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _summarize(facets: dict[str, list[dict]]) -> dict:
    """Build the summary: per-facet counts and observed knowledge types."""
    counts = {name: len(items) for name, items in facets.items()}
    knowledge_types: list[str] = []
    for items in facets.values():
        for item in items:
            ktype = (item.get("metadata") or {}).get("knowledge_type")
            if ktype and ktype not in knowledge_types:
                knowledge_types.append(ktype)
    return {"counts": counts, "knowledge_types": sorted(knowledge_types)}
