"""GraphQL SDL (schema definition language) parsing.

Like OpenAPI specs, a GraphQL schema is structured, not prose, so splitting it
by character windows loses meaning. Instead we emit one chunk per top-level
definition (``type``, ``interface``, ``enum``, ``input``, ``union``, ``scalar``)
and one chunk per field of the root operation types (``Query``, ``Mutation``,
``Subscription``), since each root field is effectively its own API operation.

Every chunk is pre-classified as ``knowledge_type="API"`` with the definition (or
field) name as the symbol, then fed through the normal embed/store flow.
Pre-classified chunks skip the LLM extractor (see :meth:`Pipeline._extract_one`).
"""

from __future__ import annotations

import re

from ..models import Chunk, KnowledgeUnit

# Root operation types whose fields each become their own chunk.
_ROOT_TYPES = {"Query", "Mutation", "Subscription"}

# A definition keyword followed by its name, e.g. ``type User``,
# ``enum Status``, ``input CreateUserInput``, ``interface Node``.
_BLOCK_RE = re.compile(
    r"^\s*(type|interface|input|enum)\s+([A-Za-z_]\w*)\b[^{]*\{",
)
# Single-line definitions: ``scalar DateTime`` and ``union Result = A | B``.
_SCALAR_RE = re.compile(r"^\s*scalar\s+([A-Za-z_]\w*)\b")
_UNION_RE = re.compile(r"^\s*union\s+([A-Za-z_]\w*)\b")


def looks_like_graphql(text: str) -> bool:
    """Heuristic: does ``text`` contain GraphQL SDL definitions?"""
    if not isinstance(text, str) or not text.strip():
        return False
    for line in text.splitlines():
        if (
            _BLOCK_RE.match(line)
            or _SCALAR_RE.match(line)
            or _UNION_RE.match(line)
        ):
            return True
    return False


def _strip_comments(text: str) -> str:
    """Drop ``#`` line comments, ignoring ``#`` inside double-quoted strings."""
    out: list[str] = []
    for line in text.splitlines():
        in_str = False
        for i, ch in enumerate(line):
            if ch == '"':
                in_str = not in_str
            elif ch == "#" and not in_str:
                line = line[:i]
                break
        out.append(line)
    return "\n".join(out)


def _split_top_level(text: str) -> list[str]:
    """Yield raw top-level definition blocks by tracking brace depth.

    A block runs from a top-level (depth 0) keyword line until its matching
    closing brace, or — for ``scalar``/``union`` — until the end of the line.
    """
    blocks: list[str] = []
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if _BLOCK_RE.match(line):
            # Accumulate until brace depth returns to zero.
            depth = 0
            start = i
            while i < n:
                depth += lines[i].count("{") - lines[i].count("}")
                i += 1
                if depth <= 0 and i > start:
                    break
            blocks.append("\n".join(lines[start:i]))
            continue
        if _SCALAR_RE.match(line) or _UNION_RE.match(line):
            blocks.append(line.strip())
        i += 1
    return blocks


def _field_names(body: str) -> list[str]:
    """Extract field names from the body of a ``type``/``interface`` block.

    Returns names in declaration order, skipping the opening/closing braces.
    A field line looks like ``name(args): ReturnType`` or ``name: ReturnType``.
    """
    names: list[str] = []
    # Body between the first '{' and the last '}'.
    inner = body[body.index("{") + 1 : body.rindex("}")]
    for raw in inner.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = re.match(r"([A-Za-z_]\w*)\s*[(:]", line)
        if match:
            names.append(match.group(1))
    return names


def _knowledge(summary: str, tags: list[str]) -> KnowledgeUnit:
    return KnowledgeUnit(
        summary=summary,
        knowledge_type="API",
        audience=["engineering"],
        tags=tags,
        confidence=1.0,
    )


def split_graphql(text: str, source: str) -> list[Chunk]:
    """Build one API-typed chunk per top-level GraphQL definition.

    Root operation types (Query/Mutation/Subscription) are exploded into one
    chunk per field; all other definitions become a single chunk each.
    """
    cleaned = _strip_comments(text)
    chunks: list[Chunk] = []
    for block in _split_top_level(cleaned):
        block_match = _BLOCK_RE.match(block.splitlines()[0])
        if block_match:
            keyword, name = block_match.group(1), block_match.group(2)
            if keyword in ("type", "interface") and name in _ROOT_TYPES:
                for field in _field_names(block):
                    label = f"{name}.{field}"
                    chunks.append(Chunk(
                        text=f"{label}\n{block.strip()}",
                        source=source,
                        kind="text",
                        symbol=label,
                        knowledge=_knowledge(label, [name.lower()]),
                    ))
                continue
            chunks.append(Chunk(
                text=block.strip(),
                source=source,
                kind="text",
                symbol=name,
                knowledge=_knowledge(f"{keyword} {name}", [keyword]),
            ))
            continue
        scalar = _SCALAR_RE.match(block)
        if scalar:
            name = scalar.group(1)
            chunks.append(Chunk(
                text=block.strip(),
                source=source,
                kind="text",
                symbol=name,
                knowledge=_knowledge(f"scalar {name}", ["scalar"]),
            ))
            continue
        union = _UNION_RE.match(block)
        if union:
            name = union.group(1)
            chunks.append(Chunk(
                text=block.strip(),
                source=source,
                kind="text",
                symbol=name,
                knowledge=_knowledge(f"union {name}", ["union"]),
            ))
    return chunks
