"""FastAPI layer for the web dashboard.

A thin HTTP wrapper over the shared :class:`~opendomainmcp.context.Context`
(pipeline + store). No business logic lives here. Ingestion progress is streamed
to the browser via Server-Sent Events.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..config import get_settings
from ..context import Context, build_context

STATIC_DIR = Path(__file__).parent / "static"


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


class SettingsPatch(BaseModel):
    values: dict


class CollectionCreate(BaseModel):
    name: str


def create_app(context: Context | None = None, context_factory=build_context) -> FastAPI:
    app = FastAPI(title="openDomainMcp")
    app.state.context = context          # pinned single context (tests / single use)
    app.state.contexts = {}              # per-collection cache (real multi-collection)
    app.state.context_factory = context_factory

    def get_ctx(request: Request) -> Context:
        if app.state.context is not None:
            return app.state.context
        name = (
            request.query_params.get("collection")
            or request.headers.get("x-collection")
            or get_settings().collection_name
        )
        if name not in app.state.contexts:
            app.state.contexts[name] = app.state.context_factory(collection=name)
        return app.state.contexts[name]

    # -- status & search ------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/stats")
    def stats(ctx: Context = Depends(get_ctx)):
        data = ctx.store.stats()
        data["data_dir"] = str(ctx.settings.data_dir)
        data["extract_knowledge"] = ctx.settings.extract_knowledge
        return data

    @app.post("/api/search")
    def search(req: SearchRequest, ctx: Context = Depends(get_ctx)):
        from ..store import build_where

        where = build_where({"kind": req.kind, "language": req.language, "symbol": req.symbol})
        results = ctx.store.search(
            req.query, top_k=req.top_k, where=where,
            mode=ctx.settings.search_mode, source_contains=req.source_contains,
        )
        return [r.to_dict() for r in results]

    @app.post("/api/ask")
    def ask(req: AskRequest, ctx: Context = Depends(get_ctx)):
        from ..query import AnswerError, answer_question

        try:
            return answer_question(req.query, ctx.store, ctx.settings, top_k=req.top_k)
        except AnswerError as exc:
            raise HTTPException(status_code=503, detail=str(exc))

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

    # -- browse & edit --------------------------------------------------
    @app.get("/api/items")
    def list_items(limit: int = 50, offset: int = 0, kind: str | None = None,
                   ctx: Context = Depends(get_ctx)):
        where = {"kind": kind} if kind else None
        return ctx.store.get_items(limit=limit, offset=offset, where=where)

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
        app.state.contexts.pop(name, None)
        return {"deleted": name}

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
