"""FastAPI layer for the web dashboard.

A thin HTTP wrapper over the shared :class:`~opendomainmcp.context.Context`
(pipeline + store). No business logic lives here. Ingestion progress is streamed
to the browser via Server-Sent Events.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..context import Context, build_context
from . import insight_routes, mcp_endpoints, source_routes
from .task_routes import register_task_routes
from .auth import auth_dependency, require_view_access
from .deps import get_ctx
from .observability import RequestLoggingMiddleware, health_payload, setup_logging

STATIC_DIR = Path(__file__).parent / "static"

# Serialize article synthesis: it is LLM-rate-limited and writes a shared
# collection, so one run at a time (a double-click or concurrent request gets a
# 409 rather than two competing runs). Process-local; matches the single-process
# web server. See the /api/synthesize/stream endpoint.
_synthesize_lock = threading.Lock()

# Live background-ingest jobs (job_id -> Checkpoint), so polling and cancellation
# reach the running job's in-memory state. The checkpoint file on disk is the
# durable record; the registry holds the live object while the run is active.
_ingest_jobs: dict = {}


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    kind: str | None = None
    language: str | None = None
    symbol: str | None = None
    source_contains: str | None = None


class AskRequest(BaseModel):
    query: str
    top_k: int = 6


class ItemPatch(BaseModel):
    metadata: dict


class ItemCreate(BaseModel):
    """Manually authored knowledge added through the review UI."""

    text: str
    source: str = "manual"
    knowledge_type: str = ""
    audience: list[str] = []
    tags: list[str] = []
    summary: str = ""


class SettingsPatch(BaseModel):
    values: dict


class CollectionCreate(BaseModel):
    name: str


class SimulateRequest(BaseModel):
    """Agent Simulator: run a task against one MCP view's tools."""

    view: str
    query: str
    top_k: int = 5


def create_app(context: Context | None = None, context_factory=build_context) -> FastAPI:
    app = FastAPI(title="openDomainMcp")
    setup_logging()
    app.add_middleware(RequestLoggingMiddleware)
    app.state.context = context          # pinned single context (tests / single use)
    app.state.contexts = {}              # per-collection cache (real multi-collection)
    app.state.context_factory = context_factory

    # -- status & search ------------------------------------------------
    @app.get("/api/health")
    def health(ctx: Context = Depends(get_ctx)):
        return health_payload(ctx)

    @app.get("/api/stats")
    def stats(ctx: Context = Depends(get_ctx)):
        data = ctx.store.stats()
        data["data_dir"] = str(ctx.settings.data_dir)
        data["extract_knowledge"] = ctx.settings.extract_knowledge
        return data

    @app.post("/api/search")
    def search(req: SearchRequest, ctx: Context = Depends(get_ctx)):
        from ..store import build_where
        from ..retrieval import search_unified

        filters = {"kind": req.kind, "language": req.language, "symbol": req.symbol}
        if ctx.settings.retrieve_approved_only:
            filters["review_status"] = "approved"
        where = build_where(filters)
        results = search_unified(
            ctx.store, req.query, top_k=req.top_k, where=where,
            mode=ctx.settings.search_mode, settings=ctx.settings,
            source_contains=req.source_contains,
        )
        out = [r.to_dict() for r in results]
        insight_routes.record_retrieval(ctx, "search", req.query, out)
        return out

    @app.post("/api/ask")
    def ask(req: AskRequest, ctx: Context = Depends(get_ctx)):
        from ..query import AnswerError, answer_question

        try:
            result = answer_question(req.query, ctx.store, ctx.settings, top_k=req.top_k, graph=ctx.graph)
        except AnswerError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        citations = result.get("citations", []) if isinstance(result, dict) else []
        insight_routes.record_retrieval(
            ctx, "ask", req.query,
            [{"score": c.get("score", 0.0), "metadata": {}} for c in citations],
        )
        return result

    @app.get("/api/ask/stream")
    async def ask_stream(query: str, top_k: int = 6, ctx: Context = Depends(get_ctx)):
        from ..query import AnswerError, answer_question_stream

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def run():
            # The model stream is blocking, so iterate it off the event loop and
            # bridge each event back via the queue.
            try:
                for event in answer_question_stream(
                    query, ctx.store, ctx.settings, top_k=top_k, graph=ctx.graph
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except AnswerError as exc:  # surface to the UI (Fail Loud)
                loop.call_soon_threadsafe(
                    queue.put_nowait, {"type": "error", "detail": str(exc)}
                )
            loop.call_soon_threadsafe(queue.put_nowait, None)

        async def events():
            task = asyncio.create_task(asyncio.to_thread(run))
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield {"event": event["type"], "data": json.dumps(event)}
            finally:
                await task

        return EventSourceResponse(events())

    # -- ingestion ------------------------------------------------------
    @app.post("/api/upload")
    async def upload(files: list[UploadFile] = File(...), ctx: Context = Depends(get_ctx)):
        stage = ctx.settings.data_dir / "uploads" / uuid.uuid4().hex
        stage.mkdir(parents=True, exist_ok=True)
        limit = ctx.settings.max_upload_mb * 1024 * 1024
        names = []
        for f in files:
            dest = stage / Path(f.filename or "upload").name
            # Stream to disk in chunks so a huge upload never lands in memory,
            # and abort once it exceeds the configured limit (Fail Loud).
            written = 0
            with dest.open("wb") as out:
                while chunk := await f.read(1024 * 1024):
                    written += len(chunk)
                    if written > limit:
                        out.close()
                        dest.unlink(missing_ok=True)
                        raise HTTPException(
                            status_code=413,
                            detail=f"{dest.name} exceeds the {ctx.settings.max_upload_mb} MB upload limit",
                        )
                    out.write(chunk)
            names.append(dest.name)
        return {"path": str(stage), "files": names}

    @app.get("/api/ingest/stream")
    async def ingest_stream(path: str, sync: bool = False, ctx: Context = Depends(get_ctx)):
        # Reject paths that escape the configured ingest root before streaming.
        if ctx.settings.ingest_root is not None:
            from ..ingest.pipeline import PathNotAllowedError, _resolve_within

            try:
                _resolve_within(Path(path), ctx.settings.ingest_root)
            except PathNotAllowedError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def progress(event):
            loop.call_soon_threadsafe(queue.put_nowait, event)

        async def run():
            try:
                report = await asyncio.to_thread(
                    ctx.pipeline.ingest_path, path, progress, sync
                )
                queue.put_nowait({"stage": "report", **report.to_dict()})
            except Exception as exc:  # surface failures to the UI (Fail Loud)
                queue.put_nowait({"stage": "error", "path": path, "detail": repr(exc)})
            queue.put_nowait(None)

        async def events():
            task = asyncio.create_task(run())
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield {"event": event["stage"], "data": json.dumps(event)}
            finally:
                await task

        return EventSourceResponse(events())

    @app.get("/api/synthesize/stream")
    async def synthesize_stream(limit: int | None = None, dry_run: bool = False,
                                ctx: Context = Depends(get_ctx)):
        from ..synthesis import synthesize_articles

        # One run at a time: reject (409) rather than launch a competing run.
        if not _synthesize_lock.acquire(blocking=False):
            raise HTTPException(
                status_code=409, detail="A synthesis run is already in progress"
            )

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def progress(event):
            loop.call_soon_threadsafe(queue.put_nowait, event)

        async def run():
            try:
                report = await asyncio.to_thread(
                    synthesize_articles, ctx.store, ctx.settings,
                    graph=ctx.graph, limit=limit, dry_run=dry_run,
                    on_event=progress,
                )
                queue.put_nowait({"stage": "report", **report.to_dict()})
            except Exception as exc:  # surface failures to the UI (Fail Loud)
                queue.put_nowait({"stage": "error", "detail": repr(exc)})
            finally:
                _synthesize_lock.release()
                queue.put_nowait(None)

        async def events():
            task = asyncio.create_task(run())
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield {"event": event["stage"], "data": json.dumps(event)}
            finally:
                await task

        return EventSourceResponse(events())

    # -- async (resumable) ingest --------------------------------------
    @app.post("/api/ingest/async")
    def ingest_async(path: str, sync: bool = False,
                     ctx: Context = Depends(get_ctx)):
        from ..ingest.checkpoint import Checkpoint, extractor_signature
        from ..ingest.pipeline import PathNotAllowedError, _resolve_within

        # Reject paths that escape the configured ingest root before scheduling.
        if ctx.settings.ingest_root is not None:
            try:
                _resolve_within(Path(path), ctx.settings.ingest_root)
            except PathNotAllowedError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

        # Key the job by collection+path so resubmitting the same path resumes an
        # interrupted run (斷點續傳) instead of starting an unrelated job. A run
        # that already finished ("done") starts fresh, so content changes are
        # re-ingested rather than skipped.
        collection = ctx.store.stats().get("collection", "")
        job_id = hashlib.sha256(f"{collection}\n{path}".encode("utf-8")).hexdigest()[:32]

        active = _ingest_jobs.get(job_id)
        if active is not None and active.status in ("queued", "running"):
            # Already in progress — don't launch a competing run on the same job.
            return {"job_id": job_id, "status": active.status}

        cp = None
        cp_path = Checkpoint.directory(ctx.settings.data_dir) / f"{job_id}.json"
        if cp_path.exists():
            prev = Checkpoint.load(cp_path)
            if prev.status != "done":
                # Resume an interrupted/errored/cancelled run: keep completed_files
                # so they are skipped. (A stale "running" left by a crash resumes.)
                cp = prev
                cp.cancel_requested = False
                cp.report = None
                cp.status = "queued"
        if cp is None:
            cp = Checkpoint.new(ctx.settings.data_dir, job_id, path, sync,
                                extractor_signature(ctx.settings))
        cp.save()
        _ingest_jobs[job_id] = cp

        # Run in a daemon thread so the request returns immediately and the work
        # continues independently of the event loop. The checkpoint (in the
        # registry + on disk) is the status source polled by GET .../jobs/{id}.
        def run():
            try:
                cp.status = "running"
                cp.save()
                report = ctx.pipeline.ingest_path(path, None, sync, None, cp)
                cp.update_from_report(report)
                cp.report = report.to_dict()
                cp.status = "cancelled" if cp.cancelled else "done"
            except Exception as exc:  # surface failures via the job record (Fail Loud)
                cp.status = "error"
                cp.errors = cp.errors + [{"path": path, "error": repr(exc)}]
            finally:
                cp.save()

        threading.Thread(target=run, name=f"ingest-{job_id}", daemon=True).start()
        return {"job_id": job_id, "status": cp.status}

    def _load_job(job_id: str, ctx: Context):
        cp = _ingest_jobs.get(job_id)
        if cp is not None:
            return cp
        # Fall back to the on-disk checkpoint (e.g. after a server restart).
        from ..ingest.checkpoint import Checkpoint

        path = Checkpoint.directory(ctx.settings.data_dir) / f"{job_id}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"unknown ingest job {job_id}")
        return Checkpoint.load(path)

    @app.get("/api/ingest/jobs/{job_id}")
    def ingest_job(job_id: str, ctx: Context = Depends(get_ctx)):
        return _load_job(job_id, ctx).to_status()

    @app.delete("/api/ingest/jobs/{job_id}")
    def cancel_ingest_job(job_id: str, ctx: Context = Depends(get_ctx)):
        cp = _ingest_jobs.get(job_id)
        if cp is None:
            raise HTTPException(
                status_code=404, detail=f"no active ingest job {job_id} to cancel"
            )
        cp.request_cancel()
        cp.save()
        return {"job_id": job_id, "status": "cancelling"}

    # -- browse, edit & review -----------------------------------------
    @app.get("/api/articles")
    def list_articles(limit: int = 200, offset: int = 0,
                      ctx: Context = Depends(get_ctx)):
        arts = ctx.store.sibling(f"{ctx.store.stats()['collection']}__articles")
        out = []
        for row in arts.get_items(limit=limit, offset=offset):
            meta = row.get("metadata") or {}
            sources = [s.strip() for s in str(meta.get("sources", "")).split(" | ")
                       if s.strip()]
            out.append({
                "id": row["id"],
                "title": meta.get("title") or meta.get("topic") or row["id"],
                "topic": meta.get("topic", ""),
                "business_relevance": float(meta.get("business_relevance", 0) or 0),
                "cross_validated": bool(meta.get("cross_validated", False)),
                "sources": sources,
                "body": row.get("text", ""),
            })
        return out

    @app.get("/api/items")
    def list_items(limit: int = 50, offset: int = 0, kind: str | None = None,
                   review_status: str | None = None,
                   knowledge_type: str | None = None,
                   ctx: Context = Depends(get_ctx)):
        from ..store import build_where

        where = build_where({
            "kind": kind, "review_status": review_status,
            "knowledge_type": knowledge_type,
        })
        return ctx.store.get_items(limit=limit, offset=offset, where=where)

    @app.post("/api/items")
    def create_item(body: ItemCreate, ctx: Context = Depends(get_ctx)):
        from ..graph.builder import build_graph
        from ..models import Chunk, KnowledgeUnit

        # Manually authored knowledge is trusted, so it is born approved.
        knowledge = KnowledgeUnit(
            summary=body.summary or body.text[:160],
            knowledge_type=body.knowledge_type,
            audience=body.audience,
            tags=body.tags,
            confidence=1.0,
            review_status="approved",
        )
        chunk = Chunk(text=body.text, source=body.source, kind="text",
                      knowledge=knowledge)
        ctx.store.upsert([chunk])
        if chunk.knowledge and not chunk.knowledge.is_empty():
            entities, edges = build_graph(chunk.knowledge, chunk.id)
            ctx.graph.upsert_entities(entities)
            ctx.graph.upsert_edges(edges)
        return ctx.store.get_item(chunk.id)

    @app.post("/api/items/{item_id}/approve")
    def approve_item(item_id: str, ctx: Context = Depends(get_ctx)):
        if not ctx.store.update_metadata(item_id, {"review_status": "approved"}):
            raise HTTPException(status_code=404, detail="item not found")
        return ctx.store.get_item(item_id)

    @app.post("/api/items/{item_id}/reject")
    def reject_item(item_id: str, ctx: Context = Depends(get_ctx)):
        if not ctx.store.update_metadata(item_id, {"review_status": "rejected"}):
            raise HTTPException(status_code=404, detail="item not found")
        return ctx.store.get_item(item_id)

    @app.get("/api/items/{item_id}")
    def get_item(item_id: str, ctx: Context = Depends(get_ctx)):
        item = ctx.store.get_item(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        return item

    @app.patch("/api/items/{item_id}")
    def update_item(item_id: str, patch: ItemPatch, ctx: Context = Depends(get_ctx)):
        if not ctx.store.update_metadata(item_id, patch.metadata):
            raise HTTPException(status_code=404, detail="item not found")
        return ctx.store.get_item(item_id)

    @app.delete("/api/items/{item_id}")
    def delete_item(item_id: str, ctx: Context = Depends(get_ctx)):
        if not ctx.store.delete_item(item_id):
            raise HTTPException(status_code=404, detail="item not found")
        ctx.graph.delete_for_chunks([item_id])
        return {"deleted": item_id}

    # -- settings -------------------------------------------------------
    @app.get("/api/settings")
    def read_settings(ctx: Context = Depends(get_ctx)):
        return {
            "editable": ctx.settings.editable_dict(),
            "collection": ctx.settings.collection_name,
            "embedder_backend": ctx.settings.embedder_backend,
            "data_dir": str(ctx.settings.data_dir),
        }

    @app.patch("/api/settings")
    def patch_settings(patch: SettingsPatch, ctx: Context = Depends(get_ctx)):
        try:
            ctx.settings.save_overrides(patch.values)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # Drop cached per-collection contexts so later requests pick up new settings.
        app.state.contexts.clear()
        return JSONResponse({"updated": list(patch.values)})

    # -- MCP views & agent simulator ------------------------------------
    @app.get("/api/views")
    def list_views():
        from ..views import VIEWS

        return {
            name: {
                "title": spec.title,
                "purpose": spec.purpose,
                "tools": [
                    {"name": t.name, "description": t.description,
                     "filters": t.filters, "default_top_k": t.default_top_k}
                    for t in spec.tools
                ],
            }
            for name, spec in VIEWS.items()
        }

    @app.post("/api/simulate")
    def simulate(req: SimulateRequest, ctx: Context = Depends(get_ctx),
                 principal: dict = Depends(auth_dependency)):
        from ..views import VIEWS, run_view_tool

        spec = VIEWS.get(req.view)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"unknown view {req.view!r}")
        # RBAC: a scoped API key may only simulate views it is granted (no-op when
        # auth is disabled — the anonymous principal has full access).
        require_view_access(principal, req.view)

        tools_out, all_results, seen = [], [], set()
        for tool in spec.tools:
            results = run_view_tool(ctx, tool, req.query, req.top_k)
            tools_out.append({"tool": tool.name, "results": results})
            for r in results:
                if r["id"] not in seen:
                    seen.add(r["id"])
                    all_results.append(r)

        scores = [r["score"] for r in all_results]
        types = sorted({
            r["metadata"].get("knowledge_type", "") for r in all_results
        } - {""})
        grounding = {
            "hits": len(all_results),
            "avg_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
            "knowledge_types": types,
        }
        insight_routes.record_retrieval(ctx, "search", req.query, all_results)
        return {"view": req.view, "tools": tools_out, "grounding": grounding}

    # -- collections (knowledge bases) ----------------------------------
    @app.get("/api/collections")
    def list_collections(ctx: Context = Depends(get_ctx)):
        return {
            "active": ctx.store.stats()["collection"],
            "collections": ctx.store.list_collections(),
        }

    @app.post("/api/collections")
    def create_collection(body: CollectionCreate, ctx: Context = Depends(get_ctx)):
        ctx.store.create_collection(body.name)
        return {"created": body.name}

    @app.delete("/api/collections/{name}")
    def delete_collection(name: str, ctx: Context = Depends(get_ctx)):
        ctx.store.drop_collection(name)
        ctx.graph.delete_collection(name)
        app.state.contexts.pop(name, None)
        return {"deleted": name}

    # -- graph query (pure read, no LLM) --------------------------------
    @app.get("/api/graph/entity/{name}")
    def graph_entity(name: str, ctx: Context = Depends(get_ctx)):
        result = ctx.graph.neighbors(name)
        if result["entity"] is None:
            return JSONResponse(status_code=404,
                                content={"error": f"entity not found: {name}"})
        return result

    @app.get("/api/graph/entities")
    def graph_entities(type: str | None = None, q: str | None = None, limit: int = 50,
                       ctx: Context = Depends(get_ctx)):
        return {"items": ctx.graph.list_entities(type=type, q=q, limit=limit)}

    @app.get("/api/graph/workflow/{name}")
    def graph_workflow(name: str, ctx: Context = Depends(get_ctx)):
        result = ctx.graph.get_workflow(name)
        if result is None:
            return JSONResponse(status_code=404,
                                content={"error": f"workflow not found: {name}"})
        return result

    @app.get("/api/graph/workflows")
    def graph_workflows(q: str | None = None, limit: int = 50,
                        ctx: Context = Depends(get_ctx)):
        return {"items": ctx.graph.list_workflows(q=q, limit=limit)}

    # -- pre-execution advisor, metrics, source registry ----------------
    app.include_router(insight_routes.router)
    app.include_router(source_routes.router, dependencies=[Depends(auth_dependency)])

    # -- dynamic MCP endpoints (real HTTP/SSE transports) ---------------
    # Mounts /mcp/{view} SSE apps and the publish registry. Must be registered
    # before the catch-all static mount at "/" below, or it would shadow them.
    mcp_endpoints.mount_mcp_apps(app)
    app.include_router(mcp_endpoints.router, dependencies=[Depends(auth_dependency)])

    # -- task center (background jobs) ----------------------------------
    def _resolve_ctx(collection):
        # Reuse the same per-collection context resolution as get_ctx: a pinned
        # context (tests) ignores the collection; otherwise build per collection.
        if app.state.context is not None:
            return app.state.context
        cached = app.state.contexts.get(collection)
        if cached is None:
            cached = app.state.context_factory(collection=collection)
            app.state.contexts[collection] = cached
        return cached

    register_task_routes(app, _resolve_ctx)

    # -- static SPA (built frontend), if present ------------------------
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


app = create_app()


def main() -> None:
    import os

    import uvicorn

    host = os.environ.get("ODM_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("ODM_WEB_PORT", "8000"))
    uvicorn.run("opendomainmcp.api.app:app", host=host, port=port)


if __name__ == "__main__":
    main()
