# Graph-Augmented Retrieval (`ask`)

## Problem

The `ask` RAG path retrieves chunks (dense + BM25 + RRF) and synthesizes an
answer from them. Questions whose answer is a **relationship** or a **sequence**
— not text inside one chunk — are poorly served, because the answer lives in the
knowledge graph, which `ask` ignores entirely.

The ERPNext benchmark (`benchmarks/erpnext`) makes this concrete. Of its 5
persistent retrieval misses, the relational/sequence ones are unreachable by
chunk retrieval even with reranking (measured net-zero):

- `gr2` — "after computing taxes, which function does `calculate_taxes` call?"
  Answer is an edge: `calculate_taxes —calls→ adjust_grand_total_for_inclusive_tax`.
- `wf1` — "order of the main steps when calculating taxes and totals." Answer is
  the call sequence inside `_calculate`.

The graph already stores these as typed edges; nothing surfaces them to `ask`.

## Goal

When a question references a graph entity (or matches a workflow), inject that
entity's neighborhood / the workflow's ordered steps into the `ask` context as
**one extra cited source**, so relation and sequence questions are answerable.

## Non-Goals

- No LLM-based entity extraction (deterministic matching only).
- No change to `search`, MCP views, or the advisor.
- No graph *writes* or schema changes — read-only use of the existing store.
- Not trying to force every benchmark miss to green; `wf1`/`ex1` depend on graph
  quality and are measured, not promised.

## Approach (chosen)

Deterministic entity detection + a compact relations block appended as a single
synthetic `SearchResult` after the relevance floor. Off by default behind a
setting; `NullGraphStore` makes it a no-op.

## Components

### 1. `query/graph_context.py` — new, pure (no LLM, no network beyond the graph store)

`build_graph_context(graph, query, chunk_results, settings) -> SearchResult | None`

1. **Seed entity names** deterministically:
   - Extract candidate identifiers from `query`: `snake_case`, `CamelCase`, and
     backtick/quote-delimited tokens (regex), plus whitespace tokens of length ≥ 4
     excluding a small stopword set (common question words like "function",
     "value", "which", "does").
   - Confirm each candidate with `graph.get_entity(name)`; keep confirmed ones.
     Confirmation + the entity cap bound any spurious matches, so the stopword
     set only needs to cover obvious noise, not be exhaustive.
   - Also seed from `entities` in the metadata of the top ~3 `chunk_results`
     when that key is present (best-effort; skipped if absent).
   - De-duplicate, cap at `_MAX_ENTITIES = 3`.
2. **Edges:** for each seed, `graph.neighbors(name)` → format up to `_MAX_EDGES = 8`
   lines as `src —relation_type→ dst` (both directions the store returns).
3. **Workflow:** `graph.list_workflows(q=query)`; for the first match,
   `graph.get_workflow(name)` → append `prerequisites` and ordered `steps`.
4. If no edges and no workflow → return `None`. Otherwise return one
   `SearchResult(id="graph:<hash>", text=<block>, score=0.0,
   metadata={"kind": "graph", "title": "Knowledge graph: <seeds>"})`,
   with the block token-capped (`_MAX_CHARS = 1500`).

The function never raises outward: any graph error is caught and yields `None`
(graph augmentation is additive; a graph failure must not break `ask`). The
failure is logged.

### 2. `query/rag.py` — hook in both `answer_question` and `answer_question_stream`

- Both gain an optional `graph=None` parameter.
- After the existing floor check (`results = _apply_relevance_floor(...)`;
  `if not results: refuse`), and only when answering:
  ```python
  if settings.retrieve_include_graph and graph is not None:
      gc = build_graph_context(graph, query, results, settings)
      if gc is not None:
          results = results + [gc]
  ```
- `_source_label` gains a `kind == "graph"` branch returning `metadata["title"]`
  (mirrors the existing `kind == "article"` branch).
- `_citations` currently branches only on `is_article`; add a parallel
  `kind == "graph"` branch → `{type: "graph", source: title, symbol: None}` so a
  graph source is labeled correctly rather than falling through to the `"chunk"`
  default.

### 3. Callers pass the graph

`cli.py`, `api/app.py`, and `server.py` already hold `ctx.graph`; pass it to
`answer_question` / `answer_question_stream`. With the flag off (default) or a
`NullGraphStore`, behavior is identical to today.

### 4. Settings (`config.py`)

`retrieve_include_graph: bool = False`, added to `EDITABLE_FIELDS`
(runtime-editable, `ODM_RETRIEVE_INCLUDE_GRAPH`). Documented in `.env.example`,
mirroring `retrieve_include_articles`.

## Data flow

```
ask(query)
  └─ search_unified → chunk hits
     └─ _apply_relevance_floor → refuse if best < floor   (graph never rescues a refusal)
        └─ [flag on & graph wired] build_graph_context(graph, query, hits)
           ├─ seed entities (question identifiers ∩ graph) + top-chunk entities
           ├─ neighbors(seed) → edge lines
           ├─ list_workflows(q)/get_workflow → ordered steps
           └─ → one SearchResult(kind="graph")  |  None
        └─ results (+ graph source) → _format_sources → LLM → answer + citations
```

## Error handling (Fail Loud where it matters)

- Graph augmentation is **additive and best-effort**: `build_graph_context`
  catches its own exceptions and returns `None` (logged). A graph outage must
  not degrade chunk-based answers — this is intentionally *not* fail-loud, since
  the answer is still grounded in chunks.
- The relevance floor and the "no content matched" refusal are unchanged: graph
  context is appended only when chunks already passed the floor, so out-of-corpus
  questions still refuse before any graph lookup.

## Testing (business-logic, offline)

`tests/test_graph_context.py` (fake graph implementing `get_entity`,
`neighbors`, `list_workflows`, `get_workflow`):
- Question naming an entity → block contains the `src —rel→ dst` lines.
- Workflow-shaped query → block contains ordered steps + prerequisites.
- No entity/workflow match → returns `None`.
- Identifier extraction picks up `snake_case`, `CamelCase`, and quoted tokens.
- Token cap (`_MAX_CHARS`) and entity/edge caps respected.
- Graph raising → returns `None` (no exception escapes).

`tests/test_rag.py`:
- `retrieve_include_graph=True` + fake graph → `ask` result includes a
  `citations` entry with `type == "graph"`; the block text reaches the model
  (assert via the injected `synthesize`'s captured `user`).
- `NullGraphStore` or flag off → citations identical to today (no graph source).
- Graph source is **not** added when the floor refuses (out-of-corpus question).

`benchmarks/erpnext`: re-run with `ODM_RETRIEVE_INCLUDE_GRAPH=true` vs off;
record gr2 (and any other) flips and check for regressions. Update the README
findings table with the measured before/after.

## Resolved decisions

- **Entity detection:** deterministic (question identifiers ∩ graph entities +
  top-chunk entities). No extra LLM call. (Chosen over LLM extraction for
  latency/cost/testability.)
- **Injection format:** one compact `kind="graph"` source listing edges +
  workflow steps, cited like any source. (Chosen over expanding entities into
  full chunk pseudo-sources to stay token-light and avoid duplicating chunks.)
- **Default:** off, behind `retrieve_include_graph`; `NullGraphStore` = no-op.
- **Ordering vs floor:** graph appended *after* the floor, so it cannot rescue an
  out-of-corpus refusal or enable fabrication.
