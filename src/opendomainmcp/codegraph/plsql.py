"""Oracle PL/SQL lightweight parser for the code graph.

No bundled tree-sitter grammar covers PL/SQL packages; declarations are
line-regular (CREATE PACKAGE BODY / PROCEDURE / FUNCTION) so a line scanner
recovers them. A procedure's body runs until the next same-level declaration
or the package END — good enough for call-site attribution, which is what
the chain assembly needs."""

from __future__ import annotations

import re

from .models import CallSite, FunctionDef, RawSymbols

_PACKAGE_BODY = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?PACKAGE\s+BODY\s+([\w$]+)", re.IGNORECASE)
_STANDALONE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|FUNCTION)\s+([\w$]+)", re.IGNORECASE)
_MEMBER = re.compile(r"^\s*(?:PROCEDURE|FUNCTION)\s+([\w$]+)", re.IGNORECASE)
_END_PACKAGE = re.compile(r"^\s*END\s+([\w$]+)\s*;", re.IGNORECASE)
_CALL = re.compile(r"\b([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)\s*\(")

_KEYWORD_BLACKLIST = {
    "if", "elsif", "while", "for", "loop", "case", "when", "values", "in",
    "insert", "update", "delete", "select", "from", "where", "and", "or",
    "not", "exists", "count", "sum", "min", "max", "avg", "nvl", "nvl2",
    "decode", "to_char", "to_date", "to_number", "substr", "instr", "trim",
    "upper", "lower", "raise_application_error", "cursor", "table", "varchar2",
    "number", "returning", "coalesce", "greatest", "least", "trunc", "round",
}


def extract_plsql(source: str, file: str) -> RawSymbols:
    syms = RawSymbols()
    lines = source.splitlines()
    package = ""
    # pass 1: find declaration lines (1-indexed, per FunctionDef contract)
    decls: list[tuple[int, str]] = []  # (lineno, qualified_name)
    for lineno, line in enumerate(lines, start=1):
        m = _PACKAGE_BODY.match(line)
        if m:
            package = m.group(1).lower()
            continue
        m = _STANDALONE.match(line)
        if m:
            decls.append((lineno, m.group(1).lower()))
            continue
        if package:
            m = _MEMBER.match(line)
            if m:
                decls.append((lineno, f"{package}.{m.group(1).lower()}"))

    # pass 2: body boundaries = next declaration (or package END / EOF).
    # Package scope is per-declaration (from the qualified name), not the
    # leftover pass-1 state — a standalone proc after a package body must
    # not get the package END-scan applied.
    end_line_total = len(lines)
    for i, (start, qualified) in enumerate(decls):
        end = decls[i + 1][0] - 1 if i + 1 < len(decls) else end_line_total
        if "." in qualified:
            pkg = qualified.split(".", 1)[0]
            for lineno in range(start, end + 1):
                m_end = _END_PACKAGE.match(lines[lineno - 1])
                if m_end and m_end.group(1).lower() == pkg:
                    end = lineno - 1
                    break
        name = qualified.rsplit(".", 1)[-1]
        syms.functions.append(FunctionDef(
            qualified_name=qualified, file=file, start_line=start,
            end_line=end, language="plsql", signature=name, kind="procedure"))
        _emit_calls(qualified, lines, start, end, file, syms, self_name=name)
    return syms


def _emit_calls(qualified: str, lines: list[str], start: int, end: int,
                file: str, syms: RawSymbols, self_name: str):
    for lineno in range(start + 1, end + 1):
        for m in _CALL.finditer(lines[lineno - 1]):
            callee = m.group(1).lower()
            head = callee.split(".")[0]
            if head in _KEYWORD_BLACKLIST or callee in (self_name, qualified):
                continue
            syms.calls.append(CallSite(caller=qualified, callee_text=callee,
                                       file=file, line=lineno))
