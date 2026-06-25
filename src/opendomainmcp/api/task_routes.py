from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from ..context import Context
from ..tasks.runners import RUNNERS
from ..tasks.store import TaskStore
from ..tasks.worker import TaskWorker
from .deps import get_ctx


class TaskCreate(BaseModel):
    type: str
    params: dict = Field(default_factory=dict)


def _title(type: str, params: dict) -> str:
    if type == "ingest":
        return f"Ingest {params.get('path', '')}"
    if type == "synthesize":
        return "Synthesize articles"
    if type == "extract":
        return f"Re-extract {params.get('source', '')}"
    return type


def register_task_routes(app, resolve_ctx) -> None:
    """resolve_ctx(collection) -> Context. Store + worker live on app.state."""

    def _store(ctx: Context) -> TaskStore:
        if getattr(app.state, "task_store", None) is None:
            app.state.task_store = TaskStore(ctx.settings.data_dir)
        return app.state.task_store

    def _worker(ctx: Context) -> TaskWorker:
        store = _store(ctx)
        if getattr(app.state, "task_worker", None) is None:
            def run_one(task, is_cancelled):
                tctx = resolve_ctx(task.collection)
                RUNNERS[task.type](tctx, store, task, is_cancelled)
            app.state.task_worker = TaskWorker(store, run_one)
            app.state.task_worker.start()
        return app.state.task_worker

    @app.post("/api/tasks")
    def create_task(body: TaskCreate, ctx: Context = Depends(get_ctx)):
        if body.type not in RUNNERS:
            raise HTTPException(status_code=400, detail=f"unknown task type {body.type!r}")
        store = _store(ctx)
        collection = ctx.store.stats().get("collection", "")
        task = store.create(body.type, _title(body.type, body.params),
                            collection, body.params)
        _worker(ctx).wake()
        return task.to_dict()

    @app.get("/api/tasks")
    def list_tasks(ctx: Context = Depends(get_ctx)):
        return {"tasks": [t.to_dict() for t in _store(ctx).list()]}

    @app.get("/api/tasks/{task_id}/children")
    def task_children(task_id: str, offset: int = 0, limit: int = 100,
                      ctx: Context = Depends(get_ctx)):
        store = _store(ctx)
        if store.get(task_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown task {task_id}")
        return store.read_children(task_id, offset, limit)

    @app.delete("/api/tasks/{task_id}")
    def cancel_task(task_id: str, ctx: Context = Depends(get_ctx)):
        store = _store(ctx)
        if store.get(task_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown task {task_id}")
        return {"cancelled": store.request_cancel(task_id)}

    @app.post("/api/tasks/clear")
    def clear_tasks(ctx: Context = Depends(get_ctx)):
        return {"cleared": _store(ctx).clear_finished()}
