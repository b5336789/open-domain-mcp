# Rule Consensus & Cross-Validation — Design Spec

**Date:** 2026-07-06
**Sub-project:** #5 of the enhancement series (order: #1 → #4 → #2 → #5 → #3)
**Status:** Approved in brainstorming; awaiting implementation plan
**Depends on:** codegraph spec (graph signals), evidence spec (per-source evidence)

## Problem

In a large codebase the same business rule appears in multiple places (e.g. "order
amount must not be negative" in the billing service *and* as a DB constraint).
Today each extraction is independent: duplicates clutter retrieval, corroboration is
never detected, and contradictions go unnoticed. Multiple independent sightings of
one rule should *raise* its trust; similar-but-contradictory rules should be flagged.

## Design

New module `consensus/`, run as a batch pass after ingest completes; also manually
re-runnable from CLI subcommand and CommandCenter.

### Stage 1 — Candidate pairing (deterministic + embedding, zero LLM)

All-pairs comparison is too expensive. A rule pair becomes a candidate when:
- embedding cosine similarity exceeds a threshold, **or**
- graph signals connect their origins: shared graph entity, membership in the same
  call chain, or an `executes_sql`/`http_call` edge between their source functions
  (this is exactly the billing-service → DB-constraint case).

### Stage 2 — LLM adjudication (candidates only)

Verdict per pair: `same` (one rule) / `related` (related but distinct) /
`conflict` (similar wording, contradictory semantics — e.g. `>= 0` vs `> 0`).
Verdicts are cached by pair content hash: re-runs skip already-judged pairs;
re-ingesting a file invalidates only pairings involving that file's rules
(incremental by construction).

### Stage 3 — Merge & trust tiers

- `same` groups merge into one **canonical rule** (`kind='rule'`, representative
  statement), carrying the full corroboration list; each corroboration keeps its own
  line-level Evidence. Original rules are retained and back-linked — a bad merge is
  always reversible.
- **Trust tiers** derived from source distribution:
  - `high` — corroborated across layers (e.g. Java service + PL/SQL constraint, or
    code + documentation);
  - `normal` — single source;
  - `conflicted` — at least one `conflict` verdict in the group; automatically
    enqueued into the review queue with top priority.
- New graph relation types: `corroborates`, `conflicts`.

## Retrieval

Search/ask prefer canonical rules over their members (no more duplicate-rule spam);
trust level is emitted in citations and MCP payloads.

## Testing

Injected fake embedder + fake adjudicator. Scenarios:
- cross-layer corroboration → canonical rule with `high` trust and both evidences;
- `>= 0` vs `> 0` style conflict → `conflicted`, lands in review queue;
- idempotent re-run (verdict cache hits, no duplicate canonicals);
- incremental: re-ingest of one file re-judges only affected pairs.

## Out of scope

- Automatic conflict *resolution* (humans decide; we only detect and route).
- Cross-collection consensus.
- Human confirmation before merge (merges are reversible; review queue covers risk).
