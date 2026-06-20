"""API-key authentication and per-view access control (RBAC).

These are the reusable building blocks; wiring them onto routes happens in the
app layer. A "principal" is a plain dict describing the caller::

    {"role": str, "views": tuple[str, ...], "key": str | None}

``views`` is either ``("*",)`` (all views) or the explicit views the key may
reach. When auth is disabled (the default) every caller is an anonymous
full-access principal, so existing behaviour is unchanged.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from ..config import get_settings

# Header carrying the caller's API key when auth is enabled.
API_KEY_HEADER = "X-API-Key"

# Wildcard marking a principal that may reach every view.
ALL_VIEWS = "*"

# Principal granted when auth is disabled: full access, no key.
ANONYMOUS_PRINCIPAL: dict = {"role": "anonymous", "views": (ALL_VIEWS,), "key": None}


def auth_dependency(request: Request) -> dict:
    """FastAPI dependency returning the authenticated principal.

    Auth OFF: returns the anonymous full-access principal.
    Auth ON: validates the ``X-API-Key`` header against the configured keys and
    raises ``HTTPException(401)`` when the key is missing or unknown.
    """
    settings = get_settings()
    if not settings.auth_enabled:
        # Return a fresh copy so callers can never mutate the shared default.
        return dict(ANONYMOUS_PRINCIPAL)

    key = request.headers.get(API_KEY_HEADER)
    if not key:
        raise HTTPException(status_code=401, detail="Missing API key")

    entry = settings.parsed_api_keys().get(key)
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return {"role": entry["role"], "views": entry["views"], "key": key}


def principal_allows_view(principal: dict, view: str) -> bool:
    """True when ``principal`` may access ``view`` (wildcard or explicit grant)."""
    views = principal.get("views", ())
    return ALL_VIEWS in views or view in views


def require_view_access(principal: dict, view: str) -> None:
    """Raise ``HTTPException(403)`` when ``principal`` may not access ``view``."""
    if not principal_allows_view(principal, view):
        raise HTTPException(
            status_code=403, detail=f"Access to view '{view}' is not permitted"
        )
