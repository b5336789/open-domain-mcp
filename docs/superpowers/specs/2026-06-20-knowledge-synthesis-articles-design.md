# Knowledge Synthesis — Business-Meaning Articles

**Date:** 2026-06-20
**Status:** Design approved (outline), pending spec review

## Problem

After ingest, the platform produces per-chunk `KnowledgeUnit` metadata
(summary / concepts / relations / knowledge_type / …). That structure exists to
**enrich retrieval** — it is folded into `embedding_text()` so search matches on
meaning. It is deliberately fragmented index-labelling, not human-readable output.

The user's actual goal is different: **automatically surface the
business-meaningful knowledge buried in a legacy system (code + docs) as readable,
conversational articles**, which should *also* improve retrieval. The hard part is
not formatting — it is **judging what counts as "business meaning"** versus
incidental implementation detail. There is no clean algorithm for that judgement;
it must be defined operationally and calibrated empirically against the user's real
data.

The current pipeline has **no stage that synthesizes fragments into articles**. It
stops at "one small label per chunk."

## Goals

- A new, independent post-ingest **synthesis stage** that groups related chunks by
  topic and writes one conversational article per business-meaningful topic.
- Articles are **human-readable** (browsable) **and retrievable** (stored so search
  / `ask` can use and cite them).
- A **thin-slice-first** workflow: produce 5–10 articles, let the user eyeball them,
  then calibrate the business-relevance filter — not a one-shot full-system run.

## Non-Goals

- No change to the existing ingest pipeline or per-chunk `KnowledgeUnit` extraction.
- **Option C is out of scope:** no separate "LLM business-analyst" pass that reads
  the whole corpus to invent a topic list (too expensive / unverifiable at legacy
  scale).
- No UI work in this slice beyond what is needed to view articles; deeper RAG
  integration (preferring articles in `ask`) is a later phase.

## Approach (chosen: "A skeleton + B booster")

Reuse existing extraction signals as the skeleton (A) and use **code↔doc
cross-validation as the primary business-meaning signal** (B). The legacy system
having *both* code and docs is the key asset: a concept that appears in **both** a
code chunk and a doc chunk is almost certainly a real domain concept; one that
appears only in code (never mentioned in any doc) is usually implementation detail.
The gap between "what the docs say" and "what the code actually does" is itself
high-value business knowledge.

## Pipeline (new `synthesis/` module, driven by `build_context()`)

Six stages, run by a new `synthesize` command. Each stage is independently
understandable and testable.

### 1. Gather — candidate topics
Read **already-stored** chunk metadata from Chroma (`entities` / `concepts` are
already there — no re-extraction). For each candidate topic, count how many chunks
mention it and record whether it appears in **code chunks**, **doc chunks**, or
both (the cross-validated flag).

### 2. Score + rank — filter to business-meaningful topics
Keep a topic if it appears in ≥ N chunks (N configurable) **and** has at least one
business signal:
- `knowledge_type` in the business set (Feature / Workflow / Permission /
  Constraint), **or**
- `audience` includes `product_manager` / `solutions_architect`, **or**
- **cross-validated** (present in both code and docs) — strongest signal.

Rank by signal strength (cross-validated weighted highest). **Thin slice: take the
top K (default 5–10).**

### 3. Collect — evidence per topic
For each surviving topic, use the existing hybrid search to pull its most relevant
chunks, partitioned into a **code-evidence** set and a **doc-evidence** set.

### 4. Synthesize — one article per topic (LLM, injected extractor-style client)
Fixed structure, conversational prose:
1. What this is / what it does (plain language).
2. **What the docs say vs. what the code actually does** — surface any gap.
3. Cited sources as `file:line`.

The LLM also returns a `title` and a `business_relevance` score (0–1). Per-topic
failures are recorded, never silently dropped (Fail Loud).

### 5. Filter — by `business_relevance`
Drop articles below a threshold (configurable). The threshold's initial value is a
guess to be **calibrated by the user reviewing the thin-slice output**.

### 6. Store — retrievable + browsable
Persist articles in a **separate Chroma collection `articles`** (does not pollute
the chunk index, but is independently searchable → satisfies "both readable and
retrievable"). Each article carries **provenance** (member chunk ids) so that
(a) retrieval can cite origins and (b) re-runs are **idempotent** via a content hash
of `topic + sorted(member chunk ids)`.

## Data shape

A new `Article` dataclass (plain, in `models.py` alongside `Chunk` /
`KnowledgeUnit`):

```
Article:
  id: str                  # hash(topic + sorted member chunk ids) — idempotent
  title: str
  topic: str               # the entity/concept this article is about
  body: str                # conversational markdown
  business_relevance: float
  source_chunk_ids: list[str]
  sources: list[str]       # "file:line" citations
  cross_validated: bool    # appeared in both code and docs
```

## Surfaces

- **CLI:** `./run.sh synthesize [--top-k K] [--min-relevance X] [--min-mentions N]`
  — runs the six stages, stores articles, prints a summary (title + relevance +
  source count per article).
- **Storage:** the `articles` Chroma collection, reachable from `build_context()`
  so search / a future UI page / `ask` can consume it.
- UI browse page: **deferred** to a follow-up (storage is built to support it now).

## Error handling (Fail Loud)

- No LLM API key → fail loud, do not fabricate articles.
- Per-topic synthesis failure → recorded in a report, other topics continue.
- Zero candidate topics survive scoring → explicit message ("no business-meaningful
  topics matched current thresholds"), not a silent empty run.

## Testing (business-logic, offline)

- **Gather/Score** with fake stored chunks: cross-validated topic ranks above a
  code-only topic; sub-threshold-mention topics are excluded.
- **Filter:** an article below `min_relevance` is dropped; one above is kept.
- **Idempotency:** same topic + same member chunk ids → same article id; re-run does
  not duplicate.
- **Fail Loud:** missing key raises; per-topic failure is reported, not swallowed.
- LLM client is **injected** (fake returning canned article JSON) so the suite stays
  offline, matching the existing extractor test pattern.

## Validation plan (thin slice first)

Run `synthesize --top-k 8` against the user's real legacy sample. The user reviews
the articles and we calibrate: `min_mentions`, the business-signal set, and
`min_relevance`. Only after calibration do we consider a full-corpus run or UI work.

## Open questions for spec review

1. Default values: `top-k` (8?), `min-mentions` (3?), `min-relevance` (0.5?).
2. Topic source for stage 1: graph `entities` only, or also free-form `concepts`?
3. Should `ask` prefer the `articles` collection now, or strictly later phase?
