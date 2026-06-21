# ERPNext accounting benchmark

A **repeatable retrieval/RAG regression benchmark** grounded in real ERPNext
accounting source — the kind of domain logic (business rules + numerical
calculation) this platform is meant to make retrievable. Use it to answer
"did a change make retrieval/answers better or worse?" with numbers instead of
vibes.

## What's here

| File | Purpose |
| --- | --- |
| `manifest.json` | Pins the ERPNext repo + **commit `0beb293`**, the 4 source files (with sha256), the target collection, and corpus path. The reproducibility anchor. |
| `setup_corpus.sh` | Re-fetches the pinned files (sparse, blobless) and ingests them into a clean `erpnext` collection. Idempotent. |
| `questions.jsonl` | **32 questions across 8 categories**, each with `ground_truth`, the source `symbol`, and machine-checkable `expected_sources` / `expected_answer`. Same `EvalCase` schema as `src/opendomainmcp/evals/` so it also loads with `load_evalset()`. |
| `run_benchmark.py` | Wires the project's `evals.run_evals` harness to the live store + RAG path and prints per-category + overall metrics. |
| `baseline.report.json` | Latest full run output (git-ignored; regenerated). |

The corpus is **not committed** (ERPNext is GPLv3); `setup_corpus.sh` re-materializes
it from the pinned commit into `.corpus/` (git-ignored).

## Why these files

`taxes_and_totals.py`, `pricing_rule.py` + `utils.py`, and `tax_rule.py` (≈3,100 LOC)
concentrate ERPNext's tax/discount/pricing math and rule-selection logic — dense
in exactly the two question types that stress a knowledge system: precise
**numerical formulas** and conditional **business rules**.

## Question categories

`numeric_calc` · `business_rule` · `workflow_sequence` · `exception_handling` ·
`edge_case` · `definition` · `graph_relation` · `negative_control`

`negative_control` cases ask about modules deliberately **excluded** from the
corpus (payroll, stock valuation, Shopify). A faithful system should refuse / say
"no content matched" rather than fabricate — they're printed for manual review,
not auto-scored.

## Metrics

- **retrieval_hit_rate** — fraction of cases where an `expected_sources` substring
  (a `file.py::symbol`) appeared among retrieved sources. Phrasing-independent and
  **the primary signal** — when the right chunk is retrieved, the answer is
  usually correct.
- **answer_grounding_rate** — fraction where the answer contained every
  `expected_answer` substring. Noisier with a local model; secondary.

## Run it

```bash
# 1. one-time (or after changing the pin): fetch corpus + ingest a clean collection
benchmarks/erpnext/setup_corpus.sh

# 2. run the benchmark (loads .env itself for LM Studio creds)
.venv/bin/python benchmarks/erpnext/run_benchmark.py --collection erpnext --out benchmarks/erpnext/baseline.report.json
```

Requires the embedder + answer model to be reachable (this repo's `.env` points
at local LM Studio) and, for `graph_relation` context, MariaDB. To benchmark a
change, run before and after and diff the two `*.report.json` files.

## Baseline (2026-06-21, local qwen3-coder-30b + qwen3-embedding-0.6b)

<!-- BASELINE -->
Collection `domain_knowledge`, top_k=8, 29 scored cases + 3 negative controls:

| metric | value |
| --- | --- |
| retrieval_hit_rate | **83%** (24/29) |
| answer_grounding_rate | ~93–100% (noisy run-to-run on a local model) |
| **negative_control refusal_rate** | **0/3 — all three FABRICATED** |

Per-category retrieval: `numeric_calc` 6/6, `business_rule` 5/5, `edge_case` 4/4,
`definition` 2/3, `workflow_sequence` 2/3, `graph_relation` 2/3,
`exception_handling` 3/5.

Retrieval misses (precise symbol not surfaced): `wf1` `_calculate`,
`ex1` `MultiplePricingRuleConflict`, `ex5` `ConflictingTaxRule`,
`df3` `set_discount_amount`, `gr2` `calculate_taxes`.

> **Headline finding — no relevance floor → fabrication.** All 3 negative
> controls (payroll withholding, stock-valuation formula, Shopify) produced
> confident answers citing 8 irrelevant chunks. Hybrid search always returns
> top-k, so the "no indexed content matched" guard (which only fires on *zero*
> results) never triggers, and the model fills the gap from parametric knowledge
> dressed in bogus citations. A retrieval **score threshold** (drop sources below
> a similarity floor; if none remain, refuse) is the highest-value fix and is
> exactly what `refusal_rate` here is meant to track over time.

### Known findings this benchmark was built to track

1. **Retrieval is the bottleneck, not synthesis.** When the right chunk is
   retrieved the local model answers correctly and grounded; misses cause vague
   or confidently-wrong answers.
2. **~14% of chunks fail JSON extraction** on the local model, leaving them with
   no summary/concepts → weak dense retrieval. `tax_rule.py`'s `get_tax_template`
   (the rule-selection logic, case `br1`) is one such chunk: keyword search ranks
   it #1, but a natural-language `ask` misses it.
3. **`calculate_item_values` is not independently indexed** (merged into a
   no-symbol chunk), so the core `amount = rate*qty` / discounted-rate formulas
   (`nc1`, `nc2`) are hard to surface — these expectations target the file, not a
   symbol, on purpose.
