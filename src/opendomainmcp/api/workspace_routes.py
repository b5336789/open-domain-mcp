from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..context import Context
from ..quality import compute_readiness
from ..tasks.store import TaskStore
from .deps import get_ctx

router = APIRouter()


@router.get("/api/workspace/readiness")
def workspace_readiness(request: Request, ctx: Context = Depends(get_ctx)) -> dict:
    return compute_readiness(ctx, tasks=_task_rows(ctx, request.app.state))


def _task_rows(ctx: Context, app_state=None) -> list[dict]:
    store = getattr(app_state, "task_store", None) if app_state is not None else None
    try:
        if store is None:
            store = TaskStore(ctx.settings.data_dir)
        tasks = store.list()
    except Exception:  # noqa: BLE001 - readiness should degrade on bad task history
        return [{"status": "error"}]

    collection = ctx.store.stats().get("collection")
    rows: list[dict] = []
    try:
        for task in tasks:
            row = task.to_dict()
            if row.get("collection") == collection:
                rows.append(row)
    except Exception:  # noqa: BLE001 - readiness should degrade on bad task rows
        return [{"status": "error"}]
    return rows
