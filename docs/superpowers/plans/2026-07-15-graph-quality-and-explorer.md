# Graph Quality Metrics + Graph Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure the knowledge graph's structural quality with five offline metrics, and upgrade the SPA's Graph page with an ego-network visualization that lets a human spot-check edges against their evidence.

**Architecture:** One new read method (`export_graph()`) on `GraphStoreProtocol` feeds pure metric functions in `evals/graph_metrics.py`; a thin runner in `benchmarks/erpnext/` wires them to the live store and writes a diffable JSON report. On the frontend, a new `GraphExplorer` component (react-force-graph-2d) is toggled into the existing `Graph.tsx` entities mode; it reuses the existing `/api/graph/entity/{name}` endpoint — zero backend API changes.

**Tech Stack:** Python ≥ 3.11 (stdlib only — no NetworkX), pytest, MariaDB (integration-marked tests only), React 18 + TypeScript + `react-force-graph-2d`, Playwright with route mocks.

**Spec:** `docs/superpowers/specs/2026-07-15-graph-quality-and-explorer-design.md`

## Global Constraints

- All pytest tests run **offline** — no network, no model download, no DB (except tests marked `integration`, which require live `GRAPH_DB_*` env).
- **No NetworkX**: connected components use a ~20-line stdlib union-find.
- Only new npm dependency allowed: `react-force-graph-2d`.
- **Fail Loud**: truncation is always shown with a count ("showing 50 of 173"); the quality runner exits with an error if the graph is empty/unwired — never a silently empty report.
- Surgical changes: the existing list view in `Graph.tsx` and all existing API routes stay untouched.
- Backend venv: `.venv` — run tests as `.venv/bin/python -m pytest ...` from the repo root.
- Frontend commands run in `web/` (`npm run build`, `npx playwright test`).
- Entity dict key vocabulary (used across all tasks): entities have `normalized_name`, `display_name`, `type`; edges have `src`, `dst`, `relation_type`, `chunk_id`, `confidence`; entity_chunks have `normalized_name`, `chunk_id`.

---

### Task 1: `export_graph()` protocol method (+ spec amendment)

The metrics need a bulk read of all three graph tables for a collection. The protocol only offers per-entity `neighbors()` today. Add **one** method to `GraphStoreProtocol`, `NullGraphStore`, and `MariaGraphStore`.

**Files:**
- Modify: `src/opendomainmcp/graph/store.py` (three classes: protocol ~line 24, Null ~line 41, Maria ~line 154)
- Test: `tests/test_graph_export.py` (create)
- Test: `tests/test_graph_store_mariadb.py` (append one integration test)
- Modify: `docs/superpowers/specs/2026-07-15-graph-quality-and-explorer-design.md` (two corrections, see Step 7)

**Interfaces:**
- Produces: `export_graph() -> dict` with keys:
  - `"entities"`: `list[dict]` — `{normalized_name, display_name, type}`
  - `"edges"`: `list[dict]` — `{src, dst, relation_type, chunk_id, confidence}`
  - `"entity_chunks"`: `list[dict]` — `{normalized_name, chunk_id}`
  All scoped to the store's collection. `NullGraphStore` returns the dict with three empty lists (same shape — callers never branch).

- [ ] **Step 1: Write the failing offline test**

```python
# tests/test_graph_export.py
"""export_graph(): bulk read used by the graph quality metrics."""
from opendomainmcp.graph.store import NullGraphStore


def test_null_store_export_graph_shape():
    export = NullGraphStore().export_graph()
    assert export == {"entities": [], "edges": [], "entity_chunks": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_graph_export.py -v`
Expected: FAIL with `AttributeError: 'NullGraphStore' object has no attribute 'export_graph'`

- [ ] **Step 3: Implement**

In `src/opendomainmcp/graph/store.py`:

Add to `GraphStoreProtocol` (after `list_workflows`, keeping the one-line style):

```python
    def export_graph(self) -> dict: ...
```

Add to `NullGraphStore` (after `list_workflows`):

```python
    def export_graph(self) -> dict:
        return {"entities": [], "edges": [], "entity_chunks": []}
```

Add to `MariaGraphStore` (after `list_workflows`, before `upsert_functions`):

```python
    def export_graph(self) -> dict:
        """Bulk read of the collection's graph for offline quality metrics."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT normalized_name, display_name, type FROM entities "
                        "WHERE collection=%s", (self._collection,))
            entities = list(cur.fetchall())
            cur.execute("SELECT src, dst, relation_type, chunk_id, confidence "
                        "FROM edges WHERE collection=%s", (self._collection,))
            edges = list(cur.fetchall())
            cur.execute("SELECT normalized_name, chunk_id FROM entity_chunks "
                        "WHERE collection=%s", (self._collection,))
            entity_chunks = list(cur.fetchall())
        return {"entities": entities, "edges": edges, "entity_chunks": entity_chunks}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_graph_export.py -v`
Expected: PASS

- [ ] **Step 5: Append the MariaDB integration test**

Append to `tests/test_graph_store_mariadb.py` (uses the existing `maria_store` fixture; runs only with live `GRAPH_DB_*` env, skipped otherwise):

```python
def test_export_graph_roundtrip(maria_store):
    maria_store.upsert_entities([
        Entity("auth service", "Auth Service", "Service", "it-c1"),
        Entity("user db", "User DB", "Resource", "it-c1")])
    maria_store.upsert_edges([Edge("auth service", "user db", "depends_on", "it-c1")])
    export = maria_store.export_graph()
    names = {e["normalized_name"] for e in export["entities"]}
    assert {"auth service", "user db"} <= names
    assert any(e["src"] == "auth service" and e["dst"] == "user db"
               and e["relation_type"] == "depends_on" for e in export["edges"])
    assert any(ec["chunk_id"] == "it-c1" for ec in export["entity_chunks"])
    maria_store.delete_for_chunks(["it-c1"])
```

- [ ] **Step 6: Run full offline suite to check for regressions**

Run: `.venv/bin/python -m pytest`
Expected: all pass (integration tests auto-skip without `GRAPH_DB_HOST`). If `GRAPH_DB_*` env is configured, also run `.venv/bin/python -m pytest tests/test_graph_store_mariadb.py -m integration -v` and expect PASS.

- [ ] **Step 7: Amend the spec (two accuracy fixes)**

In `docs/superpowers/specs/2026-07-15-graph-quality-and-explorer-design.md`:

1. Replace the protocol-extension bullet:
   - old: "`GraphStoreProtocol.list_edges(limit)` (bulk edge read; the protocol currently only offers per-entity `neighbors`)."
   - new: "`GraphStoreProtocol.export_graph()` — one bulk read returning `{entities, edges, entity_chunks}` for the collection (coverage and duplication metrics need entities and the entity↔chunk map, not just edges)."
   Also update the sentence about `MariaGraphStore`/`NullGraphStore` accordingly ("three SELECTs" / "returns the same shape with empty lists").
2. Replace the e2e sentence "(against the seeded dev backend, same as existing e2e)" with "(self-contained route mocks via `tests/helpers/mockApi.ts`, like the rest of the e2e suite — no live backend)".

- [ ] **Step 8: Commit**

```bash
git add src/opendomainmcp/graph/store.py tests/test_graph_export.py tests/test_graph_store_mariadb.py docs/superpowers/specs/2026-07-15-graph-quality-and-explorer-design.md
git commit -m "feat(graph): export_graph() bulk read on GraphStoreProtocol"
```

---

### Task 2: Metrics module part 1 — canonical key, duplication, concept recall

Pure, name-based metric functions. No I/O, no dependencies beyond stdlib.

**Files:**
- Create: `src/opendomainmcp/evals/graph_metrics.py`
- Test: `tests/test_graph_metrics.py` (create)

**Interfaces:**
- Produces:
  - `canonical_key(name: str) -> str` — lowercase, split on non-alphanumerics, singularize tokens (strip trailing `s` when token length > 3), sort tokens, join with a space. `canonical_key("Sales Orders") == canonical_key("sales_order") == "order sale"`.
  - `duplication(entities: list[dict]) -> dict` — keys: `total_entities` (int), `duplicate_clusters` (int), `excess_entities` (int), `duplication_ratio` (float), `clusters` (list of lists of display names, largest first, top 20), `clusters_truncated` (bool).
  - `concept_recall(entities: list[dict], golden: list[str]) -> dict` — keys: `golden_total`, `found`, `recall` (float or None when golden empty), `missing_concepts` (list[str]).
- Consumes: entity dicts from Task 1's `export_graph()["entities"]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_graph_metrics.py
"""Pure structural quality metrics over an exported graph (offline, no DB)."""
from opendomainmcp.evals.graph_metrics import (
    canonical_key,
    concept_recall,
    duplication,
)


def _ent(norm, display=None, type_="Concept"):
    return {"normalized_name": norm, "display_name": display or norm, "type": type_}


# --- canonical_key ---------------------------------------------------------

def test_canonical_key_merges_case_separator_and_plural_variants():
    assert canonical_key("Sales Orders") == canonical_key("sales_order")
    assert canonical_key("tax-rule") == canonical_key("Tax Rule")


def test_canonical_key_is_token_order_insensitive():
    assert canonical_key("rate tax") == canonical_key("tax rate")


def test_canonical_key_keeps_short_tokens_verbatim():
    # "gas"/"gst" style short tokens must not be de-pluralized into collisions.
    assert canonical_key("gas") != canonical_key("ga")


# --- duplication -----------------------------------------------------------

def test_duplication_clusters_name_variants():
    entities = [_ent("sales order"), _ent("sales_orders"), _ent("tax rule")]
    d = duplication(entities)
    assert d["total_entities"] == 3
    assert d["duplicate_clusters"] == 1
    assert d["excess_entities"] == 1
    assert d["duplication_ratio"] == 1 / 3
    assert sorted(d["clusters"][0]) == ["sales order", "sales_orders"]
    assert d["clusters_truncated"] is False


def test_duplication_empty_graph_is_zero_not_crash():
    d = duplication([])
    assert d["total_entities"] == 0 and d["duplication_ratio"] == 0.0


# --- concept_recall --------------------------------------------------------

def test_concept_recall_matches_by_canonical_key():
    entities = [_ent("pricing rules"), _ent("grand total")]
    r = concept_recall(entities, ["Pricing Rule", "Tax Category"])
    assert r["golden_total"] == 2
    assert r["found"] == 1
    assert r["recall"] == 0.5
    assert r["missing_concepts"] == ["Tax Category"]


def test_concept_recall_empty_golden_returns_none():
    assert concept_recall([_ent("a b c")], [])["recall"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_graph_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'opendomainmcp.evals.graph_metrics'`

- [ ] **Step 3: Implement**

```python
# src/opendomainmcp/evals/graph_metrics.py
"""Pure structural quality metrics over an exported knowledge graph.

Every function takes plain lists/dicts (the shape returned by
``GraphStoreProtocol.export_graph()``) and returns a JSON-serializable dict —
no I/O, no LLM, no DB — so the whole module is unit-testable offline. The
benchmarks/erpnext/graph_quality.py runner wires these to a live store.
"""
from __future__ import annotations

import re
from collections import defaultdict

_TOKEN_RE = re.compile(r"[a-z0-9]+")

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
        groups[canonical_key(e["normalized_name"])].append(e["display_name"])
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_graph_metrics.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/evals/graph_metrics.py tests/test_graph_metrics.py
git commit -m "feat(evals): graph metrics — canonical key, duplication, concept recall"
```

---

### Task 3: Metrics module part 2 — coverage, orphans, connectivity, compute_all

Structure-based metrics plus the aggregator, appended to the same module.

**Files:**
- Modify: `src/opendomainmcp/evals/graph_metrics.py`
- Modify: `tests/test_graph_metrics.py` (append)

**Interfaces:**
- Consumes: `canonical_key` and friends from Task 2; export dict shape from Task 1.
- Produces:
  - `extraction_coverage(chunk_ids: set[str], entity_chunks: list[dict]) -> dict` — keys: `chunks_total`, `chunks_with_entities`, `coverage` (float), `uncovered_chunk_ids` (sorted list, complete — this is the re-extraction work-list for the future remediation iteration).
  - `orphan_ratio(entities: list[dict], edges: list[dict]) -> dict` — keys: `entities_total`, `orphans`, `orphan_ratio` (float), `orphan_names` (sorted, complete).
  - `connectivity(entities: list[dict], edges: list[dict]) -> dict` — keys: `components`, `largest_component_share` (float), `component_sizes_top` (top 10, descending), `singleton_components`.
  - `compute_all(export: dict, chunk_ids: set[str], golden: list[str]) -> dict` — keys `coverage`, `orphans`, `connectivity`, `duplication`, `concept_recall`, each the corresponding function's dict.

- [ ] **Step 1: Write the failing tests (append to `tests/test_graph_metrics.py`)**

```python
from opendomainmcp.evals.graph_metrics import (
    compute_all,
    connectivity,
    extraction_coverage,
    orphan_ratio,
)


def _edge(src, dst, rel="related_to", chunk="c1"):
    return {"src": src, "dst": dst, "relation_type": rel,
            "chunk_id": chunk, "confidence": 1.0}


# --- extraction_coverage ---------------------------------------------------

def test_coverage_reports_uncovered_chunks():
    cov = extraction_coverage(
        {"c1", "c2", "c3"},
        [{"normalized_name": "a", "chunk_id": "c1"},
         {"normalized_name": "b", "chunk_id": "c1"}])
    assert cov["chunks_total"] == 3
    assert cov["chunks_with_entities"] == 1
    assert cov["coverage"] == 1 / 3
    assert cov["uncovered_chunk_ids"] == ["c2", "c3"]


def test_coverage_ignores_stale_entity_chunks_outside_universe():
    cov = extraction_coverage({"c1"}, [{"normalized_name": "a", "chunk_id": "gone"}])
    assert cov["chunks_with_entities"] == 0


# --- orphan_ratio ----------------------------------------------------------

def test_orphans_are_entities_with_no_edges():
    entities = [_ent("a"), _ent("b"), _ent("lonely")]
    o = orphan_ratio(entities, [_edge("a", "b")])
    assert o["orphans"] == 1
    assert o["orphan_names"] == ["lonely"]
    assert o["orphan_ratio"] == 1 / 3


# --- connectivity ----------------------------------------------------------

def test_connectivity_counts_components_and_largest_share():
    entities = [_ent(n) for n in ("a", "b", "c", "d", "e")]
    edges = [_edge("a", "b"), _edge("b", "c"), _edge("d", "e")]
    c = connectivity(entities, edges)
    assert c["components"] == 2
    assert c["largest_component_share"] == 3 / 5
    assert c["component_sizes_top"] == [3, 2]
    assert c["singleton_components"] == 0


def test_connectivity_ignores_edges_to_unknown_entities():
    c = connectivity([_ent("a")], [_edge("a", "ghost")])
    assert c["components"] == 1 and c["component_sizes_top"] == [1]


def test_connectivity_empty_graph():
    c = connectivity([], [])
    assert c["components"] == 0 and c["largest_component_share"] == 0.0


# --- compute_all -----------------------------------------------------------

def test_compute_all_assembles_all_five_sections():
    export = {
        "entities": [_ent("sales order"), _ent("tax rule")],
        "edges": [_edge("sales order", "tax rule")],
        "entity_chunks": [{"normalized_name": "sales order", "chunk_id": "c1"}],
    }
    report = compute_all(export, {"c1", "c2"}, ["Sales Order", "Pricing Rule"])
    assert set(report) == {"coverage", "orphans", "connectivity",
                           "duplication", "concept_recall"}
    assert report["coverage"]["coverage"] == 0.5
    assert report["concept_recall"]["missing_concepts"] == ["Pricing Rule"]
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `.venv/bin/python -m pytest tests/test_graph_metrics.py -v`
Expected: the 7 new tests FAIL with `ImportError` (cannot import `compute_all` etc.); the 6 Task-2 tests still PASS.

- [ ] **Step 3: Implement (append to `src/opendomainmcp/evals/graph_metrics.py`)**

```python
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
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `.venv/bin/python -m pytest tests/test_graph_metrics.py -v`
Expected: 13 PASS

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/evals/graph_metrics.py tests/test_graph_metrics.py
git commit -m "feat(evals): graph metrics — coverage, orphans, connectivity, compute_all"
```

---

### Task 4: Golden concepts + `graph_quality.py` runner + benchmark README

The live-wired runner, in the mold of `benchmarks/erpnext/run_benchmark.py` (dotenv self-load, `build_context`, argparse, JSON report + printed summary). The runner itself is glue — all logic already unit-tested in Tasks 2–3.

**Files:**
- Create: `benchmarks/erpnext/golden_concepts.json`
- Create: `benchmarks/erpnext/graph_quality.py`
- Modify: `benchmarks/erpnext/README.md` (document the new script)

**Interfaces:**
- Consumes: `compute_all(export, chunk_ids, golden)` from Task 3; `ctx.graph.export_graph()` from Task 1; `ChromaStore.get_items(limit, offset)` for the chunk-id universe.
- Produces: `benchmarks/erpnext/graph-quality.report.json` (git-ignored like the other `*.report.json`).

- [ ] **Step 1: Create the golden concepts file**

Hand-curated core concepts of the pinned corpus (`taxes_and_totals.py`, `pricing_rule.py`, `utils.py`, `tax_rule.py` — tax/discount/pricing math and rule selection). Flat JSON list so non-programmers can edit it:

```json
[
  "Tax Rule",
  "Pricing Rule",
  "Tax Category",
  "Item Tax Template",
  "Sales Taxes and Charges",
  "Grand Total",
  "Net Total",
  "Rounded Total",
  "Rounding Adjustment",
  "Discount Amount",
  "Discount Percentage",
  "Additional Discount",
  "Price List Rate",
  "Item Price",
  "Charge Type",
  "Inclusive Tax",
  "Tax Fraction",
  "Actual Tax",
  "Tax Withholding",
  "Margin",
  "Free Item",
  "Mixed Conditions",
  "Priority",
  "Coupon Code",
  "Currency Conversion Rate",
  "Write Off Amount"
]
```

Note in the README (Step 3) that this list is a first pass — reviewing/extending it against the corpus is expected and cheap.

- [ ] **Step 2: Create the runner**

```python
#!/usr/bin/env python
"""Structural quality report for a collection's knowledge graph.

Computes five offline metrics (extraction coverage, orphan ratio, connectivity,
entity duplication, core-concept recall) from one ``export_graph()`` bulk read.
Zero LLM calls — safe to run repeatedly; diff two reports to compare a change.

Usage:
    .venv/bin/python benchmarks/erpnext/graph_quality.py [--collection erpnext]
        [--golden PATH] [--out benchmarks/erpnext/graph-quality.report.json]

Requires MariaDB (ODM_GRAPH_DB_*) and the Chroma data dir; exits non-zero if
the graph is empty or unwired (Fail Loud — no silently empty reports).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent.parent


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs from .env into os.environ (without overriding)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(REPO_ROOT / ".env")

from opendomainmcp.context import build_context
from opendomainmcp.evals.graph_metrics import compute_all


def _all_chunk_ids(store) -> set[str]:
    ids: set[str] = set()
    offset = 0
    while True:
        items = store.get_items(limit=500, offset=offset)
        if not items:
            return ids
        ids.update(item["id"] for item in items)
        offset += len(items)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--collection", default="erpnext")
    ap.add_argument("--golden", default=str(HERE / "golden_concepts.json"))
    ap.add_argument("--out", default=str(HERE / "graph-quality.report.json"))
    args = ap.parse_args()

    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    ctx = build_context(collection=args.collection)
    export = ctx.graph.export_graph()
    if not export["entities"]:
        sys.exit(f"ERROR: graph for collection '{args.collection}' is empty or "
                 "the graph store is unwired (set ODM_GRAPH_DB_*). Refusing to "
                 "write an empty report.")

    chunk_ids = _all_chunk_ids(ctx.store)
    report = compute_all(export, chunk_ids, golden)
    report["meta"] = {"collection": args.collection,
                      "entities": len(export["entities"]),
                      "edges": len(export["edges"])}

    cov, orp, con = report["coverage"], report["orphans"], report["connectivity"]
    dup, rec = report["duplication"], report["concept_recall"]
    print(f"Graph quality — collection '{args.collection}' "
          f"({report['meta']['entities']} entities, {report['meta']['edges']} edges)\n")
    print(f"extraction coverage      {cov['chunks_with_entities']}/{cov['chunks_total']}"
          f"  ({cov['coverage']:.0%})   uncovered: {len(cov['uncovered_chunk_ids'])}")
    print(f"orphan entities          {orp['orphans']}/{orp['entities_total']}"
          f"  ({orp['orphan_ratio']:.0%})")
    print(f"connected components     {con['components']}"
          f"   largest holds {con['largest_component_share']:.0%}"
          f"   singletons: {con['singleton_components']}")
    print(f"duplicate clusters       {dup['duplicate_clusters']}"
          f"   excess entities: {dup['excess_entities']}"
          f"  ({dup['duplication_ratio']:.0%})")
    if dup["clusters"]:
        shown = len(dup["clusters"])
        note = f" (top {shown} of {dup['duplicate_clusters']})" if dup["clusters_truncated"] else ""
        print(f"  suspect clusters{note}:")
        for names in dup["clusters"][:5]:
            print(f"    - {', '.join(names)}")
    if rec["recall"] is not None:
        print(f"core-concept recall      {rec['found']}/{rec['golden_total']}"
              f"  ({rec['recall']:.0%})")
        if rec["missing_concepts"]:
            print(f"  missing: {', '.join(rec['missing_concepts'])}")

    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nFull report -> {args.out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Document in `benchmarks/erpnext/README.md`**

Add a row to the "What's here" table:

```markdown
| `graph_quality.py` | Structural quality report for the collection's knowledge graph — coverage, orphans, connectivity, duplication, core-concept recall. Offline w.r.t. LLMs; needs MariaDB + the Chroma data dir. |
| `golden_concepts.json` | Hand-curated core concepts of the pinned corpus, for the recall metric. First pass — extend as the corpus grows. |
```

And a run example after the existing "Run it" block:

```markdown
# graph structural quality (no LLM; diff two reports to compare a change)
.venv/bin/python benchmarks/erpnext/graph_quality.py --collection erpnext
```

- [ ] **Step 4: Verify**

Run: `.venv/bin/python benchmarks/erpnext/graph_quality.py --help`
Expected: usage text, exit 0.

If the live env is available (MariaDB + ingested `erpnext` collection):
Run: `.venv/bin/python benchmarks/erpnext/graph_quality.py --collection erpnext`
Expected: the five-line summary and `graph-quality.report.json` written; running twice produces identical output (deterministic). If the env is not available, note that in the task report — do not claim it verified.

Also confirm the report file is git-ignored: `git check-ignore benchmarks/erpnext/graph-quality.report.json` should print the path (the `.gitignore` already covers `*.report.json`; if it does not, add `benchmarks/erpnext/*.report.json` to `.gitignore` in this task).

- [ ] **Step 5: Commit**

```bash
git add benchmarks/erpnext/golden_concepts.json benchmarks/erpnext/graph_quality.py benchmarks/erpnext/README.md
git commit -m "feat(benchmarks): graph structural quality report (graph_quality.py)"
```

---

### Task 5: `GraphExplorer` component (frontend)

Self-contained ego-network canvas + detail side panel. Given a root entity name it fetches `/api/graph/entity/{name}`, renders center + depth-1 neighbors, expands on node click, and shows evidence on selection. No changes to `Graph.tsx` yet (that's Task 6).

**Files:**
- Modify: `web/package.json` (add dependency)
- Modify: `web/src/api.ts` (one type addition)
- Create: `web/src/components/GraphExplorer.tsx`

**Interfaces:**
- Consumes: `api.graphEntity(name): Promise<GraphNeighbors>` (existing, `web/src/api.ts:607`); UI primitives `Badge`, `Card`, `Spinner`, `EmptyState`, `useToast` from `web/src/components/ui`.
- Produces: `export default function GraphExplorer({ rootName }: { rootName: string })` — Task 6 renders `<GraphExplorer rootName={selected} />`.

- [ ] **Step 1: Install the dependency**

Run in `web/`: `npm install react-force-graph-2d`
Expected: `package.json` gains `"react-force-graph-2d": "^1.x"`; ships its own TypeScript types.

- [ ] **Step 2: Add `edge_evidence` to the `GraphNeighbor` type**

In `web/src/api.ts` (the backend's `neighbors()` already returns this field; the type just never declared it):

```typescript
export interface GraphNeighbor {
  entity: GraphEntity;
  relation_type: string;
  direction: "in" | "out";
  edge_evidence?: EvidenceEntry[];
}
```

- [ ] **Step 3: Create the component**

```tsx
// web/src/components/GraphExplorer.tsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { api, EvidenceEntry, GraphEntity, GraphNeighbors } from "../api";
import { Badge, Card, EmptyState, Spinner, useToast } from "./ui";
import { IconGraph } from "./icons";

// Per-expansion neighbor cap. Truncation is always surfaced with a count
// (Fail Loud) — never silently dropped.
const MAX_NEIGHBORS = 50;

interface GNode {
  id: string; // normalized_name
  name: string;
  type: string;
  expanded: boolean;
  entity?: GraphEntity;
}

interface GLink {
  id: string; // `${src}|${dst}|${relation_type}`
  source: string;
  target: string;
  relation_type: string;
  edge_evidence?: EvidenceEntry[];
}

type Selection =
  | { kind: "node"; node: GNode }
  | { kind: "link"; link: GLink }
  | null;

/** Merge one /api/graph/entity response into the accumulated node/link maps.
 *  Returns the number of neighbors dropped by the MAX_NEIGHBORS cap. */
function mergeDetail(
  nodes: Map<string, GNode>,
  links: Map<string, GLink>,
  detail: GraphNeighbors,
  markExpanded: string | null,
): number {
  const root = detail.entity;
  if (!root) return 0;
  const existing = nodes.get(root.normalized_name);
  nodes.set(root.normalized_name, {
    id: root.normalized_name,
    name: root.name,
    type: root.type,
    expanded: existing?.expanded || markExpanded === root.normalized_name,
    entity: root,
  });
  const shown = detail.neighbors.slice(0, MAX_NEIGHBORS);
  for (const n of shown) {
    const id = n.entity.normalized_name;
    if (!nodes.has(id)) {
      nodes.set(id, {
        id,
        name: n.entity.name,
        type: n.entity.type,
        expanded: false,
        entity: n.entity,
      });
    }
    const [src, dst] =
      n.direction === "out" ? [root.normalized_name, id] : [id, root.normalized_name];
    const key = `${src}|${dst}|${n.relation_type}`;
    if (!links.has(key)) {
      links.set(key, {
        id: key,
        source: src,
        target: dst,
        relation_type: n.relation_type,
        edge_evidence: n.edge_evidence,
      });
    }
  }
  return detail.neighbors.length - shown.length;
}

export default function GraphExplorer({ rootName }: { rootName: string }) {
  const [nodes, setNodes] = useState<Map<string, GNode>>(new Map());
  const [links, setLinks] = useState<Map<string, GLink>>(new Map());
  const [selection, setSelection] = useState<Selection>(null);
  const [truncated, setTruncated] = useState<{ node: string; hidden: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const toast = useToast();

  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(600);
  useEffect(() => {
    if (!wrapRef.current) return;
    const el = wrapRef.current;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const expand = useCallback(
    async (name: string, isRoot: boolean) => {
      setLoading(true);
      try {
        const detail = await api.graphEntity(name);
        if (!detail.entity) {
          if (isRoot) setNotFound(true);
          return;
        }
        setNodes((prev) => {
          const next = new Map(prev);
          setLinks((prevLinks) => {
            const nextLinks = new Map(prevLinks);
            const hidden = mergeDetail(next, nextLinks, detail, detail.entity!.normalized_name);
            setTruncated(hidden > 0 ? { node: detail.entity!.name, hidden } : null);
            return nextLinks;
          });
          return next;
        });
        if (isRoot) {
          setSelection({
            kind: "node",
            node: {
              id: detail.entity.normalized_name,
              name: detail.entity.name,
              type: detail.entity.type,
              expanded: true,
              entity: detail.entity,
            },
          });
        }
      } catch (e) {
        if (isRoot && String(e).startsWith("404")) setNotFound(true);
        else toast.show(String(e), "red");
      } finally {
        setLoading(false);
      }
    },
    [toast],
  );

  // A new root resets the canvas and loads its ego network.
  useEffect(() => {
    setNodes(new Map());
    setLinks(new Map());
    setSelection(null);
    setTruncated(null);
    setNotFound(false);
    void expand(rootName, true);
  }, [rootName, expand]);

  // react-force-graph mutates node objects (positions); memo on map identity.
  const graphData = useMemo(
    () => ({ nodes: [...nodes.values()], links: [...links.values()] }),
    [nodes, links],
  );

  if (notFound) {
    return (
      <EmptyState
        icon={<IconGraph className="h-6 w-6" />}
        title="Entity not found"
        hint={`No graph record exists for "${rootName}".`}
      />
    );
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_20rem]">
      <Card className="relative overflow-hidden p-0" data-testid="graph-canvas-wrap">
        <div ref={wrapRef}>
          <ForceGraph2D
            width={width}
            height={480}
            graphData={graphData}
            nodeId="id"
            nodeLabel="name"
            nodeAutoColorBy="type"
            nodeVal={(n) => ((n as GNode).id === rootName ? 3 : 1)}
            linkLabel="relation_type"
            linkDirectionalArrowLength={4}
            linkDirectionalArrowRelPos={1}
            onNodeClick={(n) => {
              const node = n as unknown as GNode;
              setSelection({ kind: "node", node });
              if (!node.expanded) void expand(node.name, false);
            }}
            onLinkClick={(l) => setSelection({ kind: "link", link: l as unknown as GLink })}
          />
        </div>
        {loading && (
          <div className="absolute right-3 top-3">
            <Spinner className="h-4 w-4" />
          </div>
        )}
        {truncated && (
          <div
            className="absolute bottom-3 left-3 rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-700 dark:bg-amber-500/15 dark:text-amber-300"
            data-testid="graph-truncation-note"
          >
            Showing {MAX_NEIGHBORS} of {MAX_NEIGHBORS + truncated.hidden} neighbors for{" "}
            {truncated.node}
          </div>
        )}
      </Card>
      <SelectionPanel selection={selection} />
    </div>
  );
}

function SelectionPanel({ selection }: { selection: Selection }) {
  if (!selection) {
    return (
      <Card className="p-5 text-sm text-slate-400 dark:text-slate-500">
        Click a node to expand it, or an edge to inspect its evidence.
      </Card>
    );
  }
  if (selection.kind === "node") {
    const { node } = selection;
    return (
      <Card className="space-y-2 p-5" data-testid="graph-selection-panel">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-base font-semibold text-slate-900 dark:text-white">
            {node.name}
          </h3>
          <Badge tone="brand">{node.type}</Badge>
        </div>
        {node.entity?.chunk_ids && (
          <div className="text-xs text-slate-400">
            {node.entity.chunk_ids.length} source chunk
            {node.entity.chunk_ids.length === 1 ? "" : "s"}
          </div>
        )}
        <EvidenceList entries={node.entity?.evidence ?? []} />
      </Card>
    );
  }
  const { link } = selection;
  return (
    <Card className="space-y-2 p-5" data-testid="graph-selection-panel">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="amber">{link.relation_type}</Badge>
        <span className="font-mono text-xs text-slate-500">
          {link.source as string} → {link.target as string}
        </span>
      </div>
      <EvidenceList entries={link.edge_evidence ?? []} />
    </Card>
  );
}

function EvidenceList({ entries }: { entries: EvidenceEntry[] }) {
  if (entries.length === 0) {
    return <div className="text-xs text-slate-400">No evidence recorded.</div>;
  }
  return (
    <div className="space-y-2 pt-1">
      <div className="text-xs uppercase tracking-wide text-slate-400">
        Evidence ({entries.length})
      </div>
      {entries.map((entry, i) => (
        <div key={i} className="rounded-md border border-slate-100 p-2 dark:border-slate-800">
          {entry.verified === false && (
            <div className="mb-1">
              <Badge tone="red">unverified</Badge>
            </div>
          )}
          <code className="block whitespace-pre-wrap break-all font-mono text-xs text-slate-700 dark:text-slate-300">
            {entry.quote}
          </code>
          <div className="mt-0.5 font-mono text-xs text-slate-400">
            {entry.start_line != null && entry.end_line != null
              ? `${entry.source ?? ""}:${entry.start_line}-${entry.end_line}`
              : (entry.source ?? "")}
          </div>
        </div>
      ))}
    </div>
  );
}
```

Note on force-graph link objects: after the simulation starts, `link.source`/`link.target` become node object references, not strings. The panel casts to string for display; if it renders `[object Object]`, replace with `typeof link.source === "object" ? (link.source as GNode).id : link.source` — implementer should check this in the browser and fix accordingly (keep the fix, it's a known library behavior).

- [ ] **Step 4: Verify it compiles**

Run in `web/`: `npm run build`
Expected: build succeeds (tsc + vite), output to `../src/opendomainmcp/api/static/`. The component is not yet reachable from any page — that's fine; this step only proves types and imports.

- [ ] **Step 5: Commit**

```bash
git add web/package.json web/package-lock.json web/src/api.ts web/src/components/GraphExplorer.tsx
git commit -m "feat(web): GraphExplorer ego-network component (react-force-graph-2d)"
```

---

### Task 6: Wire the List / Graph toggle into `Graph.tsx`

Entities mode gets a view toggle. List view is byte-for-byte the current behavior; Graph view swaps the right-hand detail area for `<GraphExplorer />`. The left entity search list stays in both views.

**Files:**
- Modify: `web/src/pages/Graph.tsx` (EntitiesMode, ~line 110)

**Interfaces:**
- Consumes: `GraphExplorer` from Task 5 (`../components/GraphExplorer`, default export, prop `rootName: string`).

- [ ] **Step 1: Add the view state and toggle**

In `EntitiesMode` (currently starting at `web/src/pages/Graph.tsx:110`):

1. Add the import at the top of the file:

```tsx
import GraphExplorer from "../components/GraphExplorer";
```

2. Add view state next to the existing state hooks:

```tsx
type EntitiesView = "list" | "graph";
// inside EntitiesMode:
const [view, setView] = useState<EntitiesView>("list");
```

3. In list view, `selectEntity` currently fetches the detail. In graph view the explorer fetches for itself — skip the duplicate request:

```tsx
async function selectEntity(name: string) {
  setSelected(name);
  if (view === "graph") return; // GraphExplorer fetches its own data
  setDetail(null);
  setNotFound(false);
  setDetailLoading(true);
  try {
    setDetail(await api.graphEntity(name));
  } catch (e) {
    if (isNotFound(e)) setNotFound(true);
    else toast.show(String(e), "red");
  } finally {
    setDetailLoading(false);
  }
}
```

When switching view back to list with an entity selected, refetch so the list detail is populated:

```tsx
function switchView(v: EntitiesView) {
  setView(v);
  if (v === "list" && selected) void selectEntity(selected);
}
```

(Note: `selectEntity` reads `view` from the closure — stale-state hazard. Implement `switchView` by inlining the fetch or passing the target view: `if (v === "list" && selected) { setView(v); refetchDetail(selected); }` — the implementer should extract the fetch body into `refetchDetail(name)` called by both paths, which avoids the closure issue entirely.)

4. Add the toggle above the search input, styled like the existing `ModeTabs` (reuse `TabButton`):

```tsx
<div
  className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-1 dark:border-slate-700 dark:bg-slate-800/70"
  data-testid="graph-view-toggle"
>
  <TabButton active={view === "list"} onClick={() => switchView("list")}>
    List
  </TabButton>
  <TabButton active={view === "graph"} onClick={() => switchView("graph")}>
    Graph
  </TabButton>
</div>
```

5. Replace the right-hand `<EntityDetail ... />` with:

```tsx
{view === "list" ? (
  <EntityDetail
    selected={selected}
    detail={detail}
    loading={detailLoading}
    notFound={notFound}
  />
) : selected ? (
  <GraphExplorer rootName={selected} />
) : (
  <EmptyState
    icon={<IconGraph className="h-6 w-6" />}
    title="Select an entity"
    hint="Pick an entity on the left to see its ego network."
  />
)}
```

- [ ] **Step 2: Build**

Run in `web/`: `npm run build`
Expected: success.

- [ ] **Step 3: Manually smoke it (if a live backend with graph data is available)**

Run from repo root: `./run.sh web`, open `http://127.0.0.1:8000/#/graph`, switch to Graph, select an entity: canvas renders the ego network; clicking a neighbor expands it; clicking an edge shows relation + evidence in the panel. If no live graph data is available, note that and rely on Task 7's mocked e2e.

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/Graph.tsx
git commit -m "feat(web): List/Graph view toggle on the Knowledge Graph page"
```

---

### Task 7: Playwright e2e for the Graph view

Extend the existing self-contained spec (route mocks — no live backend). Canvas internals aren't DOM-assertable, so the test covers: toggle renders, canvas mounts, selection panel shows the root, truncation note appears when neighbors exceed the cap.

**Files:**
- Modify: `web/tests/graph.spec.ts` (append a describe block)

**Interfaces:**
- Consumes: `installApiMocks(page, overrides)` from `web/tests/helpers/mockApi.ts`; `data-testid` hooks from Tasks 5–6: `graph-view-toggle`, `graph-canvas-wrap`, `graph-selection-panel`, `graph-truncation-note`.

- [ ] **Step 1: Append the failing test**

```typescript
// Append to web/tests/graph.spec.ts

const MANY_NEIGHBORS = {
  entity: {
    name: "Deployment",
    normalized_name: "deployment",
    type: "Process",
    chunk_ids: ["chunk-1"],
  },
  neighbors: Array.from({ length: 60 }, (_, i) => ({
    entity: {
      name: `Service ${i}`,
      normalized_name: `service-${i}`,
      type: "Service",
    },
    relation_type: "depends_on",
    direction: "out",
  })),
};

test.describe("graph view (ego network)", () => {
  test("renders the canvas, root panel, and truncation note", async ({ page }) => {
    await installApiMocks(page, {
      "GET /api/graph/entities": ENTITIES,
      "GET /api/graph/entity/*": MANY_NEIGHBORS,
    });
    await page.goto("/#/graph");

    await page.getByTestId("graph-view-toggle").getByText("Graph").click();
    await page.getByText("Deployment", { exact: true }).click();

    await expect(page.getByTestId("graph-canvas-wrap").locator("canvas")).toBeVisible();
    await expect(page.getByTestId("graph-selection-panel")).toContainText("Deployment");
    await expect(page.getByTestId("graph-truncation-note")).toContainText(
      "Showing 50 of 60 neighbors",
    );
  });
});
```

Note: `ENTITIES` is already defined at the top of this spec file. If `getByText("Deployment", { exact: true })` is ambiguous (it may also match the panel), scope it to the entity list button: `page.getByRole("button", { name: /Deployment/ }).first()`.

- [ ] **Step 2: Run the spec**

Run in `web/`: `npx playwright test tests/graph.spec.ts`
Expected: the pre-existing "graph" describe still passes; the new test passes. (If Playwright browsers are missing: `npx playwright install chromium` first.)

- [ ] **Step 3: Run the full e2e suite for regressions**

Run in `web/`: `npm run test:e2e`
Expected: all specs pass.

- [ ] **Step 4: Commit**

```bash
git add web/tests/graph.spec.ts
git commit -m "test(web): e2e for the Graph ego-network view"
```

---

## Self-Review Notes

- **Spec coverage:** export_graph → Task 1; five metrics + pure functions + union-find + no NetworkX → Tasks 2–3; runner + golden list + diffable report → Task 4; ego-network view, expansion, evidence panel, react-force-graph-2d, truncation with count → Tasks 5–6; offline unit tests / integration-marked DB test / mocked e2e → Tasks 1, 2, 3, 7. Spec's two stale sentences (list_edges naming, "seeded dev backend") corrected in Task 1 Step 7.
- **Acceptance mapping:** deterministic JSON report → Task 4 Step 4; offline pytest → Tasks 1–3; search → ego network → expand → edge evidence → Tasks 5–7 (deep interaction verified manually in Task 6 Step 3; DOM-assertable parts in Task 7).
- **Type consistency:** entity/edge/entity_chunk dict keys match `export_graph()` (Task 1) everywhere; `GraphExplorer` prop is `rootName` in both Task 5 and Task 6; testids match between Tasks 5–6 and Task 7.
