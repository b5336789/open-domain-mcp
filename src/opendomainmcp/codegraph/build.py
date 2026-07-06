"""Corpus walk -> extractors -> resolver -> CodeGraph, and persistence.

Reuses the ingest filter so the code graph sees exactly the corpus the
pipeline would ingest. Persistence maps FunctionDefs/ResolvedEdges onto the
existing entities/edges tables (types: function/procedure/endpoint/external;
relations: calls/executes_sql/http_call) plus a code_functions provenance
table (file + line range per function). Chunk ids here are synthetic
("cg:<qualified_name>") — plan 4B replaces them with real chunk ids when the
pipeline integration lands."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..graph.models import Edge, Entity
from ..graph.normalize import normalize_name
from ..ingest.filters import IngestFilter
from ..ingest.loader import LANGUAGE_BY_EXT
from .java import extract_java
from .jsts import extract_jsts
from .models import CodeGraph, RawSymbols
from .plsql import extract_plsql
from .resolve import resolve
from .vbnet import extract_vbnet

logger = logging.getLogger(__name__)


def _synthetic_chunk_id(qualified_name: str) -> str:
    """Fixed-length synthetic chunk id (4A; real chunk ids arrive in 4B).
    Hash keeps it under the store's VARCHAR(128) regardless of name length."""
    import hashlib
    return "cg:" + hashlib.sha256(qualified_name.encode("utf-8")).hexdigest()[:32]

# Codegraph-only language additions; the ingest loader mapping is unchanged
# until plan 4B wires VB.NET/PL-SQL into loading/splitting.
_EXTRA_EXTS = {".vb": "vbnet", ".sql": "plsql", ".pks": "plsql",
               ".pkb": "plsql", ".pls": "plsql"}

EXTRACTORS = {
    "java": lambda src, file: extract_java(src, file),
    "javascript": lambda src, file: extract_jsts(src, file, "javascript"),
    "typescript": lambda src, file: extract_jsts(src, file, "typescript"),
    "tsx": lambda src, file: extract_jsts(src, file, "tsx"),
    "vbnet": lambda src, file: extract_vbnet(src, file),
    "plsql": lambda src, file: extract_plsql(src, file),
}


def _language_of(path: Path) -> str | None:
    ext = path.suffix.lower()
    lang = _EXTRA_EXTS.get(ext) or LANGUAGE_BY_EXT.get(ext)
    return lang if lang in EXTRACTORS else None


def build_codegraph(root: str | Path, settings) -> CodeGraph:
    root = Path(root)
    ingest_filter = IngestFilter.from_settings(settings)
    per_file: list[RawSymbols] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            lang = _language_of(path)
            if lang is None:
                continue
            if ingest_filter.exclusion_reason(path, root) is not None:
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                logger.warning("codegraph: cannot read %s: %r", path, exc)
                continue
            rel = str(path.relative_to(root))
            per_file.append(EXTRACTORS[lang](source, rel))
    return resolve(per_file)


def persist_codegraph(graph: CodeGraph, store) -> dict:
    entities, edges, functions = [], [], []
    for fn in graph.functions.values():
        entities.append(Entity(
            normalized_name=normalize_name(fn.qualified_name),
            display_name=fn.qualified_name, type=fn.kind,
            chunk_id=_synthetic_chunk_id(fn.qualified_name),  # synthetic until 4B
        ))
        functions.append({
            "qualified_name": fn.qualified_name, "file": fn.file,
            "start_line": fn.start_line, "end_line": fn.end_line,
            "language": fn.language, "signature": fn.signature,
            "kind": fn.kind,
        })
    for edge in graph.edges:
        if edge.external:
            entities.append(Entity(
                normalized_name=normalize_name(edge.dst), display_name=edge.dst,
                type="external", chunk_id=_synthetic_chunk_id(edge.src),
                confidence=edge.confidence))
        edges.append(Edge(
            src=normalize_name(edge.src), dst=normalize_name(edge.dst),
            relation_type=edge.relation, chunk_id=_synthetic_chunk_id(edge.src),
            confidence=edge.confidence))
    store.upsert_entities(entities)
    store.upsert_edges(edges)
    store.upsert_functions(functions)
    return {"functions": len(graph.functions), "edges": len(edges)}
