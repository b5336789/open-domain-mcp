"""Source registry routes.

Surfaces which sources have been ingested into the active knowledge base and
lets an operator delete every chunk for a source. Lives in its own router so it
can be mounted via ``app.include_router(source_routes.router)`` and depend on the
shared per-collection context through ``get_ctx``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..context import Context
from .deps import get_ctx

router = APIRouter()


class SourceDelete(BaseModel):
    source: str


@router.get("/api/sources")
def list_sources(ctx: Context = Depends(get_ctx)) -> dict:
    return {"sources": ctx.store.list_sources()}


@router.delete("/api/sources")
def delete_source(body: SourceDelete, ctx: Context = Depends(get_ctx)) -> dict:
    source = body.source
    if not source:
        raise HTTPException(status_code=400, detail="source must be non-empty")

    # Grab chunk ids before deleting so the graph slice can be cleaned up too;
    # this read primitive is cheap (a filtered get returning ids only).
    chunk_ids = ctx.store.get_ids_for_source(source)
    if not chunk_ids:
        raise HTTPException(status_code=404, detail=f"unknown source {source!r}")

    ctx.graph.delete_for_chunks(chunk_ids)
    deleted = ctx.store.delete_ids(chunk_ids)
    return {"deleted": deleted, "source": source}
