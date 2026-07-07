"""Consensus orchestrator: collect → pair → adjudicate → merge → store."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from opendomainmcp.config import Settings
from opendomainmcp.consensus.adjudicate import RuleAdjudicator
from opendomainmcp.consensus.merge import merge_groups
from opendomainmcp.consensus.pairing import find_candidates
from opendomainmcp.consensus.units import collect_rule_units
from opendomainmcp.graph.models import Edge, Entity
from opendomainmcp.graph.normalize import normalize_name

logger = logging.getLogger(__name__)

_PAGE = 200


def run_consensus(
    store,
    settings: Settings,
    graph=None,
    progress: Optional[Callable[[dict], None]] = None,
    adjudicator: Optional[RuleAdjudicator] = None,
) -> dict:
    """Run a full consensus pass over the corpus.

    Stages (progress events emitted for each): units, pairing, adjudicate,
    merge, store.

    Returns::

        {
            "units": int,
            "candidates": int,
            "adjudicated": int,
            "cache_hits": int,
            "rules_created": int,
            "conflicts": int,           # verdict-level conflict count
            "trust": {"high": n, "normal": n, "conflicted": n},
            "pruned": int,
            "errors": list[dict],       # per-pair errors
        }

    Guards
    ------
    Total adjudication failure (adjudicated == 0 and candidates > 0): skip
    upsert AND pruning so a prior consensus is not wiped.
    """

    def _emit(stage: str, **kw: object) -> None:
        if progress:
            progress({"stage": stage, **kw})

    errors: list[dict] = []

    # ------------------------------------------------------------------
    # Stage 1: collect rule units
    # ------------------------------------------------------------------
    units = collect_rule_units(store)
    _emit("units", count=len(units))

    # ------------------------------------------------------------------
    # Stage 2: find candidate pairs
    # ------------------------------------------------------------------
    candidates = find_candidates(
        units,
        store._embedder,
        graph,
        threshold=settings.consensus_similarity_threshold,
    )
    _emit("pairing", count=len(candidates))

    # ------------------------------------------------------------------
    # Stage 3: adjudicate each pair
    # ------------------------------------------------------------------
    if adjudicator is None:
        adjudicator = RuleAdjudicator(settings)

    verdicts: list[tuple[str, str, str]] = []
    adjudicated = 0
    # Snapshot the counter so a reused adjudicator instance reports this
    # run's cache-hit delta, not its cumulative total.
    pre_hits = adjudicator.cache_hits

    for pair in candidates:
        quotes_a = [ev.get("quote", "") for ev in pair.a.evidence]
        quotes_b = [ev.get("quote", "") for ev in pair.b.evidence]
        try:
            verdict = adjudicator.judge(
                pair.a.claim, quotes_a,
                pair.b.claim, quotes_b,
            )
            verdicts.append((pair.a.key, pair.b.key, verdict))
            adjudicated += 1
        except Exception as exc:
            errors.append({"pair": [pair.a.key, pair.b.key], "error": repr(exc)})

    # Always save the cache — even on partial failure.
    adjudicator.save()
    cache_hits = adjudicator.cache_hits - pre_hits
    _emit("adjudicate", adjudicated=adjudicated, errors=len(errors))

    # ------------------------------------------------------------------
    # Stage 4: merge into rule items
    # ------------------------------------------------------------------
    rules = merge_groups(units, verdicts, review_mode=settings.review_mode)
    _emit("merge", rules=len(rules))

    # Compute result metrics from the merged rules and verdicts.
    trust_counts: dict[str, int] = {"high": 0, "normal": 0, "conflicted": 0}
    for r in rules:
        trust_counts[r.trust] = trust_counts.get(r.trust, 0) + 1

    conflicts = sum(1 for _, _, v in verdicts if v == "conflict")

    # ------------------------------------------------------------------
    # Stage 5: store rules + prune stale
    # ------------------------------------------------------------------
    pruned = 0
    rules_created = 0

    # Guard: total adjudication failure must not wipe prior consensus.
    if adjudicated == 0 and len(candidates) > 0:
        logger.warning(
            "Total adjudication failure (%d candidates, 0 adjudicated); "
            "skipping upsert and pruning to preserve prior consensus.",
            len(candidates),
        )
        _emit("store", skipped=True, reason="total_adjudication_failure")
    else:
        # Collect existing rule ids (paginated) before upserting new ones.
        # Also capture review_status and member_keys so we can carry human
        # approve/reject decisions across re-runs (Fix 1).
        existing_ids: set[str] = set()
        # Maps rule_id → {"review_status": str, "member_keys_set": set[str]}
        existing_rule_info: dict[str, dict] = {}
        offset = 0
        while True:
            try:
                items = store.get_items(
                    limit=_PAGE, offset=offset, where={"kind": "rule"}
                )
            except Exception as exc:
                logger.warning("Pagination of existing rules failed at offset %d: %r",
                               offset, exc)
                errors.append({"stage": "existing_rules_pagination",
                               "error": repr(exc)})
                break
            if not items:
                break
            for item in items:
                rid = item["id"]
                existing_ids.add(rid)
                meta = item.get("metadata", {})
                raw_mk = meta.get("member_keys", "")
                mk_set = set(k.strip() for k in raw_mk.split(",") if k.strip())
                existing_rule_info[rid] = {
                    "review_status": meta.get("review_status", "pending"),
                    "member_keys_set": mk_set,
                    "trust": meta.get("trust", "normal"),
                }
            offset += len(items)
            if len(items) < _PAGE:
                break

        # Fix 1: carry human approve/reject across re-runs.
        # Only restore if same membership AND trust did not newly become "conflicted".
        if existing_rule_info:
            for rule in rules:
                prior = existing_rule_info.get(rule.id)
                if prior is None:
                    continue
                human_status = prior["review_status"]
                if human_status not in ("approved", "rejected"):
                    continue
                # Membership check: compare member_keys sets.
                same_membership = set(rule.member_keys) == prior["member_keys_set"]
                newly_conflicted = (rule.trust == "conflicted"
                                    and prior["trust"] != "conflicted")
                if same_membership and not newly_conflicted:
                    rule.review_status = human_status

        if rules:
            store.upsert(rules)

        # "Created" means genuinely new: expected ids that were not already
        # stored.  Everything is still upserted (refreshes existing rules).
        expected_ids = {r.id for r in rules}
        rules_created = len(expected_ids - existing_ids)

        # Prune stale rule ids not in the newly produced set.
        stale_ids = existing_ids - expected_ids
        if stale_ids:
            store.delete_ids(stale_ids)
            pruned = len(stale_ids)
            # Fix 4: also remove graph rows for pruned rule ids.
            if graph is not None:
                try:
                    graph.delete_for_chunks(stale_ids)
                except Exception as exc:
                    errors.append({"stage": "prune_graph", "error": repr(exc)})

        _emit("store", rules_created=rules_created, pruned=pruned)

        # Graph: conflict edges and entity lineage.
        if graph is not None:
            _upsert_graph(graph, rules, verdicts, errors)

    return {
        "units": len(units),
        "candidates": len(candidates),
        "adjudicated": adjudicated,
        "cache_hits": cache_hits,
        "rules_created": rules_created,
        "conflicts": conflicts,
        "trust": trust_counts,
        "pruned": pruned,
        "errors": errors,
    }


def _upsert_graph(graph, rules, verdicts: list[tuple[str, str, str]],
                  errors: list[dict]) -> None:
    """Upsert rule entities and conflict edges into the graph store.

    For every rule: one Entity per member_chunk_id AND chain_chunk_id
    (establishes entity_chunks lineage) plus one keyed by the rule's own id.
    For every cross-group conflict verdict: one Edge with relation_type="conflicts".

    Graph failures are surfaced into ``errors`` (Fix 5) so the caller can
    report them without hiding behind a silent warning.

    Normalized entity names are truncated to 255 chars for VARCHAR(255) safety
    (Fix 5).
    """
    _MAX_NAME = 255

    def _norm(statement: str) -> str:
        return normalize_name(statement)[:_MAX_NAME]

    # Build unit-key → rule mapping for edge resolution.
    key_to_rule = {}
    for rule in rules:
        for key in rule.member_keys:
            key_to_rule[key] = rule

    # Entities: one row per (rule, chunk_id) for lineage tracking.
    # member_chunk_ids = chunk-origin suppression set; chain_chunk_ids = lineage.
    entities: list[Entity] = []
    for rule in rules:
        norm = _norm(rule.statement)
        # Primary entity anchored by the rule's content-hash id.
        entities.append(Entity(
            normalized_name=norm,
            display_name=rule.statement,
            type="rule",
            chunk_id=rule.id,
        ))
        # Chunk-origin member ids (entity_chunks lineage).
        for cid in rule.member_chunk_ids:
            entities.append(Entity(
                normalized_name=norm,
                display_name=rule.statement,
                type="rule",
                chunk_id=cid,
            ))
        # Chain-origin member ids (lineage only, not suppression set).
        for cid in rule.chain_chunk_ids:
            entities.append(Entity(
                normalized_name=norm,
                display_name=rule.statement,
                type="rule",
                chunk_id=cid,
            ))

    if entities:
        try:
            graph.upsert_entities(entities)
        except Exception as exc:
            logger.warning("Graph entity upsert failed: %r", exc)
            errors.append({"stage": "graph", "error": repr(exc)})

    # Edges: one per cross-group conflict verdict.
    edges: list[Edge] = []
    for key_a, key_b, verdict in verdicts:
        if verdict != "conflict":
            continue
        rule_a = key_to_rule.get(key_a)
        rule_b = key_to_rule.get(key_b)
        if rule_a is None or rule_b is None:
            continue
        if rule_a.id == rule_b.id:
            continue  # intra-group: not a cross-group conflict
        edges.append(Edge(
            src=_norm(rule_a.statement),
            dst=_norm(rule_b.statement),
            relation_type="conflicts",
            chunk_id=rule_a.id,
        ))

    if edges:
        try:
            graph.upsert_edges(edges)
        except Exception as exc:
            logger.warning("Graph edge upsert failed: %r", exc)
            errors.append({"stage": "graph", "error": repr(exc)})
