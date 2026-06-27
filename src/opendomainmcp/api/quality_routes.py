from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..context import Context
from ..quality import compute_quality_evidence
from .deps import get_ctx
from .workspace_routes import _task_rows

router = APIRouter()


@router.get("/api/quality/evidence")
def quality_evidence(request: Request, ctx: Context = Depends(get_ctx)) -> dict:
    return compute_quality_evidence(ctx, tasks=_task_rows(ctx, request.app.state))
