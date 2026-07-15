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
    clusters = sorted(
        (sorted(names) for names in groups.values() if len(names) > 1),
        key=lambda c: (-len(c), c),
    )
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


def extraction_coverage(chunk_ids: set[str], entity_chunks: list[dict]) -> dict:
    """Chunks with >=1 extracted entity. The uncovered list is the complete
    re-extraction work-list — never truncated."""
    universe = set(chunk_ids)
    covered = {ec["chunk_id"] for ec in entity_chunks} & universe
    uncovered = sorted(universe - covered)
    return {
        "chunks_total": len(universe),
        "chunks_with_entities": len(covered),
        "coverage": len(covered) / len(universe) if universe else 0.0,
        "uncovered_chunk_ids": uncovered,
    }


def orphan_ratio(entities: list[dict], edges: list[dict]) -> dict:
    """Entities that appear in no edge at all (entity-only extractions)."""
    linked = {e["src"] for e in edges} | {e["dst"] for e in edges}
    orphans = sorted(e["normalized_name"] for e in entities
                     if e["normalized_name"] not in linked)
    total = len(entities)
    return {
        "entities_total": total,
        "orphans": len(orphans),
        "orphan_ratio": len(orphans) / total if total else 0.0,
        "orphan_names": orphans,
    }


def connectivity(entities: list[dict], edges: list[dict]) -> dict:
    """Connected components via union-find (undirected). Many small
    components = a fragmented graph."""
    parent = {e["normalized_name"]: e["normalized_name"] for e in entities}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path halving
            x = parent[x]
        return x

    for e in edges:
        if e["src"] in parent and e["dst"] in parent:
            ra, rb = find(e["src"]), find(e["dst"])
            if ra != rb:
                parent[ra] = rb

    sizes: dict[str, int] = defaultdict(int)
    for node in parent:
        sizes[find(node)] += 1
    comp_sizes = sorted(sizes.values(), reverse=True)
    total = len(parent)
    return {
        "components": len(comp_sizes),
        "largest_component_share": comp_sizes[0] / total if comp_sizes else 0.0,
        "component_sizes_top": comp_sizes[:10],
        "singleton_components": sum(1 for s in comp_sizes if s == 1),
    }


def compute_all(export: dict, chunk_ids: set[str], golden: list[str]) -> dict:
    """Assemble the full quality report from one export_graph() payload."""
    entities, edges = export["entities"], export["edges"]
    return {
        "coverage": extraction_coverage(chunk_ids, export["entity_chunks"]),
        "orphans": orphan_ratio(entities, edges),
        "connectivity": connectivity(entities, edges),
        "duplication": duplication(entities),
        "concept_recall": concept_recall(entities, golden),
    }
