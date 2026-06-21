#!/usr/bin/env python
"""Run the ERPNext accounting benchmark against a live collection.

Wires the project's own offline eval harness (``evals.run_evals``) to the real
``build_context`` store + RAG path, so it reports the same two deterministic
metrics on a real, model-backed system:

* **retrieval_hit_rate** -- did the right chunk (``expected_sources`` substring)
  surface among the retrieved sources? This is the stable, phrasing-independent
  signal and the primary regression metric.
* **answer_grounding_rate** -- did the synthesized answer contain every
  ``expected_answer`` substring? Inherently noisier with a local model; treat as
  secondary.

Negative-control cases (category ``negative_control``) carry no expectations and
are not scored -- their answers are printed for manual hallucination review.

Usage:
    .venv/bin/python benchmarks/erpnext/run_benchmark.py [--collection erpnext]
        [--top-k 8] [--questions PATH] [--out report.json]
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent.parent


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs from .env into os.environ (without overriding).

    Credentials like OPENAI_API_KEY / OPENAI_BASE_URL live in .env and are read
    from the environment by the embedder/extractor (not via Settings). run.sh
    sources .env for the CLI; this script is invoked directly, so do it here too.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_dotenv(REPO_ROOT / ".env")

from opendomainmcp.context import build_context
from opendomainmcp.evals.cases import load_evalset
from opendomainmcp.evals.runner import run_evals
from opendomainmcp.query.rag import answer_question
from opendomainmcp.retrieval import search_unified


def _categories(path: Path) -> dict[str, str]:
    cats: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            cats[str(rec["id"])] = rec.get("category", "uncategorized")
    return cats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--collection", default="erpnext",
                    help="Collection holding the ingested ERPNext corpus (default: erpnext)")
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--questions", default=str(HERE / "questions.jsonl"))
    ap.add_argument("--out", default=None, help="Write the full JSON report here")
    args = ap.parse_args()

    qpath = Path(args.questions)
    cases = load_evalset(qpath)
    cats = _categories(qpath)

    ctx = build_context(collection=args.collection)
    store, settings = ctx.store, ctx.settings

    # Mirror exactly what answer_question() retrieves, so the retrieval metric
    # reflects the real ask path (not a separate search configuration).
    def retrieve(q: str):
        return search_unified(store, q, top_k=args.top_k,
                              mode=settings.search_mode, settings=settings)

    def ask(q: str):
        return answer_question(q, store, settings, top_k=args.top_k)

    # Negative controls ask about modules excluded from the corpus. run_evals
    # only records an answer when expected_answer is set, so it would silently
    # hide fabrication here -- evaluate them explicitly instead.
    neg_cases = [c for c in cases if cats.get(c.id) == "negative_control"]
    scored_cases = [c for c in cases if cats.get(c.id) != "negative_control"]

    print(f"Running {len(scored_cases)} scored cases against collection "
          f"'{args.collection}' (top_k={args.top_k})...\n")
    report = run_evals(scored_cases, retrieve=retrieve, ask=ask)

    # Per-category aggregation.
    by_cat_ret: dict[str, list[bool]] = defaultdict(list)
    by_cat_ans: dict[str, list[bool]] = defaultdict(list)
    for cr in report.cases:
        cat = cats.get(cr.id, "uncategorized")
        if cr.retrieval_hit is not None:
            by_cat_ret[cat].append(cr.retrieval_hit)
        if cr.answer_grounded is not None:
            by_cat_ans[cat].append(cr.answer_grounded)

    def pct(xs: list[bool]) -> str:
        return f"{sum(xs)}/{len(xs)}" if xs else "  -  "

    print("category            retrieval   grounding")
    print("-" * 44)
    for cat in sorted(set(list(by_cat_ret) + list(by_cat_ans)) | set(cats.values())):
        print(f"{cat:<18} {pct(by_cat_ret.get(cat, [])):>9}   {pct(by_cat_ans.get(cat, [])):>9}")
    print("-" * 44)
    rhr = report.retrieval_hit_rate
    agr = report.answer_grounding_rate
    print(f"OVERALL retrieval_hit_rate   = {rhr:.0%}" if rhr is not None else "OVERALL retrieval_hit_rate   = n/a")
    print(f"OVERALL answer_grounding_rate = {agr:.0%}" if agr is not None else "OVERALL answer_grounding_rate = n/a")

    # Failing scored cases + negative controls, for inspection.
    print("\nFAILED retrieval (right chunk not surfaced):")
    for cr in report.cases:
        if cr.retrieval_hit is False:
            print(f"  [{cats.get(cr.id)}] {cr.id}: missing {cr.missing_sources}")

    # Explicit negative-control evaluation. A faithful system retrieves nothing
    # relevant and refuses ("no indexed content matched"); fabrication = a
    # substantive answer despite the topic being absent from the corpus.
    refusal_markers = ("no indexed content", "no content matched", "not found",
                       "cannot find", "does not contain", "no information")
    print("\nNEGATIVE CONTROLS (out-of-corpus -- should refuse, not fabricate):")
    neg_records = []
    refused = 0
    for c in neg_cases:
        res = ask(c.query)
        ans = (res.get("answer") if isinstance(res, dict) else str(res)) or ""
        n_src = len(res.get("citations", [])) if isinstance(res, dict) else 0
        is_refusal = (not ans.strip()) or any(m in ans.lower() for m in refusal_markers)
        refused += is_refusal
        verdict = "REFUSED ok" if is_refusal else "FABRICATED!!"
        print(f"  [{verdict}] {c.id} ({n_src} sources): {ans.replace(chr(10),' ')[:150]}")
        neg_records.append({"id": c.id, "category": "negative_control",
                            "query": c.query, "answer": ans, "n_sources": n_src,
                            "refused": is_refusal})
    print(f"  -> refusal_rate = {refused}/{len(neg_cases)} "
          f"({'higher is better; fabrication = hallucination' })")

    if args.out:
        payload = report.to_dict()
        for c in payload["cases"]:
            c["category"] = cats.get(c["id"], "uncategorized")
        payload["negative_controls"] = neg_records
        payload["negative_control_refusal_rate"] = (
            refused / len(neg_cases) if neg_cases else None)
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nFull report -> {args.out}")


if __name__ == "__main__":
    main()
