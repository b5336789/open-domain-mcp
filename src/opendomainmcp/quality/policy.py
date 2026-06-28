from __future__ import annotations

from ..config import Settings

SUPPORTED_SEARCH_MODES = {"hybrid", "vector"}


def build_policy_evidence(settings: Settings) -> dict:
    approved_only = bool(settings.retrieve_approved_only)
    search_mode = str(settings.search_mode or "")
    rerank = bool(settings.rerank_enabled)
    details = [
        "approved-only on" if approved_only else "approved-only off",
        f"search mode {search_mode or 'unset'}",
        "rerank on" if rerank else "rerank off",
        *_auth_details(settings),
    ]

    if not approved_only:
        return {
            "id": "policy",
            "gate": "Policy",
            "status": "needs_review",
            "score": 60,
            "summary": "Published MCP views may include unapproved knowledge.",
            "details": details,
            "action": "Enable approved-only retrieval before publishing.",
        }

    if search_mode not in SUPPORTED_SEARCH_MODES:
        return {
            "id": "policy",
            "gate": "Policy",
            "status": "needs_review",
            "score": 70,
            "summary": f"Search mode {search_mode or 'unset'} is not publish-safe.",
            "details": details,
            "action": "Select hybrid or vector search mode.",
        }

    return {
        "id": "policy",
        "gate": "Policy",
        "status": "ready",
        "score": 100,
        "summary": f"Published MCP views use approved-only {search_mode} retrieval.",
        "details": details,
        "action": "Policy gate is clear.",
    }


def _auth_details(settings: Settings) -> list[str]:
    if not settings.auth_enabled:
        return ["auth disabled"]
    parsed = settings.parsed_api_keys()
    if not parsed:
        return ["auth enabled", "0 API keys configured", "no view scopes configured"]
    scoped = any("*" not in entry["views"] for entry in parsed.values())
    return [
        "auth enabled",
        f"{len(parsed)} API keys configured",
        "view scopes configured" if scoped else "wildcard access only",
    ]
