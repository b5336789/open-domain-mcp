"""Shared FastAPI dependencies.

The context resolver lives here (rather than inside ``create_app``) so feature
routers in their own modules can depend on the same per-collection context cache
held on ``app.state``. Behaviour is unchanged from the original inline closure:

* a pinned ``app.state.context`` (used by tests / single-collection use) wins;
* otherwise the collection is taken from the ``collection`` query param, the
  ``X-Collection`` header, or the default, and contexts are cached per name.

Multi-tenancy (opt-in via ``Settings.multi_tenant``) namespaces the collection
as ``<tenant>::<collection>`` using the ``X-Tenant`` request header, so each
tenant's vector + graph data stay isolated by the existing per-collection
separation. With it off, resolution is exactly as before.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from ..config import get_settings
from ..context import Context

TENANT_SEPARATOR = "::"


def tenant_collection(tenant: str, collection: str) -> str:
    """Namespace ``collection`` under ``tenant`` for multi-tenant isolation."""
    return f"{tenant}{TENANT_SEPARATOR}{collection}"


def resolve_tenant(request: Request, settings) -> str | None:
    """Return the tenant id when multi-tenancy is enabled, else ``None``.

    Fail loud: when enabled, a missing/blank ``X-Tenant`` header is a 400 — we
    never silently fall back to a shared default that would leak data across
    tenants.
    """
    if not getattr(settings, "multi_tenant", False):
        return None
    tenant = (request.headers.get("x-tenant") or "").strip()
    if not tenant:
        raise HTTPException(
            status_code=400,
            detail="multi-tenant mode is on: the X-Tenant header is required",
        )
    return tenant


def get_ctx(request: Request) -> Context:
    state = request.app.state
    if getattr(state, "context", None) is not None:
        return state.context
    settings = get_settings()
    name = (
        request.query_params.get("collection")
        or request.headers.get("x-collection")
        or settings.collection_name
    )
    tenant = resolve_tenant(request, settings)
    if tenant is not None:
        name = tenant_collection(tenant, name)
    if name not in state.contexts:
        state.contexts[name] = state.context_factory(collection=name)
    return state.contexts[name]
