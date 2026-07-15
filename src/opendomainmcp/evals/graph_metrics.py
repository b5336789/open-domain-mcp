"""Pure structural quality metrics over an exported knowledge graph.

Every function takes plain lists/dicts (the shape returned by
``GraphStoreProtocol.export_graph()``) and returns a JSON-serializable dict —
no I/O, no LLM, no DB — so the whole module is unit-testable offline. The
benchmarks/erpnext/graph_quality.py runner wires these to a live store.
"""
from __future__ import annotations

import re
from collections import defaultdict

# Unicode letters/digits (input is lowercased first); underscore is a separator.
_TOKEN_RE = re.compile(r"[^\W_]+")

# Report lists are capped for readability; the cap is always reported
# alongside a *_truncated flag (Fail Loud — no silent truncation).
_MAX_CLUSTERS = 20


def canonical_key(name: str) -> str:
    """Collapse naming variants (case, separators, simple plurals, token
    order) so 'Sales Orders' and 'sales_order' compare equal."""
    tokens = _TOKEN_RE.findall(name.lower())
    singular = [t[:-1] if len(t) > 3 and t.endswith("s") else t for t in tokens]
    return " ".join(sorted(singular))


def duplication(entities: list[dict]) -> dict:
    """Group entities by canonical key; clusters of >1 are likely the same
    concept split into multiple nodes (graph fragmentation at the node level)."""
    groups: dict[str, list[str]] = defaultdict(list)
    for e in entities:
        key = canonical_key(e["normalized_name"])
        if not key:  # symbols-only/blank names can never legitimately cluster
            continue
        groups[key].append(e["display_name"])
    clusters = sorted((names for names in groups.values() if len(names) > 1),
                      key=len, reverse=True)
    total = len(entities)
    excess = sum(len(c) - 1 for c in clusters)
    return {
        "total_entities": total,
        "duplicate_clusters": len(clusters),
        "excess_entities": excess,
        "duplication_ratio": excess / total if total else 0.0,
        "clusters": clusters[:_MAX_CLUSTERS],
        "clusters_truncated": len(clusters) > _MAX_CLUSTERS,
    }


def concept_recall(entities: list[dict], golden: list[str]) -> dict:
    """Fraction of hand-curated core concepts present in the graph
    (canonical-key match). The missing list is the actionable output."""
    keys = {canonical_key(e["normalized_name"]) for e in entities}
    missing = [c for c in golden if canonical_key(c) not in keys]
    found = len(golden) - len(missing)
    return {
        "golden_total": len(golden),
        "found": found,
        "recall": found / len(golden) if golden else None,
        "missing_concepts": missing,
    }
