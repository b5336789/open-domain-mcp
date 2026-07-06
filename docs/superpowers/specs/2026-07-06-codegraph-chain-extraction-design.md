# Code Graph + Call-Chain Extraction (Graph-RAG) — Design Spec

**Date:** 2026-07-06
**Sub-project:** #4 of the enhancement series (order: #1 → #4 → #2 → #5 → #3)
**Status:** Approved in brainstorming; awaiting implementation plan

## Problem

Extraction today analyzes each chunk in isolation, so cross-function business logic
(e.g. a billing rule spanning a Java service and the Oracle stored procedure it
calls) is invisible. The existing graph holds only LLM-extracted relations and
module-level imports (`graph/deps.py`) — no function-level call graph, no call
chains, and the graph never feeds context back into extraction.

## Target corpus

Enterprise legacy stack: **Java**, **VB.NET**, **Oracle PL/SQL** stored procedures,
and **JS/TS** frontend. Other languages keep the existing per-chunk path.

## Goal

Build a function-level, cross-language call graph via deterministic static analysis;
assemble entry-point-rooted call chains; analyze chains with the LLM bottom-up; store
chain-level knowledge as retrievable items. Vector/hybrid search is not removed —
what it searches over becomes chain-contextualized analysis instead of isolated-chunk
guesses.

## Design

New subsystem `codegraph/` with six stages.

### 1. Symbol extraction layer (per-language plugins, injected/swappable)

Each plugin produces:
- `FunctionDef { qualified_name, file, start_line, end_line, signature, language }`
- `CallSite { caller_qualified_name, callee_name, file, line, kind }`

Parsers:
- **Java, JS/TS:** tree-sitter (grammars already shipped).
- **VB.NET:** hand-written lightweight parser (`Sub/Function … End Sub/Function`,
  `Class/Module/Namespace` scoping; the grammar is line-oriented and regular).
- **PL/SQL:** hand-written lightweight parser (`CREATE [OR REPLACE] PACKAGE [BODY]`,
  `PROCEDURE`/`FUNCTION` declarations, call statements).

The plugin interface is a seam: any language can later be swapped to a
higher-precision external tool without touching downstream stages. Pure Python,
offline-testable, no external runtimes.

### 2. Call resolution

Name matching with scope precedence: same class → same package/module → imports →
globally unique name. Unresolvable calls (interfaces, dynamic dispatch, reflection)
are kept as low-confidence edges, never dropped. Every edge carries a resolution
confidence.

**Cross-language edges:**
- `executes_sql`: detect SQL call strings in Java/VB.NET
  (`{call PKG.PROC}`, `CallableStatement`, `CommandText = "…"`, etc.) and link the
  enclosing function to the PL/SQL procedure entity.
- `http_call`: match JS/TS request sites (fetch/axios URL literals and templates)
  against backend route declarations (`@RequestMapping`/`@GetMapping`/… and
  equivalents), tolerating path parameters. Lower expected precision; edges carry
  correspondingly lower confidence.

### 3. Graph storage

Persist into the existing MariaDB graph store with new entity types (`function`,
`procedure`, `endpoint`) and new relation types (`calls`, `executes_sql`,
`http_call`). Every node and edge carries `file`, `start_line`, `end_line`
provenance (foundation for the Traceability spec).

### 4. Chain assembly

Entry-point detection: REST endpoints, UI event handlers, public API methods with no
internal callers, top-level stored procedures. From each entry point, walk call
edges to the leaves (crossing language boundaries via the edges above). Cycles are
detected and truncated at the back-edge.

### 5. Chain analysis (LLM, bottom-up)

Analyze leaf functions first (typically PL/SQL), producing per-function summaries and
rules. Analyzing a higher function includes: full source of the function and its
1-hop callees, summaries only for deeper callees. Token cost is bounded regardless of
chain depth; every intermediate summary is reusable across chains sharing subtrees.

Outputs:
- **Per-function summary** → written back as the chunk's enrichment (replacing the
  old isolated per-chunk extraction for code).
- **Chain-level KnowledgeUnit** — end-to-end workflow, business rules, constraints —
  stored as a retrievable item with `kind='chain'`; metadata records the entry
  point and all member functions' chunk ids + line ranges.

### 6. Coverage fallback (Fail Loud)

Functions not covered by any chain fall back to the legacy per-chunk extraction.
The ingest report includes chain coverage statistics (functions covered / total,
list of fallback files). Documents (PDF/wiki/markdown) keep the existing pipeline
untouched.

## Retrieval

Hybrid search unchanged; `kind='chain'` items are naturally retrievable.
`retrieve_include_graph` keeps working and upgrades to function-level neighbors.

## Testing

Offline fixture corpus: a few small files per language (Java service calling a
PL/SQL package, VB.NET module, JS frontend hitting a Java route). Assert symbol
extraction, resolution precedence, cross-language edge detection, chain assembly,
cycle truncation, and coverage-fallback reporting. LLM is always an injected fake;
bottom-up ordering is asserted from the fake's call log.

## Out of scope

- External precise toolchains (Roslyn, JavaParser, ANTLR) — the plugin seam allows
  them later.
- Languages beyond Java / VB.NET / PL/SQL / JS/TS.
- Type-level resolution of dynamic dispatch.
