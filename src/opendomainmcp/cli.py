"""Command-line interface: ingest / search / stats / clear."""

from __future__ import annotations

import argparse
import sys

from .context import build_context


def _cmd_ingest(ctx, args) -> int:
    def progress(event):
        if event["stage"] in ("load", "skip", "error", "done"):
            detail = f" - {event['detail']}" if event["detail"] else ""
            print(f"[{event['stage']:>5}] {event['path']}{detail}", file=sys.stderr)

    report = ctx.pipeline.ingest_path(args.path, progress=progress)
    print(f"Indexed {report.files_indexed} files / {report.chunks_indexed} chunks.")
    if report.skipped:
        print(f"Skipped {len(report.skipped)} file(s).")
    if report.errors:
        print(f"Errors: {len(report.errors)}", file=sys.stderr)
        for err in report.errors:
            print(f"  {err['path']}: {err['error']}", file=sys.stderr)
    return 0


def _cmd_search(ctx, args) -> int:
    where = {"kind": args.kind} if args.kind else None
    results = ctx.store.search(args.query, top_k=args.top_k, where=where)
    if not results:
        print("No results.")
        return 0
    for i, r in enumerate(results, 1):
        meta = r.metadata
        loc = meta.get("source", "?")
        if meta.get("symbol"):
            loc += f"::{meta['symbol']}"
        print(f"\n#{i}  score={r.score:.3f}  {loc}")
        if meta.get("summary"):
            print(f"    summary: {meta['summary']}")
        snippet = r.text.strip().replace("\n", " ")
        print(f"    {snippet[:200]}")
    return 0


def _cmd_stats(ctx, args) -> int:
    for key, value in ctx.store.stats().items():
        print(f"{key:>12}: {value}")
    return 0


def _cmd_clear(ctx, args) -> int:
    ctx.store.clear()
    print("Collection cleared.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opendomainmcp", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Ingest a file or directory")
    p_ingest.add_argument("path")
    p_ingest.set_defaults(func=_cmd_ingest)

    p_search = sub.add_parser("search", help="Search the knowledge base")
    p_search.add_argument("query")
    p_search.add_argument("--top-k", type=int, default=5)
    p_search.add_argument("--kind", choices=["code", "text"], default=None)
    p_search.set_defaults(func=_cmd_search)

    p_stats = sub.add_parser("stats", help="Show collection statistics")
    p_stats.set_defaults(func=_cmd_stats)

    p_clear = sub.add_parser("clear", help="Delete all indexed content")
    p_clear.set_defaults(func=_cmd_clear)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    ctx = build_context()
    return args.func(ctx, args)


if __name__ == "__main__":
    raise SystemExit(main())
