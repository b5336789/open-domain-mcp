"""Bottom-up LLM chain analysis (plan 4B, spec stage 5).

Leaves first: each function is summarized with its direct callees' source
(token-bounded) and deeper callees' summaries, so cost stays bounded at any
chain depth and shared subtrees are analyzed once. Summaries backfill the
already-stored chunks (re-upsert re-embeds the enriched text); whole chains
become retrievable ChainItems; the code graph is re-persisted with real
chunk ids. Anything the LLM could not cover falls back to the legacy
per-chunk extractor — coverage is always reported (Fail Loud)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from ..models import Chunk, ChainItem, KnowledgeUnit
from .build import build_codegraph, persist_codegraph
from .chains import assemble_chains
from .models import CodeGraph
from .order import bottom_up_levels

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage 2: per-file cached line slicing
# ---------------------------------------------------------------------------

def _function_sources(root: Path, graph: CodeGraph) -> dict[str, str]:
    """Return {qualified_name: source_text} sliced from cached file reads."""
    file_lines: dict[str, list[str]] = {}
    result: dict[str, str] = {}
    for fn in graph.functions.values():
        if fn.file not in file_lines:
            try:
                text = (root / fn.file).read_text(encoding="utf-8", errors="ignore")
                file_lines[fn.file] = text.splitlines()
            except OSError as exc:
                logger.warning("analyze: cannot read %s: %r", fn.file, exc)
                file_lines[fn.file] = []
        lines = file_lines[fn.file]
        # start_line/end_line are 1-indexed (FunctionDef contract)
        sliced = lines[fn.start_line - 1 : fn.end_line]
        result[fn.qualified_name] = "\n".join(sliced)
    return result


# ---------------------------------------------------------------------------
# Stage 2b: internal direct-callee adjacency
# ---------------------------------------------------------------------------

def _direct_callees(graph: CodeGraph) -> dict[str, set[str]]:
    """For each internal function, the set of direct internal callees
    reachable via calls/executes_sql edges."""
    result: dict[str, set[str]] = {q: set() for q in graph.functions}
    for e in graph.edges:
        if (not e.external
                and e.relation in ("calls", "executes_sql")
                and e.src in result
                and e.dst in graph.functions):
            result[e.src].add(e.dst)
    return result


# ---------------------------------------------------------------------------
# Stage 3: bottom-up level-parallel summarization
# ---------------------------------------------------------------------------

def _summarize_levels(
    levels: list[list[str]],
    graph: CodeGraph,
    fn_sources: dict[str, str],
    direct_callees_map: dict[str, set[str]],
    analyzer,
    settings,
    errors: list[dict],
    progress: Optional[Callable[[dict], None]] = None,
) -> dict[str, object]:
    """Analyze functions level by level (leaves first) using ThreadPoolExecutor
    per level.  Returns {qualified_name: FunctionSummary}."""
    context_budget: int = getattr(settings, "codegraph_context_chars", 16_000)
    concurrency: int = getattr(settings, "extract_concurrency", 8)
    summaries: dict[str, object] = {}

    for i, level in enumerate(levels):
        # snapshot summaries for this level (so workers share the same read-only view)
        summaries_snapshot = dict(summaries)

        def _analyze(qname: str, _snap=summaries_snapshot) -> tuple[str, object]:
            fn = graph.functions[qname]
            src = fn_sources.get(qname, "")

            direct_set = direct_callees_map.get(qname, set())

            # Fill callee_sources until the context budget is exhausted. The
            # budget bounds callee-source content only — the function's own
            # source is always included.
            callee_sources: dict[str, str] = {}
            callee_summaries: dict[str, object] = {}
            running = 0

            for callee in sorted(direct_set):
                callee_src = fn_sources.get(callee, "")
                if running + len(callee_src) <= context_budget:
                    callee_sources[callee] = callee_src
                    running += len(callee_src)
                else:
                    # Direct callee that didn't fit → use its summary if available
                    fs = _snap.get(callee)
                    if fs is not None:
                        callee_summaries[callee] = fs

            # Deeper callees (transitive but not direct) — always summaries
            seen: set[str] = set(direct_set) | {qname}
            frontier: list[str] = list(direct_set)
            while frontier:
                next_f: list[str] = []
                for f in frontier:
                    for callee2 in direct_callees_map.get(f, set()):
                        if callee2 not in seen and callee2 in graph.functions:
                            seen.add(callee2)
                            next_f.append(callee2)
                            if (callee2 not in callee_sources
                                    and callee2 not in callee_summaries):
                                fs2 = _snap.get(callee2)
                                if fs2 is not None:
                                    callee_summaries[callee2] = fs2
                frontier = next_f

            return qname, analyzer.summarize_function(fn, src, callee_sources, callee_summaries)

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {pool.submit(_analyze, qname): qname for qname in level}
            for future in as_completed(futures):
                qname = futures[future]
                try:
                    _, fs = future.result()
                    summaries[qname] = fs
                except Exception as exc:  # noqa: BLE001 - Fail Loud into errors list
                    errors.append({"function": qname, "error": repr(exc)})

        n = len([q for q in level if q in summaries])
        if progress:
            progress({"stage": "summaries",
                      "detail": f"level {i + 1}/{len(levels)}: {n} functions"})

    return summaries


# ---------------------------------------------------------------------------
# Stage 4: chunk backfill
# ---------------------------------------------------------------------------

def _backfill(
    root: Path,
    graph: CodeGraph,
    summaries: dict[str, object],
    store,
    settings,
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Re-upsert chunks that overlap each summarized function's line range,
    attaching the LLM summary as KnowledgeUnit.

    Returns (chunk_ids_by_function, file_to_source) where file_to_source maps
    fn.file -> stored source path (used by the fallback stage)."""
    # Build suffix map: fn.file -> stored source path (computed once)
    all_sources: set[str] = store.get_all_sources()
    file_to_source: dict[str, str] = {}
    for fn_file in {fn.file for fn in graph.functions.values()}:
        for source in all_sources:
            if source == fn_file or source.endswith("/" + fn_file):
                file_to_source[fn_file] = source
                break

    # Load all items per source once
    source_items: dict[str, list[dict]] = {}
    for source in file_to_source.values():
        if source not in source_items:
            ids = store.get_ids_for_source(source)
            items = []
            for item_id in ids:
                item = store.get_item(item_id)
                if item is not None:
                    items.append(item)
            source_items[source] = items

    review_status = "pending" if getattr(settings, "review_mode", False) else "approved"

    # Pass 1: accumulate per-chunk contributions. Two functions whose line
    # ranges overlap the same chunk must MERGE their knowledge, not have the
    # second upsert silently overwrite the first. Sorted qualified-name order
    # makes the merged output deterministic.
    contributions: dict[str, tuple[dict, list]] = {}  # chunk_id -> (item, [FunctionSummary])
    chunk_ids_by_function: dict[str, list[str]] = {}
    for qname in sorted(summaries):
        fs = summaries[qname]
        fn = graph.functions.get(qname)
        if fn is None:
            continue
        source = file_to_source.get(fn.file)
        if source is None:
            continue
        for item in source_items.get(source, []):
            meta = item.get("metadata", {})
            item_start = meta.get("start_line")
            item_end = meta.get("end_line")
            if item_start is None or item_end is None:
                continue
            # Overlap check: function [start_line, end_line] ∩ chunk [item_start, item_end]
            if item_end < fn.start_line or item_start > fn.end_line:
                continue
            contributions.setdefault(item["id"], (item, []))[1].append(fs)
            chunk_ids_by_function.setdefault(qname, []).append(item["id"])

    # Pass 2: one merge + upsert per chunk id (no duplicate re-embeds).
    for chunk_id, (item, fss) in contributions.items():
        if len(fss) > 1:
            logger.debug("analyze: merging %d function summaries into chunk %s",
                         len(fss), chunk_id)
        summary_parts: list[str] = []
        concepts: list[str] = []
        for fs in fss:
            if fs.summary and fs.summary not in summary_parts:
                summary_parts.append(fs.summary)
            for rule in fs.rules:
                if rule not in concepts:
                    concepts.append(rule)
        meta = item.get("metadata", {})
        chunk = Chunk(
            text=item.get("text", ""),
            source=meta.get("source", ""),
            kind=meta.get("kind", "text"),
            language=meta.get("language"),
            symbol=meta.get("symbol"),
            node_type=meta.get("node_type"),
            start_line=meta.get("start_line"),
            end_line=meta.get("end_line"),
        )
        chunk.knowledge = KnowledgeUnit(
            summary=" ".join(summary_parts),
            concepts=concepts[:8],
            confidence=max((fs.confidence for fs in fss), default=0.0),
            review_status=review_status,
        )
        store.upsert([chunk])

    return chunk_ids_by_function, file_to_source


# ---------------------------------------------------------------------------
# Stage 5: chain items
# ---------------------------------------------------------------------------

def _store_chains(
    chains,
    graph: CodeGraph,
    summaries: dict[str, object],
    chunk_ids_by_function: dict[str, list[str]],
    store,
    analyzer,
    errors: list[dict],
) -> int:
    """Synthesize each chain into a ChainItem and upsert into the sibling
    __chains collection. Returns the number of chains stored."""
    chains_store = store.sibling(f"{store.stats()['collection']}__chains")
    stored = 0
    current_ids: set[str] = set()
    for chain in chains:
        # Only process chains with at least one summarized member
        if not any(m in summaries for m in chain.members):
            continue
        try:
            chain_data = analyzer.analyze_chain(chain, summaries)
        except Exception as exc:  # noqa: BLE001 - Fail Loud
            errors.append({"chain": chain.entry, "error": repr(exc)})
            continue

        sources: list[str] = []
        member_chunk_ids: list[str] = []
        for member in chain.members:
            fn = graph.functions.get(member)
            if fn:
                sources.append(f"{fn.file}:{fn.start_line}-{fn.end_line}")
            member_chunk_ids.extend(chunk_ids_by_function.get(member, []))

        item = ChainItem(
            entry=chain.entry,
            title=chain_data["title"],
            body=chain_data["body"],
            rules=chain_data.get("rules", []),
            members=chain.members,
            sources=sources,
            member_chunk_ids=member_chunk_ids,
            truncated=chain.truncated,
        )
        chains_store.upsert([item])
        current_ids.add(item.id)
        stored += 1

    # Prune stale items from previous runs. Only prune when at least one chain
    # was stored (i.e. we had successful summaries) — consistent with the
    # graph-persist guard so a total-failure run never wipes prior good data.
    if current_ids:
        existing = chains_store.get_items(limit=10_000)
        stale = [i["id"] for i in existing if i["id"] not in current_ids]
        if stale:
            chains_store.delete_ids(stale)

    return stored


# ---------------------------------------------------------------------------
# Stage 7: fallback per-chunk extraction
# ---------------------------------------------------------------------------

def _fallback_extract(
    graph: CodeGraph,
    store,
    chunk_ids_by_function: dict[str, list[str]],
    file_to_source: dict[str, str],
    extractor,
) -> int:
    """Run the legacy extractor on code chunks under analyzed sources whose
    ids were NOT already backfilled.  Returns the count extracted."""
    backfilled_ids: set[str] = set()
    for ids in chunk_ids_by_function.values():
        backfilled_ids.update(ids)

    analyzed_sources: set[str] = set(file_to_source.values())
    fallback_count = 0

    for source in analyzed_sources:
        ids = store.get_ids_for_source(source)
        for item_id in ids:
            if item_id in backfilled_ids:
                continue
            item = store.get_item(item_id)
            if item is None:
                continue
            meta = item.get("metadata", {})
            if meta.get("kind") != "code":
                continue
            chunk = Chunk(
                text=item.get("text", ""),
                source=meta.get("source", source),
                kind=meta.get("kind", "text"),
                language=meta.get("language"),
                symbol=meta.get("symbol"),
                node_type=meta.get("node_type"),
                start_line=meta.get("start_line"),
                end_line=meta.get("end_line"),
            )
            try:
                chunk.knowledge = extractor.extract(chunk.text, chunk.kind, chunk.language)
                store.update_metadata(item_id, chunk.metadata())
                fallback_count += 1
            except Exception as exc:  # noqa: BLE001 - Fail Loud
                logger.warning("analyze: fallback extract failed for %s: %r", item_id, exc)

    return fallback_count


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def analyze_corpus(
    root: "str | Path",
    store,
    settings,
    graph_store,
    progress: Optional[Callable[[dict], None]] = None,
    analyzer=None,
    extractor=None,
) -> dict:
    """Run the full bottom-up chain analysis pass over a code corpus.

    Returns::

        {
            "functions_analyzed": int,
            "chains_stored":      int,
            "chunks_backfilled":  int,
            "fallback_extracted": int,
            "coverage":           float,   # backfilled / (backfilled + fallback)
            "errors":             list[dict],
        }
    """
    root = Path(root)
    errors: list[dict] = []

    def _emit(stage: str, **kw):
        if progress:
            progress({"stage": stage, **kw})

    # Stage 1 ---------------------------------------------------------------
    _emit("codegraph", detail="building")
    graph = build_codegraph(root, settings)
    chains = assemble_chains(graph, settings.codegraph_max_chain_depth)
    levels = bottom_up_levels(graph)

    if analyzer is None:
        from .analyze_llm import ChainAnalyzer
        analyzer = ChainAnalyzer(settings)

    if extractor is None:
        from ..extract.knowledge import get_extractor
        extractor = get_extractor(settings)

    # Stage 2 (helper) -------------------------------------------------------
    fn_sources = _function_sources(root, graph)
    direct_callees_map = _direct_callees(graph)

    # Stage 3 ---------------------------------------------------------------
    _emit("summaries", total=len(graph.functions), levels=len(levels))
    summaries = _summarize_levels(
        levels, graph, fn_sources, direct_callees_map, analyzer, settings, errors,
        progress=progress,
    )
    functions_analyzed = len(summaries)

    # Stage 4 ---------------------------------------------------------------
    _emit("backfill", functions_analyzed=functions_analyzed)
    chunk_ids_by_function, file_to_source = _backfill(root, graph, summaries, store, settings)
    # Distinct chunks: a chunk shared by several functions is backfilled once.
    chunks_backfilled = len({cid for ids in chunk_ids_by_function.values() for cid in ids})

    # Stage 5 ---------------------------------------------------------------
    _emit("chains", chains=len(chains))
    chains_stored = _store_chains(
        chains, graph, summaries, chunk_ids_by_function, store, analyzer, errors,
    )

    # Stage 6 ---------------------------------------------------------------
    # Guard: if the LLM failed entirely AND there were functions to analyze,
    # skip delete + persist so a previously-good graph is not replaced by
    # synthetic-id-only rows.  A fresh corpus with zero functions (and zero
    # errors) still gets an empty graph persisted as expected.
    if not summaries and errors and graph.functions:
        errors.append({"stage": "persist",
                       "error": "skipped graph persistence: no successful summaries"})
        graph_persist_skipped = True
    else:
        graph_store.delete_codegraph()
        persist_codegraph(graph, graph_store, chunk_ids_by_function=chunk_ids_by_function)
        graph_persist_skipped = False

    # Stage 7 ---------------------------------------------------------------
    _emit("fallback")
    fallback_extracted = _fallback_extract(
        graph, store, chunk_ids_by_function, file_to_source, extractor,
    )

    # Stage 8: coverage -----------------------------------------------------
    total = chunks_backfilled + fallback_extracted
    coverage = chunks_backfilled / total if total > 0 else 0.0

    return {
        "functions_analyzed": functions_analyzed,
        "chains_stored": chains_stored,
        "chunks_backfilled": chunks_backfilled,
        "fallback_extracted": fallback_extracted,
        "coverage": coverage,
        "errors": errors,
        "graph_persist_skipped": graph_persist_skipped,
    }
