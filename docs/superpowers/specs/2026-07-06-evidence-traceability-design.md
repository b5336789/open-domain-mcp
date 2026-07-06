# Line-Level Evidence Traceability — Design Spec

**Date:** 2026-07-06
**Sub-project:** #2 of the enhancement series (order: #1 → #4 → #2 → #5 → #3)
**Status:** Approved in brainstorming; awaiting implementation plan
**Depends on:** codegraph spec (nodes/edges already carry file:line provenance)

## Problem

Chunks carry `source` + `start_line`/`end_line`, and graph entities link back to
chunk ids — but an individual extracted *rule* has no evidence of its own. Nothing
proves a rule actually came from the lines it claims, and LLM-reported line numbers
can be hallucinated. The requirement: every extracted rule and conclusion must carry
iron-clad, line-precise evidence.

## Design

### Evidence model

```
Evidence { source, start_line, end_line, quote, verified: bool }
```

Attached to:
- every concept/relation/rule in a chunk-level `KnowledgeUnit`;
- every conclusion in a chain-level KnowledgeUnit;
- every graph entity and edge (extends the codegraph provenance with quote +
  verification status).

### Extraction contract

Both chunk and chain extraction prompts require an evidence array (quote + line
range) per produced rule. Rules arriving without evidence are stored but immediately
marked `evidence_status='unverified'`.

### Deterministic verifier (zero LLM)

Checks that the quote actually appears in the claimed file at the claimed lines.
Three-stage match, tolerant of local-model transcription drift:
1. exact match at the claimed line range;
2. whitespace-normalized match;
3. nearby line-window search — on a hit, the line numbers are auto-corrected to the
   found location and the evidence is marked verified.

On failure: `evidence_status='unverified'` and a confidence penalty. Never dropped —
unverified rules are surfaced for human review with priority (see review spec).
Ingest report includes verified/unverified counts (Fail Loud).

### Storage

- Chroma metadata is flat scalars → evidence serialized as a JSON-string field.
- MariaDB graph: evidence column(s) on entities and edges.
- Entity merge keeps full lineage: the existing `entity_chunks` mapping is extended
  so merging never discards source attributions.

### Surfacing (all four)

1. **Review page** — each rule expands its quotes + `file:line`; unverified items
   sort first.
2. **Ask/RAG citations** — `[n]` references resolve to `file:line` range + quote,
   not just a file.
3. **MCP tools** — search/advisor/graph payloads gain an `evidence` field so
   downstream agents can cite hard evidence.
4. **Graph browser** — clicking an entity/edge shows source `file:line` + quote.

## Testing

Verifier business tests: exact hit, whitespace drift, hallucinated line number
(quote exists elsewhere → auto-correct), quote nowhere in file (→ unverified),
multi-line quotes. End-to-end with a fake extractor emitting evidence: assert
evidence survives to all four surfaces.

## Out of scope

- Retroactive evidence backfill for previously ingested corpora (re-ingest instead).
- Evidence for non-extracted content (raw chunks already have file:line).
