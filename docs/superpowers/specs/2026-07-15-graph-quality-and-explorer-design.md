# Graph Quality Metrics + Graph Explorer — Design Spec

**Date:** 2026-07-15
**Status:** Approved in brainstorming; awaiting implementation plan
**Origin:** Started as "should we adopt Neo4j?" — resolved as: no backend change;
invest in measuring graph quality and seeing the graph instead.

## Problem

The project's purpose is extracting domain knowledge from code and product specs
so that **humans can understand it, AI agents can consume it, with no errors and
no omissions**. The knowledge graph (MariaDB via `GraphStoreProtocol`) is built
entirely from LLM extraction, so extraction defects become graph defects:

- **Holes** — ~14% of chunks fail extraction (malformed JSON from the local
  model), so their entities/relations are simply absent.
- **Fragmentation** — inconsistent entity naming (`Sales Order` / `sales_order`)
  splits one concept into several nodes; `neighbors()` then misses links that
  should exist.
- **Wrong edges** — hallucinated relations poison graph-augmented retrieval.

Today there is no way to *measure* any of this, and the existing `Graph.tsx`
page is list-only — no visual rendering, so a human cannot inspect the graph's
shape or spot-check edges against their evidence.

### Why not Neo4j (decision record)

Evaluated and rejected for now:
- All current queries are depth ≤ 2 neighbor expansions; nothing exercises
  Cypher's strengths (deep/variable-length traversal, graph algorithms).
- Graph scale is small (10³–10⁴ entities per project) — fits in memory; any
  future analytics can run in-process, no graph engine needed.
- Visualization does not require Neo4j Browser; the SPA can render the graph
  and (unlike Neo4j Browser) link edges back to chunks/evidence.
- None of the four project goals (human-readable, agent-readable, no errors,
  no omissions) is advanced by a backend swap — they all live in the
  extraction/verification layer.
- The graph is **derived data** (source of truth = chunks + extraction), and
  `GraphStoreProtocol` is an injection seam. Migrating later = write one
  adapter + re-ingest. Deferring costs almost nothing.

## Scope

Two independent workstreams, both this round. Explicitly **out of scope** (next
iterations, driven by what the metrics reveal):
- Failed-chunk re-extraction loop (closes "no omissions")
- Edge-level review/approval workflow (closes "no errors")
- Full-graph overview mode, LLM-judged relation precision, Neo4j adapter,
  NetworkX dependency

## Workstream 1 — Graph structural quality metrics

### Metrics (all offline, zero LLM)

| Metric | Method | Defect exposed |
| --- | --- | --- |
| Extraction coverage | fraction of chunks with ≥1 entity | holes from failed extraction |
| Orphan-node ratio | entities with no edges | entity-only extractions |
| Connectivity | connected-component count, largest-component share, size distribution | fragmentation (macro) |
| Entity duplication | near-name clustering (token similarity); rate + top-20 suspect clusters | one concept, many nodes |
| Core-concept recall | presence check against a hand-curated golden list | omissions of known concepts |

### Components

- **`src/opendomainmcp/evals/graph_metrics.py`** — pure functions taking
  entity/edge lists and returning metric dicts. Unit-tested offline with
  hand-built miniature graphs. Connected components via a ~20-line stdlib
  union-find (no NetworkX).
- **`benchmarks/erpnext/graph_quality.py`** — thin runner in the mold of
  `run_benchmark.py`: wires live store + graph via `build_context()`, computes
  metrics for a `--collection`, writes `graph-quality.report.json` + prints a
  summary. Diff two reports to compare before/after a change.
- **`benchmarks/erpnext/golden_concepts.json`** — 20–30 hand-curated core
  concepts of the pinned ERPNext tax/pricing corpus, for the recall metric.
- **Protocol extension** — `GraphStoreProtocol.export_graph()` — one bulk read
  returning `{entities, edges, entity_chunks}` for the collection (coverage and
  duplication metrics need entities and the entity↔chunk map, not just edges).
  `MariaGraphStore`: three SELECTs scoped to the collection. `NullGraphStore`:
  returns the same shape with empty lists. This is the only backend change.
- Extraction coverage needs the chunk-id universe: fetched from `ChromaStore`
  (chunk ids per collection), compared against `entity_chunks`.

Relation *correctness* is deliberately not auto-judged; the explorer
(Workstream 2) makes human spot-checking of edge evidence a one-click action.

## Workstream 2 — Graph Explorer (upgrade `web/src/pages/Graph.tsx`)

Entities mode gains a **List / Graph view toggle**. The existing list view is
kept unchanged; the new Graph view renders an **ego-network with progressive
expansion**:

- Search + select an entity → fetch `/api/graph/entity/{name}` (existing
  endpoint; already returns `relation_type`, `direction`, `edge_evidence`) →
  render center node + depth-1 neighbors as a force-directed graph.
  **No backend changes.**
- Click a node → fetch its neighbors, merge into the canvas (dedupe existing
  nodes). Nodes colored by entity `type`.
- Click a node or edge → side panel with details: entity type/chunks, relation
  type, evidence entries with links back to source chunks (reusing the data the
  existing detail panel already consumes).
- New npm dependency: `react-force-graph-2d` (canvas renderer) — the SPA's
  first functional dependency beyond react/react-dom/react-router; accepted to
  avoid hand-rolling a d3-force simulation.

### Error handling

- Empty graph / missing entity → existing empty-state components.
- Neighbor sets larger than 50 per expansion are truncated **with a visible
  count badge** ("showing 50 of 173") — never silently dropped (Fail Loud).
- Expansion of an already-expanded node is a no-op.

## Testing

- Metric functions: pytest unit tests with fabricated small graphs asserting
  each metric's value (fully offline, per repo convention).
- `export_graph`: covered in the existing `integration`-marked MariaDB tests.
- Explorer: one Playwright e2e smoke — load page, switch to Graph view, search,
  expand a node (self-contained route mocks via `tests/helpers/mockApi.ts`, like
  the rest of the e2e suite — no live backend).

## Acceptance

- `graph_quality.py --collection erpnext` produces a JSON report with all five
  metrics and a printed summary; running it twice is deterministic.
- Unit tests pass offline (`pytest` with no network/DB).
- In the SPA, a user can search an entity, see its ego network, expand two
  levels, click an edge, and read its evidence with a link to the source chunk.
