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

    report = ctx.pipeline.ingest_path(args.path, progress=progress, sync=args.sync)
    print(f"Indexed {report.files_indexed} files / {report.chunks_indexed} chunks.")
    if report.chunks_pruned:
        print(f"Pruned {report.chunks_pruned} stale chunk(s).")
    if report.skipped:
        print(f"Skipped {len(report.skipped)} file(s).")
    if report.errors:
        print(f"Errors: {len(report.errors)}", file=sys.stderr)
        for err in report.errors:
            print(f"  {err['path']}: {err['error']}", file=sys.stderr)
    return 0


def _cmd_search(ctx, args) -> int:
    from .store import build_where

    where = build_where({"kind": args.kind, "language": args.language, "symbol": args.symbol})
    results = ctx.store.search(
        args.query, top_k=args.top_k, where=where,
        mode=ctx.settings.search_mode, source_contains=args.source,
    )
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


def _cmd_ask(ctx, args) -> int:
    from .query import AnswerError, answer_question

    try:
        result = answer_question(args.query, ctx.store, ctx.settings, top_k=args.top_k)
    except AnswerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(result["answer"])
    if result["citations"]:
        print("\nSources:")
        for c in result["citations"]:
            loc = c["source"] or "?"
            if c["symbol"]:
                loc += f"::{c['symbol']}"
            print(f"  [{c['n']}] {loc}")
    return 0


def _cmd_stats(ctx, args) -> int:
    for key, value in ctx.store.stats().items():
        print(f"{key:>12}: {value}")
    return 0


def _cmd_clear(ctx, args) -> int:
    ctx.store.clear()
    print("Collection cleared.")
    return 0


def _cmd_collections(ctx, args) -> int:
    active = ctx.store.stats()["collection"]
    for c in ctx.store.list_collections():
        mark = "*" if c["name"] == active else " "
        print(f"{mark} {c['name']}  ({c['count']} chunks)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opendomainmcp", description=__doc__)
    parser.add_argument("--collection", default=None, help="Knowledge base to use")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Ingest a file or directory")
    p_ingest.add_argument("path")
    p_ingest.add_argument(
        "--sync", action="store_true",
        help="Remove stored chunks for files deleted under the directory",
    )
    p_ingest.set_defaults(func=_cmd_ingest)

    p_search = sub.add_parser("search", help="Search the knowledge base")
    p_search.add_argument("query")
    p_search.add_argument("--top-k", type=int, default=5)
    p_search.add_argument("--kind", choices=["code", "text"], default=None)
    p_search.add_argument("--language", default=None, help="Filter by code language")
    p_search.add_argument("--symbol", default=None, help="Filter by exact symbol name")
    p_search.add_argument("--source", default=None, help="Filter by source path substring")
    p_search.set_defaults(func=_cmd_search)

    p_ask = sub.add_parser("ask", help="Ask a question; get a cited answer (needs API key)")
    p_ask.add_argument("query")
    p_ask.add_argument("--top-k", type=int, default=6)
    p_ask.set_defaults(func=_cmd_ask)

    p_stats = sub.add_parser("stats", help="Show collection statistics")
    p_stats.set_defaults(func=_cmd_stats)

    p_clear = sub.add_parser("clear", help="Delete all indexed content")
    p_clear.set_defaults(func=_cmd_clear)

    p_cols = sub.add_parser("collections", help="List knowledge bases (collections)")
    p_cols.set_defaults(func=_cmd_collections)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    ctx = build_context(collection=args.collection) if args.collection else build_context()
    return args.func(ctx, args)


if __name__ == "__main__":
    raise SystemExit(main())
