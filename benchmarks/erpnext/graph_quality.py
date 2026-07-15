#!/usr/bin/env python
"""Structural quality report for a collection's knowledge graph.

Computes five offline metrics (extraction coverage, orphan ratio, connectivity,
entity duplication, core-concept recall) from one ``export_graph()`` bulk read.
Zero LLM calls — safe to run repeatedly; diff two reports to compare a change.

Usage:
    .venv/bin/python benchmarks/erpnext/graph_quality.py [--collection erpnext]
        [--golden PATH] [--out benchmarks/erpnext/graph-quality.report.json]

Requires MariaDB (ODM_GRAPH_DB_*) and the Chroma data dir; exits non-zero if
the graph is empty or unwired (Fail Loud — no silently empty reports).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent.parent


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs from .env into os.environ (without overriding)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(REPO_ROOT / ".env")

from opendomainmcp.context import build_context
from opendomainmcp.evals.graph_metrics import compute_all


def _all_chunk_ids(store) -> set[str]:
    ids: set[str] = set()
    offset = 0
    while True:
        items = store.get_items(limit=500, offset=offset)
        if not items:
            return ids
        ids.update(item["id"] for item in items)
        offset += len(items)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--collection", default="erpnext")
    ap.add_argument("--golden", default=str(HERE / "golden_concepts.json"))
    ap.add_argument("--out", default=str(HERE / "graph-quality.report.json"))
    args = ap.parse_args()

    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    ctx = build_context(collection=args.collection)
    export = ctx.graph.export_graph()
    if not export["entities"]:
        sys.exit(f"ERROR: graph for collection '{args.collection}' is empty or "
                 "the graph store is unwired (set ODM_GRAPH_DB_*). Refusing to "
                 "write an empty report.")

    chunk_ids = _all_chunk_ids(ctx.store)
    report = compute_all(export, chunk_ids, golden)
    report["meta"] = {"collection": args.collection,
                      "entities": len(export["entities"]),
                      "edges": len(export["edges"])}

    cov, orp, con = report["coverage"], report["orphans"], report["connectivity"]
    dup, rec = report["duplication"], report["concept_recall"]
    print(f"Graph quality — collection '{args.collection}' "
          f"({report['meta']['entities']} entities, {report['meta']['edges']} edges)\n")
    print(f"extraction coverage      {cov['chunks_with_entities']}/{cov['chunks_total']}"
          f"  ({cov['coverage']:.0%})   uncovered: {len(cov['uncovered_chunk_ids'])}")
    print(f"orphan entities          {orp['orphans']}/{orp['entities_total']}"
          f"  ({orp['orphan_ratio']:.0%})")
    print(f"connected components     {con['components']}"
          f"   largest holds {con['largest_component_share']:.0%}"
          f"   singletons: {con['singleton_components']}")
    print(f"duplicate clusters       {dup['duplicate_clusters']}"
          f"   excess entities: {dup['excess_entities']}"
          f"  ({dup['duplication_ratio']:.0%})")
    if dup["clusters"]:
        shown = len(dup["clusters"])
        note = f" (top {shown} of {dup['duplicate_clusters']})" if dup["clusters_truncated"] else ""
        print(f"  suspect clusters{note}:")
        for names in dup["clusters"][:5]:
            print(f"    - {', '.join(names)}")
    if rec["recall"] is not None:
        print(f"core-concept recall      {rec['found']}/{rec['golden_total']}"
              f"  ({rec['recall']:.0%})")
        if rec["missing_concepts"]:
            print(f"  missing: {', '.join(rec['missing_concepts'])}")

    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nFull report -> {args.out}")


if __name__ == "__main__":
    main()
