"""Publish role-specific MCP views as live HTTP (SSE) endpoints.

The MCP Builder UI used to only print a "start server" command. This module
mounts each view's :class:`~mcp.server.fastmcp.FastMCP` server as an ASGI sub-app
under ``/mcp/{view}`` (SSE transport) and exposes a small publish registry so the
UI can publish / unpublish views and discover their absolute endpoint URLs.

Mounting builds no context and opens no database connection: each view builds its
runtime context lazily, per request. A view that fails to mount is logged and
skipped so one bad view never aborts application startup (Fail-Loud via log).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..server import build_view_server
from ..views import VIEW_NAMES, VIEWS

logger = logging.getLogger(__name__)

router = APIRouter()


def _mount_path(view: str) -> str:
    """Return the canonical mount path for a view's SSE app."""
    return f"/mcp/{view}"


def mount_mcp_apps(app) -> dict[str, str]:
    """Mount every view's SSE app under ``/mcp/{view}``.

    Returns a mapping of ``{view: "/mcp/{view}"}`` for views mounted
    successfully. Each mount is isolated in a ``try/except`` so a single failing
    view is logged and skipped rather than aborting startup.
    """
    mounted: dict[str, str] = {}
    for view in VIEW_NAMES:
        path = _mount_path(view)
        try:
            app.mount(path, build_view_server(view).sse_app())
            mounted[view] = path
        except Exception:  # surface the failure but keep the app starting
            logger.exception("failed to mount MCP view %r at %s", view, path)
    return mounted


def published_set(app) -> set[str]:
    """Return the lazily-initialised set of published view names on app.state."""
    if getattr(app.state, "published_mcps", None) is None:
        app.state.published_mcps = set()
    return app.state.published_mcps


class PublishRequest(BaseModel):
    view: str


def _entry(request: Request, view: str, published: set[str]) -> dict:
    """Build the registry entry for a single view."""
    path = _mount_path(view)
    base = str(request.base_url).rstrip("/")
    return {
        "view": view,
        "title": VIEWS[view].title,
        "path": path,
        "published": view in published,
        "url": f"{base}{path}",
    }


@router.get("/api/mcp/endpoints")
def list_endpoints(request: Request) -> list[dict]:
    """List every view with its mount path, publish state and absolute URL."""
    published = published_set(request.app)
    return [_entry(request, view, published) for view in VIEW_NAMES]


@router.post("/api/mcp/endpoints")
def publish_endpoint(body: PublishRequest, request: Request) -> dict:
    """Mark a view as published. 404 for an unknown view."""
    if body.view not in VIEW_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown view {body.view!r}")
    published = published_set(request.app)
    published.add(body.view)
    return _entry(request, body.view, published)


@router.delete("/api/mcp/endpoints/{view}")
def unpublish_endpoint(view: str, request: Request) -> dict:
    """Mark a view as unpublished."""
    published_set(request.app).discard(view)
    return {"unpublished": view}
