from __future__ import annotations

from fastapi import APIRouter, Depends

from ..context import Context
from ..quality import compute_readiness
from ..tasks.store import TaskStore
from .deps import get_ctx

router = APIRouter()


@router.get("/api/workspace/readiness")
def workspace_readiness(ctx: Context = Depends(get_ctx)) -> dict:
    return compute_readiness(ctx, tasks=_task_rows(ctx))


def _task_rows(ctx: Context) -> list[dict]:
    store = TaskStore(ctx.settings.data_dir)
    return [task.to_dict() for task in store.list()]
