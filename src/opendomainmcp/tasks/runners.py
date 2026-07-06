from __future__ import annotations

from ..codegraph.analyze import analyze_corpus
from ..codegraph.build import build_codegraph
from ..codegraph.chains import assemble_chains
from ..extract.knowledge import get_extractor
from ..ingest.checkpoint import Checkpoint, extractor_signature
from ..models import Chunk
from ..synthesis import synthesize_articles

_TERMINAL_STAGES = {"store", "skip", "error"}  # per-file terminal ingest events


def run_ingest(ctx, store, task, is_cancelled) -> None:
    path = task.params["path"]
    sync = bool(task.params.get("sync", False))
    names = ctx.pipeline.list_files(path)
    store.set_children_names(task.id, names)

    cp = Checkpoint.new(ctx.settings.data_dir, task.id, path, sync,
                        extractor_signature(ctx.settings))
    cp.save()

    done = {"n": 0}
    failures: list[dict] = []

    def progress(event):
        stage = event.get("stage", "")
        if stage in _TERMINAL_STAGES:
            done["n"] += 1
            if stage in ("skip", "error"):
                failures.append({"name": event.get("path", ""),
                                 "status": "skipped" if stage == "skip" else "error"})
            store.update(task.id, throttle=True, done=done["n"], failures=list(failures))
        if is_cancelled():
            cp.request_cancel()

    report = ctx.pipeline.ingest_path(path, progress, sync, None, cp)
    store.update(task.id, done=len(names), failures=failures,
                 result=report.to_dict())


def run_synthesize(ctx, store, task, is_cancelled) -> None:
    limit = task.params.get("limit")
    dry_run = bool(task.params.get("dry_run", False))
    done = {"n": 0}
    failures: list[dict] = []
    seen_total = {"n": 0}

    def on_event(event):
        stage = event.get("stage", "")
        if stage == "start":
            seen_total["n"] = event.get("total", 0)
            store.set_children_names(
                task.id, [f"topic {i+1}" for i in range(seen_total["n"])])
        elif stage in ("stored", "rejected", "topic_error"):
            done["n"] += 1
            if stage != "stored":
                failures.append({"name": event.get("topic", ""),
                                 "status": "error" if stage == "topic_error" else "skipped"})
            store.update(task.id, throttle=True, done=done["n"], failures=list(failures))
        if is_cancelled():
            raise _Cancelled()

    try:
        report = synthesize_articles(ctx.store, ctx.settings, graph=ctx.graph,
                                     limit=limit, dry_run=dry_run, on_event=on_event)
    except _Cancelled:
        store.update(task.id, done=done["n"], failures=failures)
        return
    store.update(task.id, done=done["n"], failures=failures, result=report.to_dict())


def run_extract(ctx, store, task, is_cancelled) -> None:
    source = task.params["source"]
    ids = sorted(ctx.store.get_ids_for_source(source))
    store.set_children_names(task.id, ids)
    extractor = get_extractor(ctx.settings)
    done = 0
    failures: list[dict] = []
    for item_id in ids:
        if is_cancelled():
            store.update(task.id, done=done, failures=failures)
            return
        item = ctx.store.get_item(item_id)
        if item is None:
            done += 1
            continue
        meta = item.get("metadata", {})
        chunk = Chunk(text=item.get("text", ""), source=meta.get("source", source),
                      kind=meta.get("kind", "text"), language=meta.get("language"),
                      symbol=meta.get("symbol"), node_type=meta.get("node_type"),
                      start_line=meta.get("start_line"), end_line=meta.get("end_line"))
        try:
            chunk.knowledge = extractor.extract(chunk.text, chunk.kind, chunk.language)
            ctx.store.update_metadata(item_id, chunk.metadata())
        except Exception as exc:  # noqa: BLE001 - Fail Loud into the report
            failures.append({"name": item_id, "status": "error"})
        done += 1
        store.update(task.id, throttle=True, done=done, failures=list(failures))
    store.update(task.id, done=done, failures=failures,
                 result={"reextracted": done - len(failures), "errors": len(failures)})


def run_analyze_chains(ctx, store, task, is_cancelled) -> None:
    path = task.params["path"]
    graph = build_codegraph(path, ctx.settings)
    chains = assemble_chains(graph, ctx.settings.codegraph_max_chain_depth)
    store.set_children_names(task.id, [c.entry for c in chains])
    if is_cancelled():
        return
    try:
        result = analyze_corpus(path, ctx.store, ctx.settings, ctx.graph)
    except Exception as exc:  # noqa: BLE001 - Fail Loud into the report
        store.update(task.id, failures=[{"name": str(path), "status": "error"}])
        raise
    failures = [{"name": e.get("function") or e.get("chain") or "?",
                 "status": "error"} for e in result.get("errors", [])]
    store.update(task.id, done=len(chains), failures=failures, result=result)


class _Cancelled(Exception):
    pass


RUNNERS = {
    "ingest": run_ingest,
    "synthesize": run_synthesize,
    "extract": run_extract,
    "analyze_chains": run_analyze_chains,
}
