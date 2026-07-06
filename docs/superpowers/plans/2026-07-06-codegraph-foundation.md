# Codegraph Foundation (Plan 4A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deterministic, offline-testable `codegraph/` subsystem that builds a function-level, cross-language call graph (Java, VB.NET, Oracle PL/SQL, JS/TS) from source files, persists it into the existing graph store with file:line provenance, and assembles entry-point-rooted call chains.

**Architecture:** Per-language symbol extractors (tree-sitter for Java/JS-TS; hand-written line-oriented parsers for VB.NET/PL-SQL) emit `FunctionDef` + `CallSite` records. A resolver builds a global symbol table, resolves call sites with scope precedence, and creates cross-language `executes_sql` (SQL-call strings) and `http_call` (fetch/axios ↔ route annotations) edges. `build.py` orchestrates files → extractors → resolver → `CodeGraph`, persisted via a new `code_functions` provenance table plus existing entities/edges. `chains.py` detects entry points and assembles chains via DFS with cycle truncation. No LLM anywhere in this plan (chain *analysis* is Plan 4B).

**Tech Stack:** Python ≥ 3.11, tree-sitter ≥ 0.23 (wheels for java/javascript/typescript already in pyproject), stdlib `re` for VB.NET/PL-SQL, pytest offline.

**Spec:** `docs/superpowers/specs/2026-07-06-codegraph-chain-extraction-design.md`

## Global Constraints

- Zero LLM/token cost in this entire plan; pure static analysis.
- All tests offline; run via `.venv/bin/python -m pytest`.
- Unresolvable calls are NEVER dropped: they become edges to an `external` entity with low confidence.
- Every `FunctionDef` and every resolved edge carries `file`, `start_line`, `end_line` provenance.
- Follow existing conventions: `graph/normalize.py:normalize_name()` for entity keys; `Entity`/`Edge` dataclasses from `graph/models.py` (type/relation_type are free strings); FakeGraphStore in tests/conftest.py mirrors MariaGraphStore semantics.
- Confidence constants (fixed for the whole plan): same-scope resolution 1.0, same package/module 0.9, import-based 0.8, unique-global 0.6, unresolved-external 0.3, executes_sql to known procedure 0.9 (unknown 0.5), http_call 0.7.
- Qualified-name formats (fixed): Java `pkg.Class.method`; VB.NET `Namespace.Class.Sub` (or `Class.Sub` without namespace); PL/SQL `package.procedure` (standalone: `procedure`); JS/TS `<relative path>:<funcName>` (e.g. `src/api/client.ts:fetchOrders`).
- Entity types written by codegraph: `function`, `procedure`, `endpoint`, `external`. Relation types: `calls`, `executes_sql`, `http_call`.
- In this plan (4A) graph persistence uses synthetic chunk ids `cg:<qualified_name>` (Plan 4B rewires real chunk ids during pipeline integration). Document this at every persistence site.
- Keyword blacklists (VB.NET/PL-SQL call detection) live as module constants, not inline literals.

## Parallel execution note

Dependency waves: **[T1] → [T2, T3, T4, T5, T6 in parallel — disjoint new files] → [T7] → [T8]**. Parallel implementers must `git add` only their own files and retry `git commit` up to 3× on index.lock contention.

---

### Task 1: `codegraph/models.py` — data model + shared DB-call string scanner

**Files:**
- Create: `src/opendomainmcp/codegraph/__init__.py`
- Create: `src/opendomainmcp/codegraph/models.py`
- Test: `tests/test_codegraph_models.py`

**Interfaces:**
- Produces (consumed by every later task):

```python
@dataclass
class FunctionDef:
    qualified_name: str          # per Global Constraints format
    file: str
    start_line: int              # 1-indexed inclusive
    end_line: int
    language: str                # "java" | "vbnet" | "plsql" | "javascript" | "typescript"
    signature: str = ""          # best-effort one-line signature
    kind: str = "function"       # "function" | "procedure" | "endpoint"
    route: Optional[tuple[str, str]] = None   # (http_method_upper, path_template) for endpoints
    exported: bool = False       # JS/TS export / VB Public / Java public

@dataclass
class CallSite:
    caller: str                  # qualified_name of enclosing FunctionDef
    callee_text: str             # raw call text, e.g. "repo.save", "PKG_BILLING.VALIDATE"
    file: str
    line: int
    kind: str = "call"           # "call" | "db_call" | "http_call"
    detail: str = ""             # db_call: proc string; http_call: "METHOD URL"

@dataclass
class RawSymbols:                # one extractor's output for one file
    functions: list[FunctionDef] = field(default_factory=list)
    calls: list[CallSite] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)   # java import FQNs / js module specifiers

@dataclass
class ResolvedEdge:
    src: str                     # qualified_name
    dst: str                     # qualified_name or external name
    relation: str                # "calls" | "executes_sql" | "http_call"
    confidence: float
    file: str
    line: int
    external: bool = False

@dataclass
class CodeGraph:
    functions: dict[str, FunctionDef] = field(default_factory=dict)  # by qualified_name
    edges: list[ResolvedEdge] = field(default_factory=list)
```

- Also produces `scan_db_calls(text: str) -> list[str]`: finds stored-procedure references in code strings — JDBC `{call PKG.PROC}` / `{?= call PKG.PROC}`, `prepareCall("...")` contents, and `exec|call|begin NAME` inside SQL-looking strings. Returns the bare proc names (e.g. `"pkg_billing.validate"`), lowercased, deduplicated, order-preserving.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_codegraph_models.py
"""Codegraph data model + shared DB-call scanner (spec 4A, task 1)."""

from opendomainmcp.codegraph.models import (
    CallSite, CodeGraph, FunctionDef, RawSymbols, ResolvedEdge, scan_db_calls,
)


def test_functiondef_defaults():
    f = FunctionDef(qualified_name="pkg.Cls.m", file="A.java",
                    start_line=3, end_line=9, language="java")
    assert f.kind == "function" and f.route is None and not f.exported


def test_dataclasses_compose_into_codegraph():
    f = FunctionDef("a.B.c", "B.java", 1, 5, "java")
    e = ResolvedEdge(src="a.B.c", dst="x.Y.z", relation="calls",
                     confidence=0.9, file="B.java", line=2)
    g = CodeGraph(functions={f.qualified_name: f}, edges=[e])
    assert g.functions["a.B.c"].end_line == 5
    assert g.edges[0].relation == "calls"
    assert RawSymbols().functions == [] and CallSite(
        caller="a.B.c", callee_text="x", file="B.java", line=2).kind == "call"


def test_scan_db_calls_jdbc_forms():
    src = '''
    CallableStatement cs = conn.prepareCall("{call PKG_BILLING.VALIDATE_AMOUNT(?, ?)}");
    var s2 = conn.prepareCall("{?= call pkg_util.compute(?)}");
    '''
    assert scan_db_calls(src) == ["pkg_billing.validate_amount", "pkg_util.compute"]


def test_scan_db_calls_exec_and_begin_forms():
    src = '''
    cmd.CommandText = "BEGIN pkg_orders.close_order(:id); END;"
    other.CommandText = "exec billing_report"
    '''
    assert scan_db_calls(src) == ["pkg_orders.close_order", "billing_report"]


def test_scan_db_calls_dedup_and_no_false_positives():
    src = '''
    a = conn.prepareCall("{call P.X}");
    b = conn.prepareCall("{call P.X}");
    plain = "select id from orders";
    '''
    assert scan_db_calls(src) == ["p.x"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_codegraph_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'opendomainmcp.codegraph'`

- [ ] **Step 3: Implement**

```python
# src/opendomainmcp/codegraph/__init__.py
"""Function-level, cross-language code graph (spec 4A).

Static analysis only — no LLM. Extractors emit FunctionDef/CallSite records,
the resolver links them into a CodeGraph, chains.py assembles entry-point
rooted call chains for the LLM analysis stage (plan 4B)."""
```

```python
# src/opendomainmcp/codegraph/models.py
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
_JDBC_CALL = re.compile(r"\{\s*\??=?\s*call\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)",
                        re.IGNORECASE)
_SQL_EXEC = re.compile(r"\b(?:exec(?:ute)?|call|begin)\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)",
                       re.IGNORECASE)
_SQL_KEYWORDS = {"transaction", "tran", "immediate"}  # "begin transaction" etc.


def scan_db_calls(text: str) -> list[str]:
    """Stored-procedure names referenced in ``text``, lowercased, deduped."""
    found: list[str] = []
    for rx in (_JDBC_CALL, _SQL_EXEC):
        for m in rx.finditer(text):
            name = m.group(1).lower()
            if name in _SQL_KEYWORDS:
                continue
            if name not in found:
                found.append(name)
    return found
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_codegraph_models.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/codegraph tests/test_codegraph_models.py
git commit -m "feat: codegraph data model and DB-call string scanner"
```

---

### Task 2: `codegraph/java.py` — Java extractor (tree-sitter)

**Files:**
- Create: `src/opendomainmcp/codegraph/java.py`
- Test: `tests/test_codegraph_java.py`

**Interfaces:**
- Consumes: Task 1 dataclasses; `scan_db_calls`.
- Produces: `extract_java(source: str, file: str) -> RawSymbols`. Qualified names `pkg.Class.method` (no package → `Class.method`). Route annotations (`@GetMapping`/`@PostMapping`/`@PutMapping`/`@DeleteMapping`/`@PatchMapping`/`@RequestMapping`, with class-level `@RequestMapping` prefix) set `kind="endpoint"` and `route=(METHOD, path)`; `@RequestMapping` without an explicit method maps to `"ANY"`. Method bodies are scanned with `scan_db_calls` → `CallSite(kind="db_call")`. `imports` = FQNs from `import` declarations.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_codegraph_java.py
"""Java symbol extraction via tree-sitter (spec 4A, task 2)."""

from opendomainmcp.codegraph.java import extract_java

BILLING = """
package com.acme.billing;

import com.acme.repo.OrderRepo;

@RequestMapping("/api/billing")
public class BillingController {

    private OrderRepo repo;

    @PostMapping("/charge")
    public Receipt charge(Order order) {
        validate(order);
        return repo.save(order);
    }

    private void validate(Order order) {
        CallableStatement cs = conn.prepareCall("{call PKG_BILLING.VALIDATE_AMOUNT(?)}");
    }
}
"""


def test_functions_with_qualified_names_and_lines():
    syms = extract_java(BILLING, "Billing.java")
    names = {f.qualified_name: f for f in syms.functions}
    assert "com.acme.billing.BillingController.charge" in names
    assert "com.acme.billing.BillingController.validate" in names
    charge = names["com.acme.billing.BillingController.charge"]
    assert charge.file == "Billing.java" and charge.language == "java"
    assert charge.start_line > 1 and charge.end_line > charge.start_line


def test_route_annotation_makes_endpoint_with_class_prefix():
    syms = extract_java(BILLING, "Billing.java")
    charge = next(f for f in syms.functions if f.qualified_name.endswith(".charge"))
    assert charge.kind == "endpoint"
    assert charge.route == ("POST", "/api/billing/charge")


def test_call_sites_and_db_calls():
    syms = extract_java(BILLING, "Billing.java")
    plain = {(c.caller.rsplit(".", 1)[1], c.callee_text)
             for c in syms.calls if c.kind == "call"}
    assert ("charge", "validate") in plain
    assert ("charge", "repo.save") in plain
    db = [c for c in syms.calls if c.kind == "db_call"]
    assert db and db[0].detail == "pkg_billing.validate_amount"
    assert db[0].caller.endswith(".validate")


def test_imports_collected():
    syms = extract_java(BILLING, "Billing.java")
    assert "com.acme.repo.OrderRepo" in syms.imports


def test_no_package_and_plain_method():
    syms = extract_java(
        "public class Util { static int add(int a, int b) { return a + b; } }",
        "Util.java")
    assert [f.qualified_name for f in syms.functions] == ["Util.add"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_codegraph_java.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'opendomainmcp.codegraph.java'`

- [ ] **Step 3: Implement**

Follow the parser-acquisition pattern from `ingest/code_splitter.py:_get_parser` (module-level cache). Walk the tree once, tracking package, class stack, and current method.

```python
# src/opendomainmcp/codegraph/java.py
"""Java symbol extraction for the code graph (tree-sitter).

Walks the AST once: collects package/imports, class stack, method
declarations (with Spring route annotations -> endpoints), and per-method
call sites (method_invocation). DB-call strings inside method bodies become
db_call CallSites via models.scan_db_calls. Dynamic dispatch is not resolved
here — the resolver decides what a callee name means."""

from __future__ import annotations

from .models import CallSite, FunctionDef, RawSymbols, scan_db_calls

_ROUTE_ANNOTATIONS = {
    "GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT",
    "DeleteMapping": "DELETE", "PatchMapping": "PATCH", "RequestMapping": "ANY",
}

_parser = None


def _get_parser():
    global _parser
    if _parser is None:
        import tree_sitter as ts
        import tree_sitter_java

        _parser = ts.Parser(ts.Language(tree_sitter_java.language()))
    return _parser


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")


def _first_string_literal(node, src: bytes) -> str:
    if node.type == "string_literal":
        return _text(node, src).strip('"')
    for child in node.children:
        s = _first_string_literal(child, src)
        if s:
            return s
    return ""


def _annotation_route(node, src: bytes) -> tuple[str, str] | None:
    """(METHOD, path) if ``node`` is a route annotation, else None."""
    if node.type not in ("annotation", "marker_annotation"):
        return None
    name_node = node.child_by_field_name("name")
    name = _text(name_node, src) if name_node else ""
    method = _ROUTE_ANNOTATIONS.get(name)
    if method is None:
        return None
    return (method, _first_string_literal(node, src))


def _collect_calls(node, src: bytes, caller: str, file: str, out: list[CallSite]):
    if node.type == "method_invocation":
        obj = node.child_by_field_name("object")
        name = node.child_by_field_name("name")
        callee = _text(name, src) if name else ""
        if obj is not None:
            callee = f"{_text(obj, src)}.{callee}"
        out.append(CallSite(caller=caller, callee_text=callee, file=file,
                            line=node.start_point[0] + 1))
    for child in node.children:
        _collect_calls(child, src, caller, file, out)


def extract_java(source: str, file: str) -> RawSymbols:
    src = source.encode("utf-8")
    tree = _get_parser().parse(src)
    syms = RawSymbols()
    _walk(tree.root_node, src, file, syms, package="", classes=[], class_route="")
    return syms


def _walk(node, src: bytes, file: str, syms: RawSymbols,
          package: str, classes: list[str], class_route: str):
    for child in node.children:
        t = child.type
        if t == "package_declaration":
            ids = [c for c in child.children if c.type in ("scoped_identifier", "identifier")]
            if ids:
                package = _text(ids[0], src)
        elif t == "import_declaration":
            ids = [c for c in child.children if c.type in ("scoped_identifier", "identifier")]
            if ids:
                syms.imports.append(_text(ids[0], src))
        elif t in ("class_declaration", "interface_declaration", "enum_declaration"):
            name_node = child.child_by_field_name("name")
            cls = _text(name_node, src) if name_node else "?"
            route_prefix = ""
            for sib in child.children:
                if sib.type == "modifiers":
                    for ann in sib.children:
                        r = _annotation_route(ann, src)
                        if r:
                            route_prefix = r[1]
            body = child.child_by_field_name("body")
            if body is not None:
                _walk(body, src, file, syms, package, classes + [cls],
                      route_prefix or class_route)
        elif t in ("method_declaration", "constructor_declaration"):
            _method(child, src, file, syms, package, classes, class_route)
        else:
            _walk(child, src, file, syms, package, classes, class_route)


def _method(node, src: bytes, file: str, syms: RawSymbols,
            package: str, classes: list[str], class_route: str):
    name_node = node.child_by_field_name("name")
    name = _text(name_node, src) if name_node else "?"
    prefix = ".".join(([package] if package else []) + classes)
    qualified = f"{prefix}.{name}" if prefix else name

    route = None
    exported = False
    for child in node.children:
        if child.type == "modifiers":
            exported = "public" in _text(child, src)
            for ann in child.children:
                r = _annotation_route(ann, src)
                if r:
                    route = (r[0], (class_route.rstrip("/") + "/" + r[1].lstrip("/"))
                             if class_route else r[1])
    params = node.child_by_field_name("parameters")
    signature = f"{name}{_text(params, src) if params else '()'}"

    syms.functions.append(FunctionDef(
        qualified_name=qualified, file=file,
        start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
        language="java", signature=signature,
        kind="endpoint" if route else "function",
        route=route, exported=exported,
    ))
    body = node.child_by_field_name("body")
    if body is not None:
        _collect_calls(body, src, qualified, file, syms.calls)
        for proc in scan_db_calls(_text(body, src)):
            syms.calls.append(CallSite(
                caller=qualified, callee_text=proc, file=file,
                line=body.start_point[0] + 1, kind="db_call", detail=proc))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_codegraph_java.py -v`
Expected: 5 passed. If a tree-sitter node-type assumption fails (grammar version drift), inspect with a quick REPL parse of the fixture and adjust node-type names — do not weaken the test assertions.

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/codegraph/java.py tests/test_codegraph_java.py
git commit -m "feat: Java symbol extractor for codegraph"
```

---

### Task 3: `codegraph/jsts.py` — JS/TS extractor (tree-sitter)

**Files:**
- Create: `src/opendomainmcp/codegraph/jsts.py`
- Test: `tests/test_codegraph_jsts.py`

**Interfaces:**
- Consumes: Task 1 dataclasses.
- Produces: `extract_jsts(source: str, file: str, language: str) -> RawSymbols` (`language` in `{"javascript","typescript","tsx"}`; tsx uses the typescript grammar's tsx language, mirroring `_GRAMMARS`). Qualified names `<file>:<name>` (nested/anonymous functions attribute their calls to the nearest named enclosing function). `fetch("URL")` and `axios.get/post/put/delete/patch("URL")` call sites become `CallSite(kind="http_call", detail="METHOD URL")` — `fetch` defaults to GET (or the `method:` property in its options object if present as a plain string); template literals have `${...}` substitutions replaced with `{param}`. `imports` = module specifier strings.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_codegraph_jsts.py
"""JS/TS symbol extraction via tree-sitter (spec 4A, task 3)."""

from opendomainmcp.codegraph.jsts import extract_jsts

CLIENT = """
import { api } from "./base";

export async function fetchOrders(customerId) {
  const res = await fetch(`/api/billing/orders/${customerId}`);
  return normalize(res);
}

function normalize(res) {
  return res.json();
}

export const charge = async (order) => {
  return axios.post("/api/billing/charge", order);
};
"""


def test_functions_and_exports():
    syms = extract_jsts(CLIENT, "src/api/client.js", "javascript")
    by_name = {f.qualified_name: f for f in syms.functions}
    assert "src/api/client.js:fetchOrders" in by_name
    assert "src/api/client.js:normalize" in by_name
    assert "src/api/client.js:charge" in by_name
    assert by_name["src/api/client.js:fetchOrders"].exported
    assert not by_name["src/api/client.js:normalize"].exported
    f = by_name["src/api/client.js:fetchOrders"]
    assert f.start_line > 1 and f.end_line >= f.start_line


def test_plain_call_sites():
    syms = extract_jsts(CLIENT, "src/api/client.js", "javascript")
    plain = {(c.caller, c.callee_text) for c in syms.calls if c.kind == "call"}
    assert ("src/api/client.js:fetchOrders", "normalize") in plain


def test_http_call_sites_with_template_params():
    syms = extract_jsts(CLIENT, "src/api/client.js", "javascript")
    http = {c.detail for c in syms.calls if c.kind == "http_call"}
    assert "GET /api/billing/orders/{param}" in http
    assert "POST /api/billing/charge" in http


def test_imports_and_typescript_language():
    syms = extract_jsts("import x from 'mod';\nexport function f(): void { g(); }",
                        "a.ts", "typescript")
    assert "mod" in syms.imports
    assert [f.qualified_name for f in syms.functions] == ["a.ts:f"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_codegraph_jsts.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

```python
# src/opendomainmcp/codegraph/jsts.py
"""JS/TS symbol extraction for the code graph (tree-sitter).

Function declarations, class methods, and named arrow functions become
FunctionDefs (qualified as ``<file>:<name>``). Call expressions attribute to
the nearest named enclosing function. fetch()/axios.*() call sites become
http_call CallSites with a normalized "METHOD /path/{param}" detail so the
resolver can match them against backend route templates."""

from __future__ import annotations

import re

from .models import CallSite, FunctionDef, RawSymbols

_AXIOS_METHODS = {"get", "post", "put", "delete", "patch"}
_parsers: dict[str, object] = {}


def _get_parser(language: str):
    if language not in _parsers:
        import tree_sitter as ts

        if language == "javascript":
            import tree_sitter_javascript as mod
            lang = mod.language()
        else:
            import tree_sitter_typescript as mod
            lang = mod.language_tsx() if language == "tsx" else mod.language_typescript()
        _parsers[language] = ts.Parser(ts.Language(lang))
    return _parsers[language]


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")


def _url_of(node, src: bytes) -> str:
    """Literal or template URL, ${...} -> {param}. '' if not a string."""
    if node.type == "string":
        return _text(node, src).strip("'\"")
    if node.type == "template_string":
        raw = _text(node, src).strip("`")
        return re.sub(r"\$\{[^}]*\}", "{param}", raw)
    return ""


def extract_jsts(source: str, file: str, language: str) -> RawSymbols:
    src = source.encode("utf-8")
    tree = _get_parser(language).parse(src)
    syms = RawSymbols()
    _walk(tree.root_node, src, file, syms, enclosing=None, exported=False)
    return syms


def _func_name(node, src: bytes) -> str | None:
    name = node.child_by_field_name("name")
    if name is not None:
        return _text(name, src)
    return None


def _register(node, src, file, syms, name: str, exported: bool) -> str:
    qualified = f"{file}:{name}"
    syms.functions.append(FunctionDef(
        qualified_name=qualified, file=file,
        start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
        language="javascript" if file.endswith((".js", ".jsx", ".mjs")) else "typescript",
        signature=name, exported=exported,
    ))
    return qualified


def _walk(node, src: bytes, file: str, syms: RawSymbols,
          enclosing: str | None, exported: bool):
    for child in node.children:
        t = child.type
        if t == "import_statement":
            source_node = child.child_by_field_name("source")
            if source_node is not None:
                syms.imports.append(_text(source_node, src).strip("'\""))
        elif t == "export_statement":
            _walk(child, src, file, syms, enclosing, exported=True)
            continue
        elif t in ("function_declaration", "generator_function_declaration",
                   "method_definition"):
            name = _func_name(child, src)
            scope = _register(child, src, file, syms, name, exported) if name else enclosing
            body = child.child_by_field_name("body")
            if body is not None:
                _walk(body, src, file, syms, scope, False)
            continue
        elif t in ("lexical_declaration", "variable_declaration"):
            for decl in [c for c in child.children if c.type == "variable_declarator"]:
                value = decl.child_by_field_name("value")
                name_node = decl.child_by_field_name("name")
                if value is not None and value.type in ("arrow_function", "function_expression") \
                        and name_node is not None:
                    scope = _register(value, src, file, syms,
                                      _text(name_node, src), exported)
                    body = value.child_by_field_name("body")
                    if body is not None:
                        _walk(body, src, file, syms, scope, False)
                else:
                    _walk(decl, src, file, syms, enclosing, False)
            continue
        elif t == "call_expression" and enclosing is not None:
            _call(child, src, file, syms, enclosing)
        _walk(child, src, file, syms, enclosing, False)


def _call(node, src: bytes, file: str, syms: RawSymbols, caller: str):
    fn = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    if fn is None:
        return
    fn_text = _text(fn, src)
    line = node.start_point[0] + 1
    first_arg = None
    if args is not None:
        actual = [c for c in args.children if c.type not in ("(", ")", ",")]
        first_arg = actual[0] if actual else None

    if fn_text == "fetch" and first_arg is not None:
        url = _url_of(first_arg, src)
        if url:
            method = "GET"
            m = re.search(r"method\s*:\s*['\"](\w+)['\"]", _text(args, src))
            if m:
                method = m.group(1).upper()
            syms.calls.append(CallSite(caller=caller, callee_text="fetch", file=file,
                                       line=line, kind="http_call",
                                       detail=f"{method} {url}"))
            return
    if fn.type == "member_expression":
        obj = fn.child_by_field_name("object")
        prop = fn.child_by_field_name("property")
        if obj is not None and prop is not None and _text(obj, src) == "axios" \
                and _text(prop, src) in _AXIOS_METHODS and first_arg is not None:
            url = _url_of(first_arg, src)
            if url:
                syms.calls.append(CallSite(
                    caller=caller, callee_text=fn_text, file=file, line=line,
                    kind="http_call",
                    detail=f"{_text(prop, src).upper()} {url}"))
                return
    syms.calls.append(CallSite(caller=caller, callee_text=fn_text,
                               file=file, line=line))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_codegraph_jsts.py -v`
Expected: 4 passed. Same node-type caveat as Task 2: fix node-type names against the actual grammar, never the assertions.

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/codegraph/jsts.py tests/test_codegraph_jsts.py
git commit -m "feat: JS/TS symbol extractor with http_call detection"
```

---

### Task 4: `codegraph/vbnet.py` — VB.NET lightweight parser

**Files:**
- Create: `src/opendomainmcp/codegraph/vbnet.py`
- Test: `tests/test_codegraph_vbnet.py`

**Interfaces:**
- Consumes: Task 1 dataclasses; `scan_db_calls`.
- Produces: `extract_vbnet(source: str, file: str) -> RawSymbols`. Line-oriented parsing: `Namespace X`/`End Namespace`, `Class X`/`Module X`/`End Class|Module`, `Sub Name(...)`/`Function Name(...)` … `End Sub|Function`. Qualified `Namespace.Class.Name`. `exported=True` for `Public`. Call sites: `Call Name(...)`, `Name(...)`, `Obj.Name(...)` within bodies, with a VB keyword blacklist. `CommandText = "..."` strings run through `scan_db_calls` → `db_call` CallSites. `Imports X` lines → imports.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_codegraph_vbnet.py
"""VB.NET lightweight parser (spec 4A, task 4). No tree-sitter grammar exists
for VB.NET; the syntax is line-oriented and regular enough for regex parsing."""

from opendomainmcp.codegraph.vbnet import extract_vbnet

BILLING_VB = """
Imports Acme.Data

Namespace Acme.Billing
    Public Class BillingService

        Public Function ChargeOrder(ByVal order As Order) As Receipt
            ValidateAmount(order)
            Return Repo.Save(order)
        End Function

        Private Sub ValidateAmount(ByVal order As Order)
            Dim cmd As New OracleCommand()
            cmd.CommandText = "BEGIN pkg_billing.validate_amount(:amt); END;"
            If order.Amount < 0 Then
                Throw New ArgumentException("negative")
            End If
        End Sub

    End Class
End Namespace
"""


def test_functions_qualified_and_lines():
    syms = extract_vbnet(BILLING_VB, "Billing.vb")
    by_name = {f.qualified_name: f for f in syms.functions}
    charge = by_name["Acme.Billing.BillingService.ChargeOrder"]
    validate = by_name["Acme.Billing.BillingService.ValidateAmount"]
    assert charge.exported and not validate.exported
    assert charge.language == "vbnet" and charge.file == "Billing.vb"
    assert charge.start_line < validate.start_line
    assert charge.end_line > charge.start_line


def test_call_sites_with_keyword_blacklist():
    syms = extract_vbnet(BILLING_VB, "Billing.vb")
    plain = {(c.caller.rsplit(".", 1)[1], c.callee_text)
             for c in syms.calls if c.kind == "call"}
    assert ("ChargeOrder", "ValidateAmount") in plain
    assert ("ChargeOrder", "Repo.Save") in plain
    # If / Throw / New must not be call sites
    assert not any(c.callee_text in ("If", "Throw", "New", "ArgumentException")
                   for c in syms.calls if c.kind == "call"
                   and c.caller.endswith("ValidateAmount"))


def test_commandtext_db_call():
    syms = extract_vbnet(BILLING_VB, "Billing.vb")
    db = [c for c in syms.calls if c.kind == "db_call"]
    assert db and db[0].detail == "pkg_billing.validate_amount"
    assert db[0].caller.endswith(".ValidateAmount")


def test_imports_and_module_without_namespace():
    syms = extract_vbnet(
        "Imports System.Data\nModule Util\n  Sub Ping()\n  End Sub\nEnd Module\n",
        "Util.vb")
    assert "System.Data" in syms.imports
    assert [f.qualified_name for f in syms.functions] == ["Util.Ping"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_codegraph_vbnet.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

```python
# src/opendomainmcp/codegraph/vbnet.py
"""VB.NET lightweight parser for the code graph.

No usable tree-sitter grammar exists for VB.NET, but the language is
line-oriented and block-delimited (Sub/Function ... End Sub/Function), so a
regex line scanner recovers definitions and call sites reliably. Anything
ambiguous is skipped rather than guessed — the resolver treats missing calls
as lower coverage, not wrong edges."""

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
                prefix = ".".join(([namespace] if namespace else []) + classes)
                name = m.group("name")
                mods = (m.group("mods") or "").lower()
                current = FunctionDef(
                    qualified_name=f"{prefix}.{name}" if prefix else name,
                    file=file, start_line=lineno, end_line=lineno,
                    language="vbnet",
                    signature=f"{name}{m.group('params') or '()'}",
                    exported="public" in mods,
                )
                body_lines = []
            continue
        # inside a Sub/Function body
        if _END_PROC.match(line):
            current.end_line = lineno
            syms.functions.append(current)
            _emit_calls(current, body_lines, file, syms)
            current = None
            continue
        body_lines.append((lineno, line))

    return syms


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
                key = (callee, lineno)
                if key in seen:
                    continue
                seen.add(key)
                syms.calls.append(CallSite(caller=fn.qualified_name,
                                           callee_text=callee, file=file,
                                           line=lineno))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_codegraph_vbnet.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/codegraph/vbnet.py tests/test_codegraph_vbnet.py
git commit -m "feat: VB.NET lightweight parser for codegraph"
```

---

### Task 5: `codegraph/plsql.py` — Oracle PL/SQL lightweight parser

**Files:**
- Create: `src/opendomainmcp/codegraph/plsql.py`
- Test: `tests/test_codegraph_plsql.py`

**Interfaces:**
- Consumes: Task 1 dataclasses.
- Produces: `extract_plsql(source: str, file: str) -> RawSymbols`. Handles `CREATE [OR REPLACE] PACKAGE BODY name` (tracks package), `PROCEDURE name`/`FUNCTION name` declarations inside package bodies, and standalone `CREATE [OR REPLACE] PROCEDURE|FUNCTION name`. Qualified `package.procedure` (lowercase), standalone `procedure`. All defs get `kind="procedure"`. Body boundary: from the declaration line to the line before the next same-level `PROCEDURE`/`FUNCTION` declaration or `END <package>;`/EOF. Call sites: `identifier(` and `pkg.identifier(` with a PL/SQL keyword blacklist.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_codegraph_plsql.py
"""Oracle PL/SQL lightweight parser (spec 4A, task 5)."""

from opendomainmcp.codegraph.plsql import extract_plsql

PKG = """
CREATE OR REPLACE PACKAGE BODY pkg_billing AS

  PROCEDURE validate_amount(p_amt IN NUMBER) IS
  BEGIN
    IF p_amt < 0 THEN
      RAISE_APPLICATION_ERROR(-20001, 'negative amount');
    END IF;
    log_util.write('validated');
  END validate_amount;

  FUNCTION compute_total(p_id IN NUMBER) RETURN NUMBER IS
    v_total NUMBER;
  BEGIN
    validate_amount(v_total);
    RETURN v_total;
  END compute_total;

END pkg_billing;
"""


def test_package_procedures_qualified_lowercase():
    syms = extract_plsql(PKG, "pkg_billing.pkb")
    names = {f.qualified_name: f for f in syms.functions}
    assert set(names) == {"pkg_billing.validate_amount", "pkg_billing.compute_total"}
    v = names["pkg_billing.validate_amount"]
    assert v.kind == "procedure" and v.language == "plsql"
    assert v.start_line == 3 and v.end_line >= 9


def test_call_sites_within_bodies():
    syms = extract_plsql(PKG, "pkg_billing.pkb")
    calls = {(c.caller, c.callee_text) for c in syms.calls}
    assert ("pkg_billing.compute_total", "validate_amount") in calls
    assert ("pkg_billing.validate_amount", "log_util.write") in calls
    # keywords are not calls
    assert not any(c.callee_text.lower() in ("if", "raise_application_error")
                   for c in syms.calls)


def test_standalone_procedure():
    src = "CREATE OR REPLACE PROCEDURE billing_report AS\nBEGIN\n  pkg_billing.compute_total(1);\nEND;\n"
    syms = extract_plsql(src, "report.sql")
    assert [f.qualified_name for f in syms.functions] == ["billing_report"]
    assert ("billing_report", "pkg_billing.compute_total") in {
        (c.caller, c.callee_text) for c in syms.calls}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_codegraph_plsql.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

```python
# src/opendomainmcp/codegraph/plsql.py
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
    # pass 1: find declaration lines
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

    # pass 2: body boundaries = next declaration (or package END / EOF)
    end_line_total = len(lines)
    for i, (start, qualified) in enumerate(decls):
        end = decls[i + 1][0] - 1 if i + 1 < len(decls) else end_line_total
        if package:
            for lineno in range(start, end):
                if _END_PACKAGE.match(lines[lineno - 1]) and \
                        _END_PACKAGE.match(lines[lineno - 1]).group(1).lower() == package:
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
            if head in _KEYWORD_BLACKLIST or callee == self_name:
                continue
            syms.calls.append(CallSite(caller=qualified, callee_text=callee,
                                       file=file, line=lineno))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_codegraph_plsql.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/codegraph/plsql.py tests/test_codegraph_plsql.py
git commit -m "feat: PL/SQL lightweight parser for codegraph"
```

---

### Task 6: `codegraph/resolve.py` — call resolution + cross-language edges

**Files:**
- Create: `src/opendomainmcp/codegraph/resolve.py`
- Test: `tests/test_codegraph_resolve.py`

**Interfaces:**
- Consumes: Task 1 dataclasses only (tests build synthetic `RawSymbols` — no dependency on Tasks 2–5).
- Produces: `resolve(per_file: list[RawSymbols]) -> CodeGraph` plus confidence constants:

```python
CONF_SAME_SCOPE = 1.0    # same class/module/package body
CONF_SAME_PACKAGE = 0.9  # same java package / same file for js
CONF_IMPORT = 0.8        # matched via an import
CONF_UNIQUE = 0.6        # globally unique basename
CONF_EXTERNAL = 0.3      # unresolved -> external node
CONF_DB_KNOWN = 0.9      # executes_sql to a known PL/SQL def
CONF_DB_UNKNOWN = 0.5    # executes_sql to an unknown proc (external)
CONF_HTTP = 0.7          # http_call matched to a route
```

Resolution rules for `kind="call"` sites, in precedence order (first hit wins):
1. **Same scope:** callee basename matches a sibling (same qualified prefix as caller) → `CONF_SAME_SCOPE`. For dotted callees like `repo.save`, try last segment against siblings too (instance-member calls).
2. **Same package/module:** for Java, match `package.*.name`; for JS/TS, same-file already covered by rule 1's prefix (file prefix); PL/SQL: same package.
3. **Import-based:** a dotted callee whose head matches the last segment of an import (e.g. import `com.acme.repo.OrderRepo`, callee `repo.save` does NOT match; callee `OrderRepo.save` does) → resolve to `<import FQN>.<method>` if that qualified name exists → `CONF_IMPORT`.
4. **Unique global basename:** callee's last segment matches exactly one known FunctionDef basename → `CONF_UNIQUE`.
5. Otherwise → edge to `dst=callee_text` (lowercased), `external=True`, `CONF_EXTERNAL`.

`kind="db_call"` sites: `detail` (proc name) matched case-insensitively against PL/SQL defs (`package.procedure` or standalone name) → `executes_sql`/`CONF_DB_KNOWN`, else external with `CONF_DB_UNKNOWN`.
`kind="http_call"` sites: `detail` = `"METHOD /path"` matched against endpoint FunctionDefs' `route` — method equal (or endpoint `ANY`), path template match segment-by-segment where `{...}`/`{param}` segments are wildcards → `http_call`/`CONF_HTTP`, else external (`external=True`, name `http:<METHOD> <path>`, `CONF_EXTERNAL`).
Self-calls (src == dst) are dropped. Duplicate (src,dst,relation) keep highest confidence.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_codegraph_resolve.py
"""Call resolution and cross-language edges (spec 4A, task 6).
Uses synthetic RawSymbols — independent of the language extractors."""

from opendomainmcp.codegraph.models import CallSite, FunctionDef, RawSymbols
from opendomainmcp.codegraph.resolve import (
    CONF_DB_KNOWN, CONF_EXTERNAL, CONF_HTTP, CONF_SAME_SCOPE, CONF_UNIQUE, resolve,
)


def _fd(qname, language="java", kind="function", route=None, file="F"):
    return FunctionDef(qualified_name=qname, file=file, start_line=1,
                       end_line=2, language=language, kind=kind, route=route)


def test_same_scope_resolution_wins():
    syms = RawSymbols(
        functions=[_fd("a.B.charge"), _fd("a.B.validate")],
        calls=[CallSite(caller="a.B.charge", callee_text="validate",
                        file="F", line=5)])
    g = resolve([syms])
    e = g.edges[0]
    assert (e.src, e.dst, e.relation) == ("a.B.charge", "a.B.validate", "calls")
    assert e.confidence == CONF_SAME_SCOPE and not e.external


def test_unique_global_basename():
    syms1 = RawSymbols(functions=[_fd("a.B.charge")],
                       calls=[CallSite(caller="a.B.charge",
                                       callee_text="write_audit", file="F", line=2)])
    syms2 = RawSymbols(functions=[_fd("x.Y.write_audit")])
    g = resolve([syms1, syms2])
    e = next(e for e in g.edges if e.dst == "x.Y.write_audit")
    assert e.confidence == CONF_UNIQUE


def test_unresolved_becomes_low_confidence_external():
    syms = RawSymbols(functions=[_fd("a.B.charge")],
                      calls=[CallSite(caller="a.B.charge",
                                      callee_text="Mystery.thing", file="F", line=2)])
    g = resolve([syms])
    e = g.edges[0]
    assert e.external and e.confidence == CONF_EXTERNAL
    assert e.dst == "mystery.thing"


def test_db_call_links_to_plsql_procedure():
    java = RawSymbols(
        functions=[_fd("a.B.validate")],
        calls=[CallSite(caller="a.B.validate", callee_text="pkg_billing.validate_amount",
                        file="F", line=3, kind="db_call",
                        detail="pkg_billing.validate_amount")])
    plsql = RawSymbols(functions=[_fd("pkg_billing.validate_amount",
                                      language="plsql", kind="procedure")])
    g = resolve([java, plsql])
    e = next(e for e in g.edges if e.relation == "executes_sql")
    assert e.dst == "pkg_billing.validate_amount"
    assert e.confidence == CONF_DB_KNOWN and not e.external


def test_http_call_matches_route_template():
    js = RawSymbols(
        functions=[_fd("src/client.js:fetchOrders", language="javascript")],
        calls=[CallSite(caller="src/client.js:fetchOrders", callee_text="fetch",
                        file="src/client.js", line=4, kind="http_call",
                        detail="GET /api/billing/orders/{param}")])
    java = RawSymbols(functions=[_fd("a.B.getOrders", kind="endpoint",
                                     route=("GET", "/api/billing/orders/{customerId}"))])
    g = resolve([js, java])
    e = next(e for e in g.edges if e.relation == "http_call")
    assert e.dst == "a.B.getOrders" and e.confidence == CONF_HTTP


def test_unmatched_http_call_is_external_and_self_calls_dropped():
    js = RawSymbols(
        functions=[_fd("c.js:f", language="javascript")],
        calls=[
            CallSite(caller="c.js:f", callee_text="fetch", file="c.js", line=1,
                     kind="http_call", detail="POST /nope"),
            CallSite(caller="c.js:f", callee_text="f", file="c.js", line=2),
        ])
    g = resolve([js])
    assert all(e.src != e.dst for e in g.edges)
    http = next(e for e in g.edges if e.relation == "http_call")
    assert http.external and http.dst == "http:post /nope"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_codegraph_resolve.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

```python
# src/opendomainmcp/codegraph/resolve.py
"""Call-site resolution: RawSymbols -> CodeGraph.

Name-based with scope precedence (same scope > same package > import >
globally-unique basename). Unresolvable calls are never dropped — they become
low-confidence edges to an ``external`` node so coverage gaps stay visible.
Cross-language edges: db_call sites match PL/SQL defs (executes_sql);
http_call sites match endpoint route templates (http_call)."""

from __future__ import annotations

from .models import CallSite, CodeGraph, FunctionDef, RawSymbols, ResolvedEdge

CONF_SAME_SCOPE = 1.0
CONF_SAME_PACKAGE = 0.9
CONF_IMPORT = 0.8
CONF_UNIQUE = 0.6
CONF_EXTERNAL = 0.3
CONF_DB_KNOWN = 0.9
CONF_DB_UNKNOWN = 0.5
CONF_HTTP = 0.7


def resolve(per_file: list[RawSymbols]) -> CodeGraph:
    graph = CodeGraph()
    imports_by_file: dict[str, list[str]] = {}
    for syms in per_file:
        for fn in syms.functions:
            graph.functions[fn.qualified_name] = fn
        for fn in syms.functions:
            imports_by_file.setdefault(fn.file, syms.imports)

    by_basename: dict[str, list[str]] = {}
    for qname in graph.functions:
        base = qname.rsplit(".", 1)[-1].rsplit(":", 1)[-1].lower()
        by_basename.setdefault(base, []).append(qname)
    plsql_by_name = {q.lower(): q for q, f in graph.functions.items()
                     if f.language == "plsql"}
    endpoints = [f for f in graph.functions.values() if f.route is not None]

    edges: dict[tuple[str, str, str], ResolvedEdge] = {}

    def add(edge: ResolvedEdge):
        if edge.src == edge.dst:
            return
        key = (edge.src, edge.dst, edge.relation)
        if key not in edges or edges[key].confidence < edge.confidence:
            edges[key] = edge

    for syms in per_file:
        for call in syms.calls:
            if call.kind == "db_call":
                add(_resolve_db(call, plsql_by_name))
            elif call.kind == "http_call":
                add(_resolve_http(call, endpoints))
            else:
                add(_resolve_call(call, graph, by_basename,
                                  imports_by_file.get(call.file, [])))

    graph.edges = list(edges.values())
    return graph


def _prefix_of(qualified: str) -> str:
    if ":" in qualified:
        return qualified.split(":", 1)[0]
    return qualified.rsplit(".", 1)[0] if "." in qualified else ""


def _resolve_call(call: CallSite, graph: CodeGraph,
                  by_basename: dict[str, list[str]],
                  imports: list[str]) -> ResolvedEdge:
    callee = call.callee_text
    base = callee.rsplit(".", 1)[-1].lower()
    caller_prefix = _prefix_of(call.caller)
    sep = ":" if ":" in call.caller else "."

    # 1. same scope
    sibling = f"{caller_prefix}{sep}{callee}" if caller_prefix else callee
    if sibling in graph.functions:
        return _edge(call, sibling, CONF_SAME_SCOPE)
    sib_base = f"{caller_prefix}{sep}{base}" if caller_prefix else base
    if sib_base in graph.functions:
        return _edge(call, sib_base, CONF_SAME_SCOPE)

    # 2. same package (java/plsql): parent of the caller prefix
    if "." in caller_prefix:
        pkg = caller_prefix.rsplit(".", 1)[0]
        for candidate in by_basename.get(base, []):
            if candidate.startswith(pkg + "."):
                return _edge(call, candidate, CONF_SAME_PACKAGE)

    # 3. import-based: callee head matches an import's last segment
    if "." in callee:
        head = callee.split(".")[0]
        for imp in imports:
            if imp.rsplit(".", 1)[-1] == head:
                candidate = f"{imp}.{callee.split('.', 1)[1]}"
                if candidate in graph.functions:
                    return _edge(call, candidate, CONF_IMPORT)

    # 4. globally unique basename
    candidates = by_basename.get(base, [])
    if len(candidates) == 1:
        return _edge(call, candidates[0], CONF_UNIQUE)

    # 5. external
    return _edge(call, callee.lower(), CONF_EXTERNAL, external=True)


def _resolve_db(call: CallSite, plsql_by_name: dict[str, str]) -> ResolvedEdge:
    proc = call.detail.lower()
    target = plsql_by_name.get(proc)
    if target is None and "." not in proc:
        # standalone procedure referenced without a package prefix
        matches = [q for k, q in plsql_by_name.items() if k == proc]
        target = matches[0] if matches else None
    if target is not None:
        return _edge(call, target, CONF_DB_KNOWN, relation="executes_sql")
    return _edge(call, proc, CONF_DB_UNKNOWN, relation="executes_sql", external=True)


def _resolve_http(call: CallSite, endpoints: list[FunctionDef]) -> ResolvedEdge:
    method, _, path = call.detail.partition(" ")
    for ep in endpoints:
        ep_method, ep_path = ep.route
        if ep_method not in ("ANY", method.upper()):
            continue
        if _paths_match(path, ep_path):
            return _edge(call, ep.qualified_name, CONF_HTTP, relation="http_call")
    return _edge(call, f"http:{call.detail.lower()}", CONF_EXTERNAL,
                 relation="http_call", external=True)


def _paths_match(actual: str, template: str) -> bool:
    a = [s for s in actual.split("/") if s]
    t = [s for s in template.split("/") if s]
    if len(a) != len(t):
        return False
    for sa, st in zip(a, t):
        wild = (st.startswith("{") and st.endswith("}")) or \
               (sa.startswith("{") and sa.endswith("}"))
        if not wild and sa != st:
            return False
    return True


def _edge(call: CallSite, dst: str, confidence: float,
          relation: str = "calls", external: bool = False) -> ResolvedEdge:
    return ResolvedEdge(src=call.caller, dst=dst, relation=relation,
                        confidence=confidence, file=call.file, line=call.line,
                        external=external)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_codegraph_resolve.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/codegraph/resolve.py tests/test_codegraph_resolve.py
git commit -m "feat: codegraph call resolution with cross-language edges"
```

---

### Task 7: `codegraph/build.py` — orchestration + graph-store persistence

**Files:**
- Create: `src/opendomainmcp/codegraph/build.py`
- Modify: `src/opendomainmcp/graph/store.py` (MariaGraphStore: new `code_functions` table + `upsert_functions`/`get_function`; NullGraphStore: no-ops)
- Modify: `tests/conftest.py` (FakeGraphStore: `upsert_functions`/`get_function` mirroring Maria semantics)
- Test: `tests/test_codegraph_build.py`

**Interfaces:**
- Consumes: extractors (Tasks 2–5) via a dispatch table; `resolve()` (Task 6); `IngestFilter` (reuse — the corpus walk respects the same exclusion rules as ingestion); `graph/models.py` Entity/Edge; `normalize_name`.
- Produces:

```python
EXTRACTORS = {
    "java":       lambda src, file: extract_java(src, file),
    "javascript": lambda src, file: extract_jsts(src, file, "javascript"),
    "typescript": lambda src, file: extract_jsts(src, file, "typescript"),
    "tsx":        lambda src, file: extract_jsts(src, file, "tsx"),
    "vbnet":      lambda src, file: extract_vbnet(src, file),
    "plsql":      lambda src, file: extract_plsql(src, file),
}

def build_codegraph(root: str | Path, settings) -> CodeGraph
def persist_codegraph(graph: CodeGraph, store) -> dict   # {"functions": n, "edges": n}
```

- Language detection for the walk: `LANGUAGE_BY_EXT` from loader **plus codegraph-only additions** `{".vb": "vbnet", ".sql": "plsql", ".pks": "plsql", ".pkb": "plsql", ".pls": "plsql"}` (kept local to build.py — the ingest loader mapping is untouched in 4A; wiring loader/splitter is 4B).
- Persistence mapping: each `FunctionDef` → `Entity(normalized_name=normalize_name(qualified_name), display_name=qualified_name, type=fn.kind, chunk_id=f"cg:{qualified_name}", confidence=1.0)` + one `code_functions` row (provenance). Each `ResolvedEdge` → `Edge(src=normalize_name(src), dst=normalize_name(dst), relation_type=edge.relation, chunk_id=f"cg:{src}", confidence=edge.confidence)`; external dst also upserted as `Entity(type="external", ...)`. Synthetic `cg:` chunk ids are 4A-only (4B rewires real chunk ids) — comment this at the call site.
- New store surface (all three stores):

```python
def upsert_functions(self, functions: list[dict]) -> None
    # dict: {"qualified_name","file","start_line","end_line","language","signature","kind"}
def get_function(self, qualified_name: str) -> Optional[dict]
```

MariaDB DDL (add to `ensure_schema`, mirroring existing style):

```sql
CREATE TABLE IF NOT EXISTS code_functions (
    collection     VARCHAR(255) NOT NULL,
    qualified_name VARCHAR(512) NOT NULL,
    file           VARCHAR(1024) NOT NULL,
    start_line     INT NOT NULL,
    end_line       INT NOT NULL,
    language       VARCHAR(32) NOT NULL,
    signature      VARCHAR(1024) NOT NULL DEFAULT '',
    kind           VARCHAR(32) NOT NULL DEFAULT 'function',
    PRIMARY KEY (collection(150), qualified_name(300))
) CHARACTER SET utf8mb4
```

`upsert_functions` uses `INSERT ... ON DUPLICATE KEY UPDATE file=VALUES(file), start_line=VALUES(start_line), end_line=VALUES(end_line), signature=VALUES(signature), kind=VALUES(kind)` (re-runs refresh provenance). FakeGraphStore stores them in `backing[collection]["code_functions"][qualified_name] = dict`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_codegraph_build.py
"""End-to-end codegraph build over a mixed-language corpus (spec 4A, task 7)."""

from opendomainmcp.codegraph.build import build_codegraph, persist_codegraph
from opendomainmcp.config import Settings

JAVA = """
package com.acme.billing;

public class BillingService {
    public void charge(Order order) {
        validate(order);
    }
    private void validate(Order order) {
        CallableStatement cs = conn.prepareCall("{call PKG_BILLING.VALIDATE_AMOUNT(?)}");
    }
}
"""
PLSQL = """
CREATE OR REPLACE PACKAGE BODY pkg_billing AS
  PROCEDURE validate_amount(p_amt IN NUMBER) IS
  BEGIN
    NULL;
  END validate_amount;
END pkg_billing;
"""


def _corpus(root):
    (root / "BillingService.java").write_text(JAVA)
    (root / "pkg_billing.pkb").write_text(PLSQL)
    (root / "test_ignored.java").write_text("x")           # excluded name? no — java
    (root / "vendor").mkdir()
    (root / "vendor" / "Skip.java").write_text(JAVA)       # excluded by filter


def test_build_walks_extracts_and_resolves(tmp_path):
    _corpus(tmp_path)
    graph = build_codegraph(tmp_path, Settings())
    assert "com.acme.billing.BillingService.charge" in graph.functions
    assert "pkg_billing.validate_amount" in graph.functions
    # vendor/ excluded by the shared ingest filter
    assert not any(f.file.endswith("Skip.java") for f in graph.functions.values())
    rels = {(e.src, e.dst, e.relation) for e in graph.edges}
    assert ("com.acme.billing.BillingService.charge",
            "com.acme.billing.BillingService.validate", "calls") in rels
    assert ("com.acme.billing.BillingService.validate",
            "pkg_billing.validate_amount", "executes_sql") in rels


def test_persist_writes_entities_edges_and_provenance(tmp_path, fake_graph):
    _corpus(tmp_path)
    graph = build_codegraph(tmp_path, Settings())
    stats = persist_codegraph(graph, fake_graph)
    assert stats["functions"] == len(graph.functions)
    assert stats["edges"] == len(graph.edges)

    ent = fake_graph.get_entity("com.acme.billing.billingservice.charge")
    assert ent and ent["type"] == "function"

    fn = fake_graph.get_function("com.acme.billing.BillingService.validate")
    assert fn["file"].endswith("BillingService.java")
    assert fn["start_line"] > 0 and fn["end_line"] >= fn["start_line"]
    assert fn["language"] == "java"

    nb = fake_graph.neighbors("com.acme.billing.billingservice.validate")
    rels = {(n["relation_type"], n["direction"]) for n in nb["neighbors"]}
    assert ("executes_sql", "out") in rels


def test_get_function_missing_returns_none(fake_graph):
    assert fake_graph.get_function("nope.nothing") is None
```

Note for the implementer: check FakeGraphStore.neighbors' actual return shape in conftest before asserting `direction` — mirror whatever key names it already uses (adjust the assertion to the real shape if it differs, keeping the intent: the executes_sql edge is visible from the validate entity).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_codegraph_build.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

```python
# src/opendomainmcp/codegraph/build.py
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
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
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
            chunk_id=f"cg:{fn.qualified_name}",  # synthetic until 4B
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
                type="external", chunk_id=f"cg:{edge.src}",
                confidence=edge.confidence))
        edges.append(Edge(
            src=normalize_name(edge.src), dst=normalize_name(edge.dst),
            relation_type=edge.relation, chunk_id=f"cg:{edge.src}",
            confidence=edge.confidence))
    store.upsert_entities(entities)
    store.upsert_edges(edges)
    store.upsert_functions(functions)
    return {"functions": len(graph.functions), "edges": len(edges)}
```

Store changes — `src/opendomainmcp/graph/store.py`:
- NullGraphStore: add `def upsert_functions(self, functions): pass` and `def get_function(self, qualified_name): return None`.
- MariaGraphStore: add the `code_functions` DDL (verbatim from Interfaces above) to `ensure_schema`; implement `upsert_functions` (executemany INSERT ... ON DUPLICATE KEY UPDATE as specified) and `get_function` (SELECT by collection + qualified_name → dict or None) following the existing method style (connection handling identical to `upsert_entities`/`get_entity`).

`tests/conftest.py` — FakeGraphStore: add

```python
    def upsert_functions(self, functions):
        table = self._data().setdefault("code_functions", {})
        for fn in functions:
            table[fn["qualified_name"]] = dict(fn)

    def get_function(self, qualified_name):
        return self._data().get("code_functions", {}).get(qualified_name)
```

(using whatever per-collection accessor the class already has — read the class first and match its internal style; `_data()` above is illustrative).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_codegraph_build.py tests/test_graph_store_fake.py -v`
Expected: all pass (existing fake-store tests unaffected).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: no regressions (MariaDB tests are `integration`-marked and skip offline).

- [ ] **Step 6: Commit**

```bash
git add src/opendomainmcp/codegraph/build.py src/opendomainmcp/graph/store.py tests/conftest.py tests/test_codegraph_build.py
git commit -m "feat: codegraph build orchestration and graph-store persistence"
```

---

### Task 8: `codegraph/chains.py` — entry points + chain assembly, and CLI

**Files:**
- Create: `src/opendomainmcp/codegraph/chains.py`
- Modify: `src/opendomainmcp/cli.py` (new `codegraph` subcommand)
- Modify: `src/opendomainmcp/config.py` (`codegraph_max_chain_depth: int = 12` — NOT in EDITABLE_FIELDS; env-only for now)
- Test: `tests/test_codegraph_chains.py`, `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `CodeGraph`/`ResolvedEdge` (Task 1), `build_codegraph`/`persist_codegraph` (Task 7).
- Produces:

```python
@dataclass
class Chain:
    entry: str                    # qualified_name of the entry point
    members: list[str]            # DFS preorder, entry first, internal nodes only
    edges: list[ResolvedEdge]     # edges traversed (internal + boundary-to-external)
    truncated: bool = False       # a cycle or depth limit cut the walk

def detect_entry_points(graph: CodeGraph) -> list[str]
def assemble_chains(graph: CodeGraph, max_depth: int = 12) -> list[Chain]
```

- Entry points (deterministic, order-stable): every endpoint (`route is not None`), plus every non-external function with in-degree 0 over internal (`external=False`) edges. Sorted by qualified_name.
- DFS follows internal edges only for traversal; boundary edges to external nodes are recorded in `chain.edges` but externals never join `members`. Cycle handling: a back-edge to a node already on the current path sets `truncated=True` and is not followed. Depth limit `max_depth` likewise sets `truncated=True`.
- CLI: `opendomainmcp codegraph PATH [--persist] [--json]` → builds the graph, detects chains, prints stats: functions by language, edges by relation, entry-point count, chain count, truncated count, unresolved(external-edge) count; `--persist` additionally writes to `ctx.graph` via `persist_codegraph`; `--json` dumps the stats dict as JSON.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_codegraph_chains.py
"""Entry-point detection and chain assembly (spec 4A, task 8)."""

from opendomainmcp.codegraph.chains import assemble_chains, detect_entry_points
from opendomainmcp.codegraph.models import CodeGraph, FunctionDef, ResolvedEdge


def _fd(q, kind="function", route=None):
    return FunctionDef(qualified_name=q, file="F", start_line=1, end_line=2,
                       language="java", kind=kind, route=route)


def _edge(src, dst, relation="calls", external=False):
    return ResolvedEdge(src=src, dst=dst, relation=relation, confidence=1.0,
                        file="F", line=1, external=external)


def _graph():
    # endpoint -> a -> b -> (external db proc);  orphan has no callers
    fns = [_fd("api.Ctl.post", kind="endpoint", route=("POST", "/x")),
           _fd("svc.A.a"), _fd("svc.B.b"), _fd("svc.Orphan.run")]
    edges = [_edge("api.Ctl.post", "svc.A.a"),
             _edge("svc.A.a", "svc.B.b"),
             _edge("svc.B.b", "pkg.proc", relation="executes_sql", external=True)]
    return CodeGraph(functions={f.qualified_name: f for f in fns}, edges=edges)


def test_entry_points_endpoints_and_indegree_zero():
    eps = detect_entry_points(_graph())
    assert eps == ["api.Ctl.post", "svc.Orphan.run"]


def test_chain_members_edges_and_external_boundary():
    chains = {c.entry: c for c in assemble_chains(_graph())}
    main = chains["api.Ctl.post"]
    assert main.members == ["api.Ctl.post", "svc.A.a", "svc.B.b"]
    assert any(e.relation == "executes_sql" for e in main.edges)
    assert not main.truncated
    assert chains["svc.Orphan.run"].members == ["svc.Orphan.run"]


def test_cycle_truncates_but_terminates():
    fns = {q: _fd(q) for q in ("x.A.a", "x.B.b")}
    fns["x.A.a"] = _fd("x.A.a")
    g = CodeGraph(functions=fns,
                  edges=[_edge("x.A.a", "x.B.b"), _edge("x.B.b", "x.A.a")])
    chains = assemble_chains(g)
    entry = [c for c in chains if c.entry == "x.A.a"]
    # in-degree of both nodes is 1, so neither is an entry unless cycle-only
    # components are surfaced via their lexicographically-first node
    assert entry and entry[0].truncated
    assert entry[0].members == ["x.A.a", "x.B.b"]


def test_depth_limit_truncates():
    fns = {f"m.C{i}.f": _fd(f"m.C{i}.f") for i in range(5)}
    edges = [_edge(f"m.C{i}.f", f"m.C{i+1}.f") for i in range(4)]
    g = CodeGraph(functions=fns, edges=edges)
    chains = {c.entry: c for c in assemble_chains(g, max_depth=2)}
    c = chains["m.C0.f"]
    assert c.truncated and len(c.members) == 3  # entry + 2 levels
```

Append to `tests/test_cli.py` (reuse the file's existing `cli.main` + monkeypatched `build_context` pattern with `lambda **_: ctx`):

```python
def test_codegraph_cli_stats(cli_ctx_pattern, tmp_path, capsys, monkeypatch):
    # follow the file's established fake-ctx pattern; the fake ctx only needs
    # .settings and .graph (FakeGraphStore) for this command
    (tmp_path / "A.java").write_text(
        "public class A { public void run() { help(); } void help() {} }")
    import opendomainmcp.cli as cli
    rc = cli.main(["codegraph", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "functions" in out and "entry" in out.lower()
```

(Adapt the fixture wiring to the file's real pattern — assertions stay.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_codegraph_chains.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

```python
# src/opendomainmcp/codegraph/chains.py
"""Entry-point detection and call-chain assembly.

An entry point is an endpoint (has a route) or an internal function nobody
internal calls (in-degree 0). Cycle-only components would otherwise be
unreachable, so each such component is surfaced through its
lexicographically-first member. Chains follow internal edges depth-first;
external boundary edges (db procs we don't have source for, unmatched http
calls) are recorded on the chain but never traversed."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import CodeGraph, ResolvedEdge


@dataclass
class Chain:
    entry: str
    members: list[str] = field(default_factory=list)
    edges: list[ResolvedEdge] = field(default_factory=list)
    truncated: bool = False


def detect_entry_points(graph: CodeGraph) -> list[str]:
    internal = {q for q in graph.functions}
    indegree = {q: 0 for q in internal}
    for e in graph.edges:
        if not e.external and e.dst in indegree and e.src in internal:
            indegree[e.dst] += 1
    entries = {q for q, f in graph.functions.items() if f.route is not None}
    entries |= {q for q, d in indegree.items() if d == 0}
    # cycle-only components: nodes not reachable from any current entry
    reachable = _reachable(graph, entries)
    leftover = sorted(internal - reachable)
    while leftover:
        entries.add(leftover[0])
        reachable = _reachable(graph, entries)
        leftover = sorted(internal - reachable)
    return sorted(entries)


def _out_edges(graph: CodeGraph) -> dict[str, list[ResolvedEdge]]:
    out: dict[str, list[ResolvedEdge]] = {}
    for e in graph.edges:
        out.setdefault(e.src, []).append(e)
    return out


def _reachable(graph: CodeGraph, roots: set[str]) -> set[str]:
    out = _out_edges(graph)
    seen = set(roots)
    stack = list(roots)
    while stack:
        node = stack.pop()
        for e in out.get(node, []):
            if not e.external and e.dst in graph.functions and e.dst not in seen:
                seen.add(e.dst)
                stack.append(e.dst)
    return seen


def assemble_chains(graph: CodeGraph, max_depth: int = 12) -> list[Chain]:
    out = _out_edges(graph)
    chains = []
    for entry in detect_entry_points(graph):
        chain = Chain(entry=entry)
        _dfs(entry, out, graph, chain, path=[entry], depth=0, max_depth=max_depth)
        chain.members = _preorder(chain, entry)
        chains.append(chain)
    return chains


def _dfs(node: str, out, graph: CodeGraph, chain: Chain,
         path: list[str], depth: int, max_depth: int):
    for e in sorted(out.get(node, []), key=lambda e: (e.dst, e.relation)):
        if e.external or e.dst not in graph.functions:
            chain.edges.append(e)          # boundary edge, not traversed
            continue
        if e.dst in path:
            chain.truncated = True         # cycle back-edge
            continue
        if depth + 1 > max_depth:
            chain.truncated = True
            continue
        chain.edges.append(e)
        _dfs(e.dst, out, graph, chain, path + [e.dst], depth + 1, max_depth)


def _preorder(chain: Chain, entry: str) -> list[str]:
    members = [entry]
    seen = {entry}
    for e in chain.edges:
        if not e.external and e.dst not in seen:
            seen.add(e.dst)
            members.append(e.dst)
    return members
```

CLI — in `build_parser()` add after the ingest parser block:

```python
    p_cg = sub.add_parser("codegraph",
                          help="Build the function-level code graph and show stats")
    p_cg.add_argument("path", help="Directory of source code to analyze")
    p_cg.add_argument("--persist", action="store_true",
                      help="Write the graph to the configured graph store")
    p_cg.add_argument("--json", action="store_true", help="Emit stats as JSON")
    p_cg.set_defaults(func=_cmd_codegraph)
```

```python
def _cmd_codegraph(ctx, args) -> int:
    import json as _json
    from collections import Counter

    from .codegraph.build import build_codegraph, persist_codegraph
    from .codegraph.chains import assemble_chains

    graph = build_codegraph(args.path, ctx.settings)
    chains = assemble_chains(graph,
                             max_depth=ctx.settings.codegraph_max_chain_depth)
    stats = {
        "functions": len(graph.functions),
        "functions_by_language": dict(Counter(
            f.language for f in graph.functions.values())),
        "edges_by_relation": dict(Counter(e.relation for e in graph.edges)),
        "unresolved_external_edges": sum(1 for e in graph.edges if e.external),
        "entry_points": sum(1 for c in chains),
        "truncated_chains": sum(1 for c in chains if c.truncated),
    }
    if args.persist:
        stats["persisted"] = persist_codegraph(graph, ctx.graph)
    if args.json:
        print(_json.dumps(stats, indent=2))
    else:
        for key, value in stats.items():
            print(f"{key}: {value}")
    return 0
```

Config — after `retrieve_include_graph` in `Settings`:

```python
    # Max call-chain depth for codegraph chain assembly (spec 4A). Env-only.
    codegraph_max_chain_depth: int = 12
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_codegraph_chains.py tests/test_cli.py -v`
Expected: all pass

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m pytest`
Expected: no regressions

- [ ] **Step 6: Commit**

```bash
git add src/opendomainmcp/codegraph/chains.py src/opendomainmcp/cli.py src/opendomainmcp/config.py tests/test_codegraph_chains.py tests/test_cli.py
git commit -m "feat: codegraph entry points, chain assembly, and CLI"
```

---

## Self-review notes

- **Spec coverage (4A slice):** symbol extraction layer with per-language plugins ✔ (T2–T5), name/scope resolution with low-confidence retention ✔ (T6), cross-language `executes_sql` + `http_call` ✔ (T1 scanner + T6), graph storage with new entity/relation types and file:line provenance ✔ (T7 `code_functions`), chain assembly with entry points and cycle truncation ✔ (T8). Deliberately deferred to 4B: LLM chain analysis, per-function summary backfill, `kind='chain'` retrievable items, pipeline/report integration, coverage fallback, loader/splitter wiring for `.vb`/`.sql`.
- **Placeholder scan:** every task carries complete test + implementation code; the two "adapt to the file's real pattern" notes (T7 FakeGraphStore internals, T8 CLI fixture) name exactly what to read and pin the assertions that must hold.
- **Type consistency:** `RawSymbols`/`FunctionDef`/`CallSite` produced by T2–T5 and consumed by T6 `resolve(per_file)`; `CodeGraph` consumed by T7 `persist_codegraph(graph, store)` and T8 `assemble_chains(graph, max_depth)`; store surface `upsert_functions(list[dict])`/`get_function(str)` identical across Null/Maria/Fake. Confidence constants defined once in T6 and referenced by name elsewhere.
- **Known risk:** tree-sitter node-type names (T2/T3) may drift by grammar version — both tasks instruct fixing node names against the actual grammar, never weakening assertions.
