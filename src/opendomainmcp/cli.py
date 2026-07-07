"""Command-line interface: ingest / search / stats / clear."""

from __future__ import annotations

import argparse
import sys

from .context import build_context
from .store.chroma_store import VALID_REVIEW_STATUSES


def _cmd_ingest(ctx, args) -> int:
    def progress(event):
        if event["stage"] in ("load", "skip", "filter", "error", "done"):
            detail = f" - {event['detail']}" if event["detail"] else ""
            print(f"[{event['stage']:>6}] {event['path']}{detail}", file=sys.stderr)

    report = ctx.pipeline.ingest_path(
        args.path, progress=progress, sync=args.sync,
        exclude=args.exclude, use_default_excludes=not args.no_default_excludes,
    )
    print(f"Indexed {report.files_indexed} files / {report.chunks_indexed} chunks.")
    if report.chunks_pruned:
        print(f"Pruned {report.chunks_pruned} stale chunk(s).")
    if report.filtered:
        print(f"Filtered {len(report.filtered)} file(s) by exclude rules.")
    if report.skipped:
        print(f"Skipped {len(report.skipped)} file(s).")
    if report.evidence_verified or report.evidence_unverified:
        print(f"Evidence: {report.evidence_verified} verified / {report.evidence_unverified} unverified.")
    if report.errors:
        print(f"Errors: {len(report.errors)}", file=sys.stderr)
        for err in report.errors:
            print(f"  {err['path']}: {err['error']}", file=sys.stderr)
    return 0


def _cmd_search(ctx, args) -> int:
    from .store import build_where
    from .retrieval import search_unified

    where = build_where({"kind": args.kind, "language": args.language, "symbol": args.symbol})
    results = search_unified(
        ctx.store, args.query, top_k=args.top_k, where=where,
        mode=ctx.settings.search_mode, settings=ctx.settings,
        source_contains=args.source,
    )
    if not results:
        print("No results.")
        return 0
    for i, r in enumerate(results, 1):
        meta = r.metadata
        if meta.get("kind") == "article":
            print(f"\n#{i}  score={r.score:.3f}  [article] {meta.get('title', '?')}")
        else:
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
    from .query import AnswerError, answer_question_stream

    citations = []
    try:
        for event in answer_question_stream(
            args.query, ctx.store, ctx.settings, top_k=args.top_k, graph=ctx.graph
        ):
            if event["type"] == "delta":
                print(event["text"], end="", flush=True)
            elif event["type"] == "citations":
                citations = event["citations"]
    except AnswerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print()  # newline after the streamed answer
    if citations:
        print("\nSources:")
        for c in citations:
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


def _cmd_backfill_review(ctx, args) -> int:
    updated = ctx.store.backfill_review_status(
        status=args.status, only_missing=not args.all
    )
    print(f"Back-filled review_status={args.status} on {updated} chunk(s).")
    return 0


def _cmd_collections(ctx, args) -> int:
    active = ctx.store.stats()["collection"]
    for c in ctx.store.list_collections():
        mark = "*" if c["name"] == active else " "
        print(f"{mark} {c['name']}  ({c['count']} chunks)")
    return 0


def _cmd_codegraph(ctx, args) -> int:
    import json as _json
    from collections import Counter

    from .codegraph.build import build_codegraph, persist_codegraph
    from .codegraph.chains import assemble_chains

    if args.analyze:
        from .codegraph.analyze import analyze_corpus
        from .graph.store import NullGraphStore

        if isinstance(ctx.graph, NullGraphStore):
            print("warning: graph store not configured — chain analysis will "
                  "skip graph persistence", file=sys.stderr)

        def progress(event):
            print(f"[{event['stage']:>9}] {event.get('detail', '')}",
                  file=sys.stderr)

        result = analyze_corpus(args.path, ctx.store, ctx.settings, ctx.graph,
                                progress=progress)
        if args.json:
            print(_json.dumps(result, indent=2))
        else:
            for key, value in result.items():
                print(f"{key}: {value}")
        return 0

    graph = build_codegraph(args.path, ctx.settings)
    chains = assemble_chains(graph,
                             max_depth=ctx.settings.codegraph_max_chain_depth)
    stats = {
        "functions": len(graph.functions),
        "functions_by_language": dict(Counter(
            f.language for f in graph.functions.values())),
        "edges_by_relation": dict(Counter(e.relation for e in graph.edges)),
        "unresolved_external_edges": sum(1 for e in graph.edges if e.external),
        "entry_points": sum(1 for c in chains),
        "truncated_chains": sum(1 for c in chains if c.truncated),
    }
    if args.persist:
        from .graph.store import NullGraphStore
        if isinstance(ctx.graph, NullGraphStore):
            print(
                "graph store not configured — set ODM_GRAPH_DB_* "
                "(or ODM_GRAPH_STORE_BACKEND=mariadb)",
                file=sys.stderr,
            )
            return 1
        stats["persisted"] = persist_codegraph(graph, ctx.graph)
    if args.json:
        print(_json.dumps(stats, indent=2))
    else:
        for key, value in stats.items():
            print(f"{key}: {value}")
    return 0


def _cmd_consolidate(ctx, args) -> int:
    import json as _json
    from .consensus.run import run_consensus

    def progress(event):
        stage = event.get("stage", "")
        detail = {k: v for k, v in event.items() if k != "stage"}
        print(f"[{stage:>10}] {detail}", file=sys.stderr)

    try:
        result = run_consensus(ctx.store, ctx.settings, graph=ctx.graph, progress=progress)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(_json.dumps(result, indent=2))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")
    return 0


def _cmd_synthesize(ctx, args) -> int:
    from .synthesis import synthesize_articles

    report = synthesize_articles(
        ctx.store, ctx.settings, graph=ctx.graph,
        limit=args.limit, dry_run=args.dry_run,
    )
    print(f"Gated {report.topics_gated} topic(s); wrote {report.articles_written}.")
    print(f"Stored {report.stored} article(s). Rejected {len(report.rejected)}. "
          f"Removed {report.removed} stale.")
    for r in report.rejected:
        print(f"  rejected: {r['topic']}  {r['verdict']}", file=sys.stderr)
    if report.errors:
        print(f"Errors: {len(report.errors)}", file=sys.stderr)
        for e in report.errors:
            print(f"  {e['topic']}: {e['error']}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opendomainmcp", description=__doc__)
    parser.add_argument("--collection", default=None, help="Knowledge base to use")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser(
        "ingest",
        help="Ingest a file, directory, Git repo, zip, or API spec",
        description=(
            "Ingest content into the knowledge base. The PATH may be:\n"
            "  - a single file (code, Markdown/text, PDF, DOCX, HTML)\n"
            "  - a directory (walked recursively)\n"
            "  - a Git repository URL (shallow-cloned, then ingested)\n"
            "  - a .zip archive (safely extracted, then ingested)\n"
            "  - an OpenAPI/Swagger spec (JSON or YAML, split per operation)\n"
            "  - a GraphQL SDL file (.graphql/.graphqls/.gql, split per definition)\n"
            "\n"
            "Git URLs are recognised by scheme (git@, git+, ssh://, git://), a\n"
            "trailing .git, or a known host (github.com, gitlab.com, bitbucket.org)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_ingest.add_argument(
        "path",
        help="File, directory, Git repo URL, .zip archive, or OpenAPI/GraphQL spec",
    )
    p_ingest.add_argument(
        "--sync", action="store_true",
        help="Remove stored chunks for files deleted under the directory",
    )
    p_ingest.add_argument(
        "--exclude", action="append", default=[], metavar="GLOB",
        help="Extra exclude pattern for this run (repeatable; layered over "
             "built-in defaults and ODM_INGEST_EXCLUDE)",
    )
    p_ingest.add_argument(
        "--no-default-excludes", action="store_true",
        help="Disable the built-in exclude list and generated-marker sniff",
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

    p_backfill = sub.add_parser(
        "backfill-review",
        help="Set review_status on stored chunks that lack one",
    )
    p_backfill.add_argument(
        "--status", choices=list(VALID_REVIEW_STATUSES), default="approved",
        help="Review status to apply (default: approved)",
    )
    p_backfill.add_argument(
        "--all", action="store_true",
        help="Re-stamp every chunk, not just those missing a review_status",
    )
    p_backfill.set_defaults(func=_cmd_backfill_review)

    p_synth = sub.add_parser(
        "synthesize",
        help="Autonomously synthesize business-meaning articles from indexed content",
    )
    p_synth.add_argument("--limit", type=int, default=None,
                         help="Cap topics processed (cost control); default: all gated")
    p_synth.add_argument("--dry-run", action="store_true",
                         help="Synthesize and critique but do not store")
    p_synth.set_defaults(func=_cmd_synthesize)

    p_cg = sub.add_parser("codegraph",
                          help="Build the function-level code graph and show stats")
    p_cg.add_argument("path", help="Directory of source code to analyze")
    p_cg.add_argument("--persist", action="store_true",
                      help="Write the graph to the configured graph store")
    p_cg.add_argument("--analyze", action="store_true",
                      help="Run the LLM chain-analysis pass (backfills chunk "
                           "summaries and stores chain items)")
    p_cg.add_argument("--json", action="store_true", help="Emit stats as JSON")
    p_cg.set_defaults(func=_cmd_codegraph)

    p_consolidate = sub.add_parser(
        "consolidate",
        help="Run a consensus pass to deduplicate and consolidate rules",
    )
    p_consolidate.add_argument("--json", action="store_true", help="Emit result as JSON")
    p_consolidate.set_defaults(func=_cmd_consolidate)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    ctx = build_context(collection=args.collection) if args.collection else build_context()
    return args.func(ctx, args)


if __name__ == "__main__":
    raise SystemExit(main())
