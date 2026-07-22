"""Download endpoint for finished export tasks. Task creation goes through the
generic POST /api/tasks with type "export" like every other background job."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..context import Context
from .deps import get_ctx

router = APIRouter()


@router.get("/api/export/{task_id}/download")
def download_export(task_id: str, ctx: Context = Depends(get_ctx)):
    zip_path = Path(ctx.settings.data_dir) / "exports" / f"{task_id}.zip"
    if not zip_path.is_file():
        raise HTTPException(status_code=404, detail="export not found")
    return FileResponse(zip_path, media_type="application/zip",
                        filename="knowledge-export.zip")
