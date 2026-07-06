"""Codegraph data model.

Dataclasses shared by every extractor and the resolver, plus the one piece
of cross-language string analysis both Java and VB.NET extractors need:
finding stored-procedure calls embedded in code strings (JDBC call escapes,
ADO.NET CommandText). Kept here so extractors stay parser-only."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FunctionDef:
    qualified_name: str
    file: str
    start_line: int
    end_line: int
    language: str
    signature: str = ""
    kind: str = "function"       # function | procedure | endpoint
    route: Optional[tuple[str, str]] = None  # (METHOD, path template)
    exported: bool = False


@dataclass
class CallSite:
    caller: str
    callee_text: str
    file: str
    line: int
    kind: str = "call"           # call | db_call | http_call
    detail: str = ""


@dataclass
class RawSymbols:
    functions: list[FunctionDef] = field(default_factory=list)
    calls: list[CallSite] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


@dataclass
class ResolvedEdge:
    src: str
    dst: str
    relation: str                # calls | executes_sql | http_call
    confidence: float
    file: str
    line: int
    external: bool = False


@dataclass
class CodeGraph:
    functions: dict[str, FunctionDef] = field(default_factory=dict)
    edges: list[ResolvedEdge] = field(default_factory=list)


# Stored-procedure references inside code strings. Two shapes cover the
# enterprise corpus: JDBC call escapes and exec/call/begin statements in
# ADO.NET / dynamic SQL strings.
_DB_CALL = re.compile(
    r"\{\s*\??=?\s*call\s+(?P<jdbc>[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)"
    r"|\b(?:exec(?:ute)?|call|begin)\s+(?P<stmt>[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)",
    re.IGNORECASE)
_SQL_KEYWORDS = {"transaction", "tran", "immediate"}  # "begin transaction" etc.


def scan_db_calls(text: str) -> list[str]:
    """Stored-procedure names referenced in ``text``, lowercased, deduped,
    in document order."""
    found: list[str] = []
    for m in _DB_CALL.finditer(text):
        name = (m.group("jdbc") or m.group("stmt")).lower()
        if name in _SQL_KEYWORDS or name in found:
            continue
        found.append(name)
    return found
