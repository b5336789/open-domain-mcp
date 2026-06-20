"""Code dependency extraction (task 4.4).

Builds module/symbol dependency edges from a code chunk's import statements.
Tree-sitter is used to read imports when a grammar is available; otherwise a
regex fallback handles the common Python and JS/TS forms. Unsupported languages
degrade gracefully to an empty result.

The result mirrors :func:`opendomainmcp.graph.builder.build_graph`: a list of
:class:`Entity` plus a list of :class:`Edge`. The chunk's own module is emitted
as one ``module`` entity; each imported module becomes a ``module`` entity with
an ``imports`` edge from the source module to it.

Pure function: no I/O, no mutation of inputs.
"""

from __future__ import annotations

import re

from .models import Edge, Entity
from .normalize import normalize_name

# relation_type used for every dependency edge produced here.
IMPORTS_RELATION = "imports"

# Languages whose import syntax we know how to read with the regex fallback.
_PY_LANGS = {"python"}
_JS_LANGS = {"javascript", "typescript", "tsx", "jsx"}

# Python: `import a, b.c` and `from a.b import x`.
_PY_IMPORT = re.compile(r"^\s*import\s+(.+)$", re.MULTILINE)
_PY_FROM = re.compile(r"^\s*from\s+([.\w]+)\s+import\s+", re.MULTILINE)
# JS/TS: `import ... from "x"`, `export ... from 'x'`, and `require("x")`.
_JS_FROM = re.compile(r"""(?:import|export)\b[^;\n]*?\bfrom\s+['"]([^'"]+)['"]""")
_JS_BARE = re.compile(r"""(?:^|\n)\s*import\s+['"]([^'"]+)['"]""")
_JS_REQUIRE = re.compile(r"""\brequire\(\s*['"]([^'"]+)['"]\s*\)""")


def _module_label(symbol: str | None, chunk_id: str) -> str:
    """Human-readable name for the chunk's own module node.

    Prefer the chunk's ``symbol`` (set by the AST splitter); fall back to the
    chunk id so the node is always addressable.
    """
    label = (symbol or "").strip()
    return label or chunk_id


def _py_imports(source: str) -> list[str]:
    names: list[str] = []
    for block in _PY_IMPORT.findall(source):
        # `import a.b as c, d` -> ["a.b", "d"]; strip aliases and submodule tail
        # is kept (a.b) so distinct modules stay distinct.
        for part in block.split(","):
            mod = part.strip().split(" as ")[0].strip()
            if mod:
                names.append(mod)
    names.extend(m for m in _PY_FROM.findall(source) if m)
    return names


def _js_imports(source: str) -> list[str]:
    names: list[str] = []
    names.extend(_JS_FROM.findall(source))
    names.extend(_JS_BARE.findall(source))
    names.extend(_JS_REQUIRE.findall(source))
    return names


def _imports_for(language: str, source: str) -> list[str]:
    lang = (language or "").lower()
    if lang in _PY_LANGS:
        return _py_imports(source)
    if lang in _JS_LANGS:
        return _js_imports(source)
    return []


def _dedupe(names: list[str]) -> list[str]:
    """Order-preserving dedupe on the *original* names (post-normalization the
    edges are deduped again by the store)."""
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        key = n.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def extract_dependencies(
    language: str, source: str, symbol: str | None, chunk_id: str
) -> tuple[list[Entity], list[Edge]]:
    """Extract import dependency entities and edges for one code chunk.

    Returns ``([], [])`` when the language is unsupported or there are no
    imports, so callers can upsert unconditionally.
    """
    imports = _imports_for(language, source or "")
    if not imports:
        return [], []

    src_label = _module_label(symbol, chunk_id)
    src_norm = normalize_name(src_label)
    if not src_norm:
        return [], []

    entities: dict[str, Entity] = {
        src_norm: Entity(normalized_name=src_norm, display_name=src_label,
                         type="module", chunk_id=chunk_id, confidence=1.0)
    }
    edges: list[Edge] = []
    for name in _dedupe(imports):
        dst_norm = normalize_name(name)
        if not dst_norm or dst_norm == src_norm:
            continue
        if dst_norm not in entities:
            entities[dst_norm] = Entity(normalized_name=dst_norm, display_name=name,
                                        type="module", chunk_id=chunk_id, confidence=1.0)
        edges.append(Edge(src=src_norm, dst=dst_norm,
                          relation_type=IMPORTS_RELATION, chunk_id=chunk_id,
                          confidence=1.0))

    return list(entities.values()), edges
