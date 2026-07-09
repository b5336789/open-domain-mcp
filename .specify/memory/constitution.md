<!--
Sync Impact Report
==================
Version change: (template) → 1.0.0 (initial ratification)
Modified principles: n/a (all five principles newly defined)
Added sections:
  - Core Principles (I–V)
  - Technology & Configuration Constraints
  - Development Workflow & Quality Gates
  - Governance
Removed sections: none (template placeholders replaced)
Templates requiring updates:
  - .specify/templates/plan-template.md ✅ no change needed (Constitution Check
    gates are derived from this file at plan time)
  - .specify/templates/spec-template.md ✅ no change needed (generic, no
    constitution-specific references)
  - .specify/templates/tasks-template.md ✅ no change needed (task phases are
    generic; test tasks remain optional per spec, consistent with Principle V)
  - .specify/templates/checklist-template.md ✅ no change needed
Follow-up TODOs: none
-->

# open-domain-mcp Constitution

## Core Principles

### I. Simplicity First, Surgical Changes

Write the minimum code required to solve the immediate problem. Speculative
features, premature abstractions, and configuration for hypothetical futures
MUST NOT be added. Changes MUST touch only code directly relevant to the task:
no drive-by cleanup of adjacent code, styling, or comments. New code MUST match
established codebase conventions (naming, structure, idiom) even where the
author disagrees with them.

**Rationale**: The codebase stays reviewable and its architecture map
(CLAUDE.md Part 4) stays truthful only if every change is small, local, and
idiomatic.

### II. One Path Through the System

Every surface — CLI (`cli.py`), MCP server (`server.py`), web API
(`api/app.py`) — MUST remain a thin adapter over `context.py:build_context()`.
There is exactly one ingestion path and one retrieval path; behavioral changes
MUST be made in the shared pipeline/store/context layer, never re-implemented
per surface. New surfaces MUST be built by calling `build_context()` and
driving `ctx.pipeline` / `ctx.store`. New MCP views/tools MUST be declared as
data in `views.VIEWS`, not as bespoke functions.

**Rationale**: A single source of truth is what guarantees the CLI, MCP, and
web UI never drift apart in behavior, and is the platform's core extension
seam.

### III. Injected Dependencies, Offline Tests

External capabilities (embedder, extractor, reranker, graph store) MUST be
injected behind small interfaces, with a Null/fake implementation available.
The default `pytest` suite MUST run fully offline — no network calls, no model
downloads, no live databases. Tests that require live services (e.g. MariaDB)
MUST be marked `integration` and excluded from the default run.

**Rationale**: Dependency injection is what lets the entire stack be exercised
in CI and on any laptop; one un-mocked network call breaks that guarantee for
everyone.

### IV. Fail Loud

Errors, skips, and partial results MUST be surfaced, never hidden. Skipped
files (binary/non-UTF-8), per-chunk extraction failures, and pruned/stale data
MUST appear in the ingestion report. Missing credentials or configuration MUST
produce an explicit error, not degraded silent behavior. Code MUST NOT swallow
exceptions to keep a pipeline "green"; uncertainty is reported to the caller.

**Rationale**: In a knowledge platform, silently dropped data poisons every
downstream answer; a loud failure at ingest time is far cheaper than a wrong
answer at query time.

### V. Grounded, Verified Outcomes

RAG answers MUST be synthesized strictly from retrieved, numbered sources with
inline `[n]` citations; when no content matches, the system MUST say so rather
than fabricate. Likewise for development work: success criteria MUST be
defined up front (typically a business-logic test that validates real intent,
not line coverage) and the work is not done until that verification passes.
Multi-step efforts MUST checkpoint at milestones: what was done, what was
verified, what remains.

**Rationale**: Both the product (grounded answers) and the process (verified
changes) share one rule — no claim without evidence.

## Technology & Configuration Constraints

- Backend is Python ≥ 3.11 in a `.venv`; the frontend is a Vite/React SPA in
  `web/` that builds into `src/opendomainmcp/api/static/`.
- All settings use the `ODM_` env prefix and load through `config.py` from
  env / `.env`. Runtime-editable settings persist to `<data_dir>/settings.json`
  layered over env; credentials and `data_dir` MUST NOT be runtime-editable.
- Credentials come only from standard provider vars (`ANTHROPIC_API_KEY`,
  `OPENAI_API_KEY`, `VOYAGE_API_KEY`, …) and MUST never be committed or logged.
- Chunk ids are content hashes; ingestion MUST remain idempotent, and
  re-ingestion MUST prune stale chunks rather than accumulate duplicates.
- Non-local ingest sources MUST be materialized under `<data_dir>/.sources/`
  and confined to the resolved `allowed_root`.
- Deterministic work (string formatting, mechanical transforms) belongs in
  plain code, not in LLM calls; LLM usage is reserved for extraction,
  synthesis, and other genuinely semantic steps.

## Development Workflow & Quality Gates

- `pytest` (the offline suite) MUST pass before merge; `integration`-marked
  tests run when a live MariaDB is configured.
- New features follow the spec-kit flow where used: spec → plan (with
  Constitution Check gate) → tasks → implement. Plans that violate a principle
  MUST justify the violation in the plan's Complexity Tracking table or be
  revised.
- Extension work uses the documented seams (new embedder, language, extractor,
  MCP view, surface — CLAUDE.md Part 6) instead of forking existing paths.
- Assumptions are stated before coding; trade-offs are discussed and
  clarifying questions asked rather than guessed at.
- Surrounding code and imports MUST be read before editing to ensure
  compatibility (no blind patches).

## Governance

This constitution supersedes other written practices for this repository where
they conflict; CLAUDE.md remains the operational companion (commands,
architecture map, extension seams) and MUST be kept consistent with it.

- **Amendments**: proposed as a PR that edits this file, states the rationale,
  and includes an updated Sync Impact Report. Dependent templates under
  `.specify/templates/` MUST be re-checked for alignment in the same change.
- **Versioning**: semantic. MAJOR for removed or redefined principles, MINOR
  for new principles or materially expanded guidance, PATCH for clarifications
  and wording fixes.
- **Compliance review**: every `/speckit-plan` run evaluates its Constitution
  Check gate against the current version of this file; code review verifies
  Principles I–V for changes made outside the spec-kit flow.

**Version**: 1.0.0 | **Ratified**: 2026-07-09 | **Last Amended**: 2026-07-09
