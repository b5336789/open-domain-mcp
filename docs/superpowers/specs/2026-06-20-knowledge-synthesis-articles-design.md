# Knowledge Synthesis — Business-Meaning Articles

**Date:** 2026-06-20
**Status:** Design approved; open questions resolved. Ready for implementation plan.

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
- **Fully autonomous: no human-in-the-loop.** The default run takes zero arguments,
  derives its own thresholds, judges business-relevance itself, and **self-verifies**
  its output. No human calibration gate and no manual review are required for the
  pipeline to complete. There are **no human-tuned magic numbers** in the default
  path; CLI flags exist only as optional escape hatches.

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
understandable and testable. **The default run is fully autonomous and takes no
arguments.**

### 1. Gather — candidate topics
Read **already-stored** chunk metadata from Chroma (`entities` / `concepts` are
already there — no re-extraction). For each candidate topic, count how many chunks
mention it and record whether it appears in **code chunks**, **doc chunks**, or
both (the cross-validated flag).

### 2. Gate — structural filter (no tuned numbers)
Keep a topic using a **structural rule**, not a human-picked threshold:

> A topic qualifies if it is **cross-validated** (present in both code and docs),
> **or** it is mentioned in **more than one** chunk whose `knowledge_type` is in the
> business set (Feature / Workflow / Permission / Constraint) or whose `audience`
> includes `product_manager` / `solutions_architect`.

"More than once" is a structural minimum (filters one-off noise), not a tuning knob.
Topics are **ranked** by signal strength (cross-validated highest) only to order the
work and the report — **every** gated topic is processed; there is no human-chosen K.
The judgement of "is this actually worth an article" is delegated to the LLM critic
in stage 5, not to a numeric cutoff.

### 3. Collect — evidence per topic
For each gated topic, use the existing hybrid search to pull its most relevant
chunks, partitioned into a **code-evidence** set and a **doc-evidence** set.

### 4. Synthesize — one article per topic (LLM, injected extractor-style client)
Fixed structure, conversational prose:
1. What this is / what it does (plain language).
2. **What the docs say vs. what the code actually does** — surface any gap.
3. Cited sources as `file:line`.

The LLM also returns a `title` and a `business_relevance` score (0–1). Per-topic
failures are recorded, never silently dropped (Fail Loud).

### 5. Critic — automated self-verification (replaces the human review gate)
A **second, independent LLM call** acts as an adversarial critic over each draft
article, returning a structured verdict:
- **Grounded?** Is every substantive claim supported by the cited source chunks?
  (Refute hallucinations — the article's claims are checked against the evidence it
  was given.)
- **Business-meaningful?** Is this genuine domain/business knowledge, or just
  implementation trivia dressed up?

An article is **kept only if the critic passes both checks.** This is the autonomy
safeguard: quality rides on the judges, so we use two independent passes (author +
skeptic) instead of trusting one. The numeric `business_relevance` is retained for
the report/ranking but is **not** a human-tuned gate — the binary critic verdict is
the gate. To keep the judges honest, the critic is prompted to **default to reject
when uncertain**.

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
  business_relevance: float  # author's self-score, for ranking/report only
  source_chunk_ids: list[str]
  sources: list[str]       # "file:line" citations
  cross_validated: bool    # appeared in both code and docs
  critic_verdict: dict     # {grounded: bool, business_meaningful: bool, note: str}
```

Only articles whose `critic_verdict` passes both checks are stored.

## Surfaces

- **CLI:** `./run.sh synthesize` — **zero required arguments**; runs the full
  autonomous pipeline, stores surviving articles, prints a report. Optional escape
  hatches only: `--limit N` (cap topics processed, for cost control on huge
  corpora), `--dry-run` (synthesize + critique but print instead of store). No flag
  is needed for a normal run.
- **Storage:** the `articles` Chroma collection, reachable from `build_context()`
  so search / a future UI page / `ask` can consume it.
- UI browse page: **deferred** to a follow-up (storage is built to support it now).

## Error handling (Fail Loud)

- No LLM API key → fail loud, do not fabricate articles.
- Per-topic synthesis or critic failure → recorded in the report, other topics
  continue.
- Zero candidate topics survive the gate, or the critic rejects everything →
  explicit message (e.g. "no business-meaningful topics passed the critic"), not a
  silent empty run.

## Autonomy & quality safeguards

Because the run is fully autonomous (no human calibration, no human review gate),
quality rests entirely on the LLM judges. Safeguards that replace the human:

- **Two independent passes** — author (stage 4) then skeptical critic (stage 5).
  An article ships only if the critic confirms it is grounded *and*
  business-meaningful.
- **Grounding by construction** — the critic checks the article's claims against the
  exact source chunks it was synthesized from; unsupported claims fail.
- **Reject-when-uncertain** — the critic defaults to reject, biasing toward
  precision over recall (better to drop a borderline article than ship a wrong one).
- **Observable, non-blocking report** — every run prints what was gated, what the
  critic rejected and why, and what was stored. Nothing blocks on a human, but a
  human *can* inspect quality at any time. (Fail Loud: silent truncation / silent
  empty runs are forbidden.)

**Risk (explicit):** with no human in the loop, early-corpus article quality depends
on judge quality; a systematically biased judge could pass weak articles or drop
good ones. The report is the early-warning signal. If precision proves poor, the
mitigation is to strengthen the critic prompt or add a second critic vote — **not**
to reintroduce a human gate.

## Testing (business-logic, offline)

- **Gather/Gate** with fake stored chunks: a cross-validated topic passes the gate;
  a code-only single-mention topic is excluded; ranking puts cross-validated first.
- **Critic gate:** an article the critic marks `grounded=false` or
  `business_meaningful=false` is dropped; one passing both is kept.
- **Idempotency:** same topic + same member chunk ids → same article id; re-run does
  not duplicate.
- **Fail Loud:** missing key raises; per-topic failure is reported, not swallowed;
  all-rejected → explicit message.
- Both LLM calls (author + critic) are **injected** fakes returning canned JSON, so
  the suite stays offline, matching the existing extractor test pattern.

## Resolved decisions

1. **Topic source (stage 1):** use graph `entities` as the backbone **and** fold in
   free-form `concepts`, normalized/deduped together (case-insensitive). Entities are
   clean and typed; concepts widen recall on legacy systems with sparse graphs.
2. **`ask` integration:** **deferred** to a follow-up. This slice produces and stores
   articles in the `articles` collection; making `ask`/search prefer that collection
   is a later phase.
3. **Critic strength:** **single critic call** for v1 (simpler, cheaper). Best-of-N
   voting is the documented escalation if the report shows poor precision — not part
   of the initial build.
