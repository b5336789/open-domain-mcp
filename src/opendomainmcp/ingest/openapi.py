"""OpenAPI / Swagger parsing.

API specs are not prose, so splitting them by character windows loses structure.
Instead we emit one chunk per operation (method + path), pre-classified as
``knowledge_type="API"`` with the ``operationId`` as the symbol, then feed those
chunks through the normal embed/store flow. Pre-classified chunks skip the LLM
extractor (see :meth:`Pipeline._extract_one`).
"""

from __future__ import annotations

import json
from typing import Optional

from ..models import Chunk, KnowledgeUnit

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def parse_spec(text: str) -> Optional[dict]:
    """Parse ``text`` as JSON or YAML, returning a dict or ``None``."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        import yaml

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            return None
    return data if isinstance(data, dict) else None


def looks_like_openapi(data) -> bool:
    return (
        isinstance(data, dict)
        and ("openapi" in data or "swagger" in data)
        and isinstance(data.get("paths"), dict)
    )


def _operation_text(method: str, path: str, op: dict) -> str:
    parts = [f"{method.upper()} {path}"]
    for key in ("summary", "description"):
        value = op.get(key)
        if value:
            parts.append(str(value).strip())
    params = [
        p.get("name", "") for p in op.get("parameters", [])
        if isinstance(p, dict) and p.get("name")
    ]
    if params:
        parts.append("Parameters: " + ", ".join(params))
    responses = op.get("responses")
    if isinstance(responses, dict) and responses:
        parts.append("Responses: " + ", ".join(str(c) for c in responses))
    return "\n".join(parts)


def split_openapi(text: str, source: str) -> list[Chunk]:
    """Build one API-typed chunk per operation in an OpenAPI/Swagger document."""
    spec = parse_spec(text)
    if not looks_like_openapi(spec):
        return []
    chunks: list[Chunk] = []
    for path, ops in spec.get("paths", {}).items():
        if not isinstance(ops, dict):
            continue
        # Path-level tags/summary may apply to every operation under the path.
        for method, op in ops.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(op, dict):
                continue
            label = f"{method.upper()} {path}"
            tags = [str(t) for t in op.get("tags", []) if t]
            knowledge = KnowledgeUnit(
                summary=str(op.get("summary") or "").strip() or label,
                knowledge_type="API",
                audience=["engineering"],
                tags=tags,
                confidence=1.0,
            )
            chunks.append(Chunk(
                text=_operation_text(method, path, op),
                source=source,
                kind="text",
                symbol=op.get("operationId") or label,
                knowledge=knowledge,
            ))
    return chunks
