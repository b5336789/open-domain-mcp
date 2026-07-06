"""VB.NET lightweight parser for the code graph.

No usable tree-sitter grammar exists for VB.NET, but the language is
line-oriented and block-delimited (Sub/Function ... End Sub/Function), so a
regex line scanner recovers definitions and call sites reliably. Anything
ambiguous is skipped rather than guessed — the resolver treats missing calls
as lower coverage, not wrong edges.

Known silent limitations:
- Line continuations (trailing ``_``) are not joined; each physical line is
  parsed independently.
- Single-line colon-separated procedures (``Sub Foo() : ... : End Sub``) are
  not recognized as complete blocks; statements packed after the ``:`` on the
  declaration line are not scanned for calls."""

from __future__ import annotations

import re

from .models import CallSite, FunctionDef, RawSymbols, scan_db_calls

_IMPORTS = re.compile(r"^\s*Imports\s+([\w.]+)", re.IGNORECASE)
_NAMESPACE = re.compile(r"^\s*Namespace\s+([\w.]+)", re.IGNORECASE)
_END_NAMESPACE = re.compile(r"^\s*End\s+Namespace", re.IGNORECASE)
_CLASS = re.compile(r"^\s*(?:Public\s+|Private\s+|Friend\s+|Partial\s+)*(?:Class|Module)\s+(\w+)",
                    re.IGNORECASE)
_END_CLASS = re.compile(r"^\s*End\s+(?:Class|Module)", re.IGNORECASE)
_PROC = re.compile(
    r"^\s*(?P<mods>(?:Public|Private|Friend|Protected|Shared|Overrides|Async|\s)+)?"
    r"(?P<kind>Sub|Function)\s+(?P<name>\w+)\s*(?P<params>\([^)]*\))?",
    re.IGNORECASE)
_END_PROC = re.compile(r"^\s*End\s+(?:Sub|Function)", re.IGNORECASE)
_CALL_STMT = re.compile(r"\bCall\s+([\w.]+)\s*\(", re.IGNORECASE)
_CALL_EXPR = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(")
_COMMAND_TEXT = re.compile(r"\.CommandText\s*=\s*\"([^\"]*)\"", re.IGNORECASE)

# VB keywords / constructs that look like calls but are not.
_KEYWORD_BLACKLIST = {
    "if", "while", "for", "select", "case", "catch", "throw", "new", "ctype",
    "directcast", "trycast", "cint", "cstr", "cdbl", "cdate", "cbool", "clng",
    "return", "dim", "using", "synclock", "nameof", "gettype", "sub", "function",
    "addhandler", "removehandler", "raiseevent", "not", "and", "or", "andalso",
    "orelse", "iif",
}


def extract_vbnet(source: str, file: str) -> RawSymbols:
    syms = RawSymbols()
    namespace = ""
    classes: list[str] = []
    current: FunctionDef | None = None
    body_lines: list[tuple[int, str]] = []

    for lineno, line in enumerate(source.splitlines(), start=1):
        m = _IMPORTS.match(line)
        if m:
            syms.imports.append(m.group(1))
            continue
        m = _NAMESPACE.match(line)
        if m:
            namespace = m.group(1)
            continue
        if _END_NAMESPACE.match(line):
            namespace = ""
            continue
        m = _CLASS.match(line)
        if m and current is None:
            classes.append(m.group(1))
            continue
        if _END_CLASS.match(line) and current is None:
            if classes:
                classes.pop()
            continue
        if current is None:
            m = _PROC.match(line)
            if m and m.group("name").lower() not in _KEYWORD_BLACKLIST:
                current = _start_proc(m, namespace, classes, file, lineno)
                body_lines = []
            continue
        # inside a Sub/Function body
        if _END_PROC.match(line):
            current.end_line = lineno
            syms.functions.append(current)
            _emit_calls(current, body_lines, file, syms)
            current = None
            continue
        m = _PROC.match(line)
        if m and m.group("name").lower() not in _KEYWORD_BLACKLIST:
            # Implicit recovery: a new Sub/Function declaration while a body
            # is still open means the previous End Sub/Function is missing
            # (malformed but real in legacy corpora). Close the current
            # function at the previous line and start the new one here,
            # instead of leaking the declaration into the body as a call.
            current.end_line = lineno - 1
            syms.functions.append(current)
            _emit_calls(current, body_lines, file, syms)
            current = _start_proc(m, namespace, classes, file, lineno)
            body_lines = []
            continue
        body_lines.append((lineno, line))

    return syms


def _start_proc(m: re.Match, namespace: str, classes: list[str], file: str,
                lineno: int) -> FunctionDef:
    prefix = ".".join(([namespace] if namespace else []) + classes)
    name = m.group("name")
    mods = (m.group("mods") or "").lower()
    return FunctionDef(
        qualified_name=f"{prefix}.{name}" if prefix else name,
        file=file, start_line=lineno, end_line=lineno,
        language="vbnet",
        signature=f"{name}{m.group('params') or '()'}",
        exported="public" in mods,
    )


def _emit_calls(fn: FunctionDef, body: list[tuple[int, str]], file: str,
                syms: RawSymbols):
    seen: set[tuple[str, int]] = set()
    for lineno, line in body:
        sql = _COMMAND_TEXT.search(line)
        if sql:
            for proc in scan_db_calls(sql.group(0)):
                syms.calls.append(CallSite(caller=fn.qualified_name,
                                           callee_text=proc, file=file,
                                           line=lineno, kind="db_call",
                                           detail=proc))
            continue
        for rx in (_CALL_STMT, _CALL_EXPR):
            for m in rx.finditer(line):
                callee = m.group(1)
                head = callee.split(".")[0].lower()
                if head in _KEYWORD_BLACKLIST:
                    continue
                # Skip if preceded by "New" keyword
                if m.start() > 0:
                    before = line[:m.start()].rstrip()
                    if before.lower().endswith("new"):
                        continue
                key = (callee, lineno)
                if key in seen:
                    continue
                seen.add(key)
                syms.calls.append(CallSite(caller=fn.qualified_name,
                                           callee_text=callee, file=file,
                                           line=lineno))
