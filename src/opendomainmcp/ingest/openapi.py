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

# Cap recursion so cyclic / deeply nested $refs cannot infinite-loop.
_MAX_REF_DEPTH = 8


def _resolve_ref(spec: dict, ref: str):
    """Resolve a local JSON-pointer ``$ref`` (e.g. ``#/components/schemas/Foo``).

    Only same-document refs are supported; external refs return ``None``.
    """
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node = spec
    for raw in ref[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        else:
            return None
    return node


def _collect_field_names(spec: dict, node, names: list[str], seen: set[str], depth: int) -> None:
    """Walk a schema/parameter/body node, appending referenced field names.

    ``seen`` tracks visited ``$ref`` pointers and ``depth`` caps recursion so
    cyclic refs terminate.
    """
    if depth > _MAX_REF_DEPTH or node is None:
        return
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            if ref in seen:
                return
            seen.add(ref)
            _collect_field_names(spec, _resolve_ref(spec, ref), names, seen, depth + 1)
        props = node.get("properties")
        if isinstance(props, dict):
            for prop_name, prop_schema in props.items():
                if prop_name not in names:
                    names.append(prop_name)
                _collect_field_names(spec, prop_schema, names, seen, depth + 1)
        for key in ("items", "additionalProperties", "schema"):
            if isinstance(node.get(key), dict):
                _collect_field_names(spec, node[key], names, seen, depth + 1)
        for key in ("allOf", "anyOf", "oneOf"):
            for sub in node.get(key, []) or []:
                _collect_field_names(spec, sub, names, seen, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _collect_field_names(spec, item, names, seen, depth + 1)


def _deref(spec: dict, node):
    """Return ``node`` with a single top-level ``$ref`` resolved (one hop)."""
    if isinstance(node, dict) and isinstance(node.get("$ref"), str):
        resolved = _resolve_ref(spec, node["$ref"])
        if isinstance(resolved, dict):
            return resolved
    return node


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


def _schema_fields(spec: dict, node) -> list[str]:
    """Return field names reachable from a (possibly ``$ref``'d) schema node."""
    names: list[str] = []
    _collect_field_names(spec, node, names, set(), 0)
    return names


def _operation_text(spec: dict, method: str, path: str, op: dict) -> str:
    parts = [f"{method.upper()} {path}"]
    for key in ("summary", "description"):
        value = op.get(key)
        if value:
            parts.append(str(value).strip())
    params = []
    for p in op.get("parameters", []):
        p = _deref(spec, p)
        if isinstance(p, dict) and p.get("name"):
            params.append(p["name"])
    if params:
        parts.append("Parameters: " + ", ".join(params))

    body = _deref(spec, op.get("requestBody"))
    body_fields: list[str] = []
    if isinstance(body, dict):
        for media in (body.get("content") or {}).values():
            if isinstance(media, dict):
                body_fields.extend(_schema_fields(spec, media.get("schema")))
    body_fields = list(dict.fromkeys(body_fields))
    if body_fields:
        parts.append("Request body fields: " + ", ".join(body_fields))

    responses = op.get("responses")
    if isinstance(responses, dict) and responses:
        parts.append("Responses: " + ", ".join(str(c) for c in responses))
        resp_fields: list[str] = []
        for resp in responses.values():
            resp = _deref(spec, resp)
            if not isinstance(resp, dict):
                continue
            for media in (resp.get("content") or {}).values():
                if isinstance(media, dict):
                    resp_fields.extend(_schema_fields(spec, media.get("schema")))
        resp_fields = list(dict.fromkeys(resp_fields))
        if resp_fields:
            parts.append("Response fields: " + ", ".join(resp_fields))
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
                text=_operation_text(spec, method, path, op),
                source=source,
                kind="text",
                symbol=op.get("operationId") or label,
                knowledge=knowledge,
            ))
    return chunks
