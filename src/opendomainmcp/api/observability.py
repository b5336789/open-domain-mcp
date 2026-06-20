"""Observability helpers for the web API: structured logging, a rich health
payload, and request-level logging middleware.

These are intentionally framework-light helpers that ``app.py`` imports and
wires up. Health must degrade gracefully: a broken graph backend yields a
``"degraded"`` signal in the payload, never an exception.
"""

from __future__ import annotations

import logging
import os
import time
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"
_LOGGER_NAME = "opendomainmcp.api"
ENV_LOG_LEVEL = "ODM_LOG_LEVEL"

logger = logging.getLogger(_LOGGER_NAME)


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger once with a concise structured format.

    Idempotent: a sentinel attribute on the root logger guards against adding
    duplicate handlers if called more than once (e.g. by tests and app start).
    The ``ODM_LOG_LEVEL`` env var, when set, overrides the ``level`` argument.
    """
    resolved = os.environ.get(ENV_LOG_LEVEL, level).upper()
    root = logging.getLogger()

    if getattr(root, "_odm_logging_configured", False):
        # Already configured: only refresh the level, never re-add handlers.
        root.setLevel(resolved)
        return

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    root.addHandler(handler)
    root.setLevel(resolved)
    root._odm_logging_configured = True  # type: ignore[attr-defined]


def _package_version() -> str:
    """Return the installed package version, or ``"unknown"`` if unresolved."""
    try:
        return version("opendomainmcp")
    except PackageNotFoundError:
        return "unknown"
    except Exception:  # noqa: BLE001 - version lookup must never break health
        return "unknown"


def _graph_status(ctx: Any) -> str:
    """Probe graph connectivity with a cheap, guarded call.

    Returns ``"ok"`` if a tiny query succeeds, ``"unavailable"`` otherwise.
    Never raises: health must survive a degraded graph backend.
    """
    try:
        ctx.graph.list_workflows(limit=1)
        return "ok"
    except Exception:  # noqa: BLE001 - degraded graph is reported, not raised
        return "unavailable"


def health_payload(ctx: Any) -> dict[str, Any]:
    """Build a rich health dict from the runtime context.

    Always returns ``status == "ok"``; a degraded graph is surfaced via the
    ``graph`` field rather than by failing the health check.
    """
    stats = ctx.store.stats()
    return {
        "status": "ok",
        "collection": ctx.settings.collection_name,
        "documents": stats.get("count", 0),
        "embedder": stats.get("embedder", "unknown"),
        "graph": _graph_status(ctx),
        "version": _package_version(),
    }


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log method, path, status, and duration for each HTTP request at INFO.

    Lightweight: it times the downstream handler and logs once, always
    returning the original response untouched.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "%s %s -> %s (%sms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
