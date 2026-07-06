# Codegraph Chain Analysis (Plan 4B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The LLM half of the graph-RAG architecture: bottom-up analysis of the call chains built by plan 4A — per-function summaries backfilled onto stored chunks (replacing isolated per-chunk extraction for code), chain-level knowledge stored as retrievable `kind='chain'` items, wired into ingest, retrieval, CLI, and the task worker.

**Architecture:** `codegraph/order.py` computes a bottom-up level ordering (SCC-condensed reverse topological). `codegraph/analyze_llm.py` holds the two LLM prompts (function summary; chain synthesis) behind an injectable `complete` callable using the existing extract provider settings. `models.py` gains `ChainItem` (duck-typed like `Article`, stored in a `{collection}__chains` sibling and RRF-fused into unified search). `codegraph/analyze.py` orchestrates: build graph → chains → level-parallel summaries with token-bounded context → chunk backfill (re-upsert re-embeds) → chain items → graph persistence with **real chunk ids** + stale `cg:` cleanup → per-chunk extraction fallback for uncovered code. Ingest gains a `codegraph_extract` mode that defers code extraction to this pass. Surfaces: `codegraph --analyze` CLI and an `analyze_chains` task runner.

**Tech Stack:** Python ≥ 3.11; existing anthropic/openai clients via extract settings; pytest offline (all LLM via injected fakes).

**Spec:** `docs/superpowers/specs/2026-07-06-codegraph-chain-extraction-design.md` (stages 5–6). 4A follow-ups folded in: stale `cg:` cleanup, `.pks` spec/body collision, loader wiring for `.vb`/`.sql`.

## Global Constraints

- Zero-network tests; LLM always an injected fake (`complete: Callable[[str, str], str]`).
- Chain analyzer uses the extraction provider settings (`resolved_extract_provider()`, `extraction_model`, `extract_base_url`, `request_timeout`, `max_retries`) — no new credential surface.
- Level-parallelism reuses `extract_concurrency` (local-LLM users already tune it).
- Fail Loud: per-function/per-chain LLM failures recorded in the result's `errors` list, never silently dropped; coverage stats always reported.
- Token bounding: direct-callee source context capped by `codegraph_context_chars` (new setting, default 16000 chars); deeper callees contribute summaries only.
- New settings: `codegraph_extract: bool = False` (EDITABLE), `retrieve_include_chains: bool = True` (EDITABLE), `codegraph_context_chars: int = 16000` (env-only).
- Chain item ids: `sha256(entry)` (one item per entry point; re-analysis overwrites) in sibling collection `{collection}__chains`.
- Real-chunk-id mapping: a function's chunks are those whose `source` path ends with the function's repo-relative `file` and whose line range overlaps `[start_line, end_line]`.
- Follow existing patterns exactly: Article storage/fusion for ChainItem; `run_extract` for the runner; `_extract_one` gating for the ingest switch.

## Parallel execution note

Waves: **[T1, T2, T3, T4 in parallel — disjoint files] → [T5, T6 in parallel] → [T7]**. Parallel implementers `git add` only their own files; retry commit on index.lock.

---

### Task 1: Loader wiring for VB.NET / PL-SQL

**Files:**
- Modify: `src/opendomainmcp/ingest/loader.py` (LANGUAGE_BY_EXT, line ~17)
- Modify: `src/opendomainmcp/codegraph/build.py` (drop `_EXTRA_EXTS`, use loader mapping)
- Test: `tests/test_loader.py` (append), `tests/test_codegraph_build.py` (must keep passing)

**Interfaces:**
- Produces: `LANGUAGE_BY_EXT` gains `".vb": "vbnet", ".sql": "plsql", ".pks": "plsql", ".pkb": "plsql", ".pls": "plsql"`. Ingesting these files now yields `kind="code"` chunks with those languages; `code_splitter` has no grammar for them so it line-window-falls-back (existing warning path). `build.py::_language_of` drops `_EXTRA_EXTS` and reads `LANGUAGE_BY_EXT` (still gated on `EXTRACTORS`). Behavior change (intended): `.sql`/`.vb` were previously unsupported-skipped at ingest; now they ingest.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loader.py`:

```python
def test_vbnet_and_plsql_extensions_load_as_code(tmp_path):
    from opendomainmcp.ingest.loader import load_file

    vb = tmp_path / "Billing.vb"
    vb.write_text("Module M\n  Sub Ping()\n  End Sub\nEnd Module\n")
    doc = load_file(vb)
    assert doc.kind == "code" and doc.language == "vbnet"

    for ext in (".sql", ".pks", ".pkb", ".pls"):
        f = tmp_path / f"pkg{ext}"
        f.write_text("CREATE OR REPLACE PROCEDURE p AS BEGIN NULL; END;\n")
        doc = load_file(f)
        assert doc.kind == "code" and doc.language == "plsql", ext
```

Append to `tests/test_pipeline.py`:

```python
def test_plsql_file_ingests_via_line_fallback(pipeline, store, tmp_path):
    f = tmp_path / "pkg_billing.pkb"
    f.write_text("CREATE OR REPLACE PACKAGE BODY pkg_billing AS\n"
                 "  PROCEDURE validate_amount(p IN NUMBER) IS\n"
                 "  BEGIN\n    NULL;\n  END validate_amount;\nEND pkg_billing;\n")
    report = pipeline.ingest_path(f)
    assert report.files_indexed == 1
    items = store.get_items(limit=10, where={"language": "plsql"})
    assert items and all(i["metadata"]["kind"] == "code" for i in items)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_loader.py tests/test_pipeline.py -k "vbnet or plsql" -v`
Expected: FAIL — `.vb`/`.sql` raise `UnsupportedFileError`

- [ ] **Step 3: Implement**

`ingest/loader.py` — append to `LANGUAGE_BY_EXT`:

```python
    ".vb": "vbnet",
    ".sql": "plsql",
    ".pks": "plsql",
    ".pkb": "plsql",
    ".pls": "plsql",
```

`codegraph/build.py` — delete `_EXTRA_EXTS` and simplify:

```python
def _language_of(path: Path) -> str | None:
    lang = LANGUAGE_BY_EXT.get(path.suffix.lower())
    return lang if lang in EXTRACTORS else None
```

(update the module docstring line that mentioned `_EXTRA_EXTS` accordingly).

- [ ] **Step 4: Run tests + codegraph regression**

Run: `.venv/bin/python -m pytest tests/test_loader.py tests/test_pipeline.py tests/test_codegraph_build.py -v`
Expected: all pass (build tests unaffected — same languages resolve).

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/ingest/loader.py src/opendomainmcp/codegraph/build.py tests/test_loader.py tests/test_pipeline.py
git commit -m "feat: ingest VB.NET and PL/SQL sources as code"
```

---

### Task 2: `codegraph/order.py` — bottom-up level ordering

**Files:**
- Create: `src/opendomainmcp/codegraph/order.py`
- Test: `tests/test_codegraph_order.py`

**Interfaces:**
- Consumes: `CodeGraph` (functions dict + edges; internal edges only matter).
- Produces: `bottom_up_levels(graph: CodeGraph) -> list[list[str]]` — level 0 = leaves (no internal outgoing edges), each later level depends only on earlier ones. Cycles: SCC members share one level (placed once all their non-SCC callees are placed). Deterministic: names sorted within a level.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_codegraph_order.py
"""Bottom-up (leaves-first) level ordering for chain analysis (plan 4B)."""

from opendomainmcp.codegraph.models import CodeGraph, FunctionDef, ResolvedEdge
from opendomainmcp.codegraph.order import bottom_up_levels


def _fd(q):
    return FunctionDef(qualified_name=q, file="F", start_line=1, end_line=2,
                       language="java")


def _edge(src, dst, external=False):
    return ResolvedEdge(src=src, dst=dst, relation="calls", confidence=1.0,
                        file="F", line=1, external=external)


def _graph(names, edges):
    return CodeGraph(functions={n: _fd(n) for n in names}, edges=edges)


def test_linear_chain_is_leaves_first():
    g = _graph(["a", "b", "c"], [_edge("a", "b"), _edge("b", "c")])
    assert bottom_up_levels(g) == [["c"], ["b"], ["a"]]


def test_diamond_shares_levels():
    g = _graph(["a", "b", "c", "d"],
               [_edge("a", "b"), _edge("a", "c"), _edge("b", "d"), _edge("c", "d")])
    assert bottom_up_levels(g) == [["d"], ["b", "c"], ["a"]]


def test_cycle_members_share_a_level():
    g = _graph(["a", "b", "c"],
               [_edge("a", "b"), _edge("b", "a"), _edge("a", "c")])
    levels = bottom_up_levels(g)
    assert levels[0] == ["c"]
    assert sorted(levels[1]) == ["a", "b"]


def test_external_edges_ignored():
    g = _graph(["a"], [_edge("a", "ext.proc", external=True)])
    assert bottom_up_levels(g) == [["a"]]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_codegraph_order.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

```python
# src/opendomainmcp/codegraph/order.py
"""Bottom-up level ordering for chain analysis.

Leaves (functions calling nothing internal) come first so their summaries
exist before any caller is analyzed. Cycles are condensed (iterative
Tarjan SCC) and their members share a level — within a cycle, analysis uses
whatever summaries are already available."""

from __future__ import annotations

from .models import CodeGraph


def bottom_up_levels(graph: CodeGraph) -> list[list[str]]:
    nodes = set(graph.functions)
    adj: dict[str, set[str]] = {n: set() for n in nodes}
    for e in graph.edges:
        if not e.external and e.src in nodes and e.dst in nodes and e.src != e.dst:
            adj[e.src].add(e.dst)

    scc_of, sccs = _tarjan(nodes, adj)
    # condensation: scc -> callee sccs
    cadj: dict[int, set[int]] = {i: set() for i in range(len(sccs))}
    for src, dsts in adj.items():
        for dst in dsts:
            if scc_of[src] != scc_of[dst]:
                cadj[scc_of[src]].add(scc_of[dst])

    # level = 1 + max(level of callee sccs); leaves = 0 (memoized DFS)
    level: dict[int, int] = {}

    def _level(i: int) -> int:
        if i not in level:
            level[i] = 1 + max((_level(j) for j in cadj[i]), default=-1)
        return level[i]

    for i in range(len(sccs)):
        _level(i)

    depth = max(level.values(), default=-1) + 1
    out: list[list[str]] = [[] for _ in range(depth)]
    for i, members in enumerate(sccs):
        out[level[i]].extend(members)
    return [sorted(lvl) for lvl in out if lvl]


def _tarjan(nodes: set[str], adj: dict[str, set[str]]):
    """Iterative Tarjan; returns (scc index per node, list of SCC member lists)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    scc_of: dict[str, int] = {}
    sccs: list[list[str]] = []
    counter = 0

    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, iter(sorted(adj[root])))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(adj[nxt]))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                members = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    scc_of[member] = len(sccs)
                    members.append(member)
                    if member == node:
                        break
                sccs.append(sorted(members))
    return scc_of, sccs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_codegraph_order.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/codegraph/order.py tests/test_codegraph_order.py
git commit -m "feat: bottom-up SCC level ordering for chain analysis"
```

---

### Task 3: `codegraph/analyze_llm.py` — ChainAnalyzer (LLM prompts)

**Files:**
- Create: `src/opendomainmcp/codegraph/analyze_llm.py`
- Modify: `src/opendomainmcp/extract/knowledge.py` (expose a public lenient-JSON helper)
- Test: `tests/test_codegraph_analyze_llm.py`

**Interfaces:**
- Consumes: extract settings; `FunctionDef`, `Chain`.
- Produces:

```python
@dataclass
class FunctionSummary:
    qualified_name: str
    summary: str
    rules: list[str] = field(default_factory=list)
    confidence: float = 0.0

class ChainAnalyzer:
    def __init__(self, settings, complete: Optional[Callable[[str, str], str]] = None)
        # complete(system, user) -> raw text; default built from settings
        # (anthropic messages / openai chat, mirroring the extractors)
    def summarize_function(self, fn: FunctionDef, source: str,
                           callee_sources: dict[str, str],
                           callee_summaries: dict[str, FunctionSummary]) -> FunctionSummary
    def analyze_chain(self, chain, summaries: dict[str, FunctionSummary]) -> dict
        # {"title": str, "body": str, "rules": list[str]}
```

- `extract/knowledge.py` gains `parse_llm_json(raw: str) -> dict` — public wrapper for the existing fence-stripping + `_loads_lenient` behavior; `_parse` is refactored to call it (no behavior change).
- Prompts (fixed): `_FUNC_SYSTEM` demands ONLY JSON `{"summary", "rules", "confidence"}` where rules are business rules/constraints visible in the code; `_CHAIN_SYSTEM` demands ONLY JSON `{"title", "body", "rules"}` describing the end-to-end workflow from entry point through the layers.
- Failures raise (caller records them; Fail Loud).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_codegraph_analyze_llm.py
"""ChainAnalyzer prompt/parse behavior with an injected fake LLM (plan 4B)."""

import json

import pytest

from opendomainmcp.codegraph.analyze_llm import ChainAnalyzer, FunctionSummary
from opendomainmcp.codegraph.chains import Chain
from opendomainmcp.codegraph.models import FunctionDef
from opendomainmcp.config import Settings


def _fd(q, language="java"):
    return FunctionDef(qualified_name=q, file="F.java", start_line=1,
                       end_line=9, language=language)


def test_summarize_function_parses_json_and_builds_context():
    seen = {}

    def fake(system, user):
        seen["system"], seen["user"] = system, user
        return json.dumps({"summary": "Validates order amount.",
                           "rules": ["amount must not be negative"],
                           "confidence": 0.9})

    analyzer = ChainAnalyzer(Settings(), complete=fake)
    fs = analyzer.summarize_function(
        _fd("a.B.validate"), "if (amt < 0) throw ...",
        callee_sources={"pkg.check": "PROCEDURE check ..."},
        callee_summaries={"deep.fn": FunctionSummary("deep.fn", "Logs stuff.")},
    )
    assert fs.qualified_name == "a.B.validate"
    assert fs.rules == ["amount must not be negative"] and fs.confidence == 0.9
    # context assembly: own source, 1-hop callee source, deep summary
    assert "if (amt < 0)" in seen["user"]
    assert "PROCEDURE check" in seen["user"]
    assert "Logs stuff." in seen["user"]
    assert "JSON" in seen["system"]


def test_summarize_function_tolerates_fenced_json():
    def fake(system, user):
        return '```json\n{"summary": "S", "rules": [], "confidence": 0.5}\n```'

    fs = ChainAnalyzer(Settings(), complete=fake).summarize_function(
        _fd("x.Y.z"), "code", {}, {})
    assert fs.summary == "S" and fs.confidence == 0.5


def test_analyze_chain_includes_member_summaries_in_order():
    seen = {}

    def fake(system, user):
        seen["user"] = user
        return json.dumps({"title": "Charge flow", "body": "Entry to DB.",
                           "rules": ["r1"]})

    chain = Chain(entry="api.Ctl.post", members=["api.Ctl.post", "svc.A.a"])
    summaries = {
        "api.Ctl.post": FunctionSummary("api.Ctl.post", "Receives request."),
        "svc.A.a": FunctionSummary("svc.A.a", "Does work."),
    }
    out = ChainAnalyzer(Settings(), complete=fake).analyze_chain(chain, summaries)
    assert out == {"title": "Charge flow", "body": "Entry to DB.", "rules": ["r1"]}
    assert seen["user"].index("Receives request.") < seen["user"].index("Does work.")


def test_llm_failure_raises():
    def fake(system, user):
        return "not json at all {{{"

    with pytest.raises(Exception):
        ChainAnalyzer(Settings(), complete=fake).summarize_function(
            _fd("x.Y.z"), "code", {}, {})


def test_parse_llm_json_public_helper():
    from opendomainmcp.extract.knowledge import parse_llm_json

    assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_llm_json('{"a": 1}') == {"a": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_codegraph_analyze_llm.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

`extract/knowledge.py` — refactor the fence-stripping + lenient load out of `_parse` into a public function (keep `_parse` behavior identical):

```python
def parse_llm_json(raw: str) -> dict:
    """Extract and parse the JSON object from an LLM reply.

    Handles markdown code fences and lenient/repaired JSON — the shared
    tolerance layer for every LLM-JSON call site (extraction, chain analysis)."""
    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("```")[1]
        if candidate.startswith("json"):
            candidate = candidate[4:]
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1:
        candidate = candidate[start:end + 1]
    return _loads_lenient(candidate)
```

(then make `_parse` call `parse_llm_json(raw)` in place of its inline equivalent — read `_parse` first and keep its exact fallback semantics; if its existing fence handling differs in detail, move that exact code into `parse_llm_json` rather than the sketch above.)

```python
# src/opendomainmcp/codegraph/analyze_llm.py
"""LLM prompts for chain analysis (plan 4B).

Two calls: a per-function summary (bottom-up, with 1-hop callee source and
deeper summaries as context) and a per-chain end-to-end synthesis. The LLM
transport is an injectable ``complete(system, user) -> str`` so tests run
offline; the default transport reuses the extraction provider settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..extract.knowledge import parse_llm_json

_FUNC_SYSTEM = (
    "You analyze one function from a business application, with context from "
    "the functions it calls. Respond with ONLY a JSON object:\n"
    '  "summary": 1-2 sentences on what the function does in business terms,\n'
    '  "rules": a list of short business rules/constraints enforced here '
    "(may be empty),\n"
    '  "confidence": a number 0..1.\n'
    "No prose outside the JSON."
)

_CHAIN_SYSTEM = (
    "You are given an end-to-end call chain from a business application: an "
    "entry point followed by the functions it reaches (with per-function "
    "summaries). Respond with ONLY a JSON object:\n"
    '  "title": a short name for this business flow,\n'
    '  "body": a paragraph describing the end-to-end workflow across layers,\n'
    '  "rules": business rules/constraints enforced anywhere along the chain.\n'
    "No prose outside the JSON."
)


@dataclass
class FunctionSummary:
    qualified_name: str
    summary: str
    rules: list[str] = field(default_factory=list)
    confidence: float = 0.0


def _default_complete(settings) -> Callable[[str, str], str]:
    provider = settings.resolved_extract_provider()
    base_url = settings.extract_base_url or None
    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(timeout=settings.request_timeout,
                        max_retries=settings.max_retries,
                        **({"base_url": base_url} if base_url else {}))

        def complete(system: str, user: str) -> str:
            resp = client.chat.completions.create(
                model=settings.extraction_model, max_tokens=1200,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}])
            return resp.choices[0].message.content or ""

        return complete
    import anthropic

    client = anthropic.Anthropic(timeout=settings.request_timeout,
                                 max_retries=settings.max_retries,
                                 **({"base_url": base_url} if base_url else {}))

    def complete(system: str, user: str) -> str:
        msg = client.messages.create(model=settings.extraction_model,
                                     max_tokens=1200, system=system,
                                     messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in msg.content if b.type == "text")

    return complete


class ChainAnalyzer:
    def __init__(self, settings,
                 complete: Optional[Callable[[str, str], str]] = None):
        self._settings = settings
        self._complete = complete or _default_complete(settings)

    def summarize_function(self, fn, source: str,
                           callee_sources: dict[str, str],
                           callee_summaries: dict[str, "FunctionSummary"],
                           ) -> FunctionSummary:
        parts = [f"Function: {fn.qualified_name} ({fn.language})",
                 f"Source:\n{source}"]
        for name, src in callee_sources.items():
            parts.append(f"\nDirect callee {name}:\n{src}")
        for name, fs in callee_summaries.items():
            parts.append(f"\nDeeper callee {name} (summary): {fs.summary}")
        data = parse_llm_json(self._complete(_FUNC_SYSTEM, "\n".join(parts)))
        return FunctionSummary(
            qualified_name=fn.qualified_name,
            summary=str(data.get("summary", "")).strip(),
            rules=[str(r).strip() for r in data.get("rules", []) if str(r).strip()],
            confidence=float(data.get("confidence", 0.0) or 0.0),
        )

    def analyze_chain(self, chain, summaries: dict[str, FunctionSummary]) -> dict:
        lines = [f"Entry point: {chain.entry}"]
        for member in chain.members:
            fs = summaries.get(member)
            lines.append(f"- {member}: {fs.summary if fs else '(no summary)'}")
            if fs and fs.rules:
                for rule in fs.rules:
                    lines.append(f"    rule: {rule}")
        if chain.truncated:
            lines.append("(note: chain truncated by cycle/depth limit)")
        data = parse_llm_json(self._complete(_CHAIN_SYSTEM, "\n".join(lines)))
        return {
            "title": str(data.get("title", chain.entry)).strip() or chain.entry,
            "body": str(data.get("body", "")).strip(),
            "rules": [str(r).strip() for r in data.get("rules", [])
                      if str(r).strip()],
        }
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_codegraph_analyze_llm.py tests/test_extract.py -v`
Expected: all pass (extract tests guard the `_parse` refactor).

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/codegraph/analyze_llm.py src/opendomainmcp/extract/knowledge.py tests/test_codegraph_analyze_llm.py
git commit -m "feat: ChainAnalyzer LLM prompts with injectable transport"
```

---

### Task 4: `ChainItem` — storage, retrieval fusion, citations

**Files:**
- Modify: `src/opendomainmcp/models.py` (add `ChainItem` after `Article`)
- Modify: `src/opendomainmcp/retrieval/unified.py` (fuse `__chains` sibling)
- Modify: `src/opendomainmcp/query/rag.py` (`_source_label`, `_citations` handle kind='chain')
- Modify: `src/opendomainmcp/config.py` (`retrieve_include_chains: bool = True`, EDITABLE)
- Test: `tests/test_chain_items.py`

**Interfaces:**
- Produces (mirror `Article`'s duck-typed store contract — read `Article` in models.py first and match its shape exactly):

```python
@dataclass
class ChainItem:
    entry: str
    title: str
    body: str
    rules: list[str] = field(default_factory=list)
    members: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)       # "file:start-end"
    member_chunk_ids: list[str] = field(default_factory=list)
    truncated: bool = False

    @staticmethod
    def id_for_entry(entry: str) -> str: ...   # sha256 hex of entry

    @property
    def id(self) -> str: ...
    @property
    def text(self) -> str: ...                 # body + bulleted rules
    def embedding_text(self) -> str: ...       # title + text + members
    def metadata(self) -> dict: ...            # kind="chain", title, entry,
                                               # members/sources/rules joined,
                                               # member_chunk_ids joined, truncated
```

- `search_unified` fuses a `{collection}__chains` sibling exactly like articles, gated by `settings.retrieve_include_chains` (skip when sibling count is 0).
- `rag.py`: `_source_label` returns the chain title (fallback entry); `_citations` emits `type_="chain"` with `source=title`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_chain_items.py
"""ChainItem storage + unified retrieval fusion + citations (plan 4B)."""

from opendomainmcp.config import Settings
from opendomainmcp.models import ChainItem


def _item():
    return ChainItem(
        entry="api.Ctl.post", title="Charge flow",
        body="Validates and persists a charge.",
        rules=["amount must not be negative"],
        members=["api.Ctl.post", "svc.A.a"],
        sources=["Ctl.java:10-30", "A.java:5-40"],
        member_chunk_ids=["c1", "c2"],
    )


def test_chain_item_id_stable_and_metadata_flat():
    item = _item()
    assert item.id == ChainItem.id_for_entry("api.Ctl.post")
    meta = item.metadata()
    assert meta["kind"] == "chain" and meta["title"] == "Charge flow"
    assert "api.Ctl.post" in meta["members"]
    assert all(not isinstance(v, (list, dict)) for v in meta.values())
    assert "amount must not be negative" in item.text
    assert "Charge flow" in item.embedding_text()


def test_chain_items_fuse_into_unified_search(store):
    from opendomainmcp.models import Chunk
    from opendomainmcp.retrieval import search_unified

    store.upsert([Chunk(text="def charge(): pass", source="a.py", kind="code")])
    chains = store.sibling(f"{store.stats()['collection']}__chains")
    chains.upsert([_item()])

    hits = search_unified(store, "charge flow rules", top_k=5,
                          settings=Settings(retrieve_include_chains=True))
    kinds = {h.metadata.get("kind") for h in hits}
    assert "chain" in kinds

    hits_off = search_unified(store, "charge flow rules", top_k=5,
                              settings=Settings(retrieve_include_chains=False))
    assert "chain" not in {h.metadata.get("kind") for h in hits_off}


def test_chain_citations_and_source_label():
    from opendomainmcp.models import SearchResult
    from opendomainmcp.query.rag import _citations, _source_label

    r = SearchResult(id=_item().id, text=_item().text, score=0.9,
                     metadata=_item().metadata())
    assert _source_label(r) == "Charge flow"
    cite = _citations([r])[0]
    assert cite["type"] == "chain" and cite["source"] == "Charge flow"


def test_retrieve_include_chains_is_editable():
    from opendomainmcp.config import EDITABLE_FIELDS

    assert "retrieve_include_chains" in EDITABLE_FIELDS
```

Note for the implementer: check `SearchResult`'s actual constructor fields and `_citations`' actual output keys in the code first — adapt the test's construction/keys to reality, keeping the assertions' intent (label = title; a chain-typed citation).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chain_items.py -v`
Expected: FAIL — `ImportError: cannot import name 'ChainItem'`

- [ ] **Step 3: Implement**

`models.py` — add after `Article`, mirroring its style:

```python
@dataclass
class ChainItem:
    """End-to-end call-chain knowledge synthesized by chain analysis (4B).

    Duck-types the store contract (id/text/embedding_text/metadata) like
    Article; lives in the ``<collection>__chains`` sibling collection."""

    entry: str
    title: str
    body: str
    rules: list[str] = field(default_factory=list)
    members: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    member_chunk_ids: list[str] = field(default_factory=list)
    truncated: bool = False

    @staticmethod
    def id_for_entry(entry: str) -> str:
        return hashlib.sha256(entry.encode("utf-8")).hexdigest()

    @property
    def id(self) -> str:
        return self.id_for_entry(self.entry)

    @property
    def text(self) -> str:
        rules = "".join(f"\n- {r}" for r in self.rules)
        return f"{self.body}{rules}" if rules else self.body

    def embedding_text(self) -> str:
        return f"{self.title}\n{self.text}\nFunctions: {', '.join(self.members)}"

    def metadata(self) -> dict:
        return {
            "kind": "chain",
            "title": self.title,
            "entry": self.entry,
            "members": ", ".join(self.members),
            "sources": " | ".join(self.sources),
            "rules": " | ".join(self.rules),
            "member_chunk_ids": ", ".join(self.member_chunk_ids),
            "truncated": self.truncated,
        }
```

`retrieval/unified.py` — after the article fusion block, add the identical pattern for chains (read the function and extend the fusion lists rather than duplicating the whole flow):

```python
    if getattr(settings, "retrieve_include_chains", True):
        chain_store = store.sibling(f"{store.stats()['collection']}__chains")
        if chain_store.stats()["count"] > 0:
            chain_hits = chain_store.search(query, top_k=top_k, mode=mode)
            pool.update({r.id: r for r in chain_hits})
            ranked_lists.append([h.id for h in chain_hits])
```

(adapt names to the function's actual local variables; the fusion must remain one `rrf_fuse` call over 2–3 ranked lists).

`query/rag.py` — extend `_source_label` and `_citations`:

```python
    if meta.get("kind") == "chain":
        return meta.get("title") or meta.get("entry") or r.id
```

```python
        elif kind == "chain":
            source = _source_label(r)
            symbol = None
            type_ = "chain"
```

`config.py` — add after `retrieve_include_articles`:

```python
    # Include chain-analysis items (the <base>__chains collection) in ask/search
    # retrieval, fused with chunks. No chains ingested == today's behavior.
    retrieve_include_chains: bool = True
```

and `"retrieve_include_chains",` in `EDITABLE_FIELDS` after `"retrieve_include_articles",`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_chain_items.py tests/test_config.py -k "chain or editable" -v` then the retrieval/rag test files (`tests/test_retrieval*.py tests/test_rag*.py` — locate exact names with `ls tests | grep -iE 'rag|retriev|unified'`)
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/models.py src/opendomainmcp/retrieval/unified.py src/opendomainmcp/query/rag.py src/opendomainmcp/config.py tests/test_chain_items.py
git commit -m "feat: ChainItem storage, retrieval fusion, and citations"
```

---

### Task 5: `codegraph/analyze.py` — the analysis pass

**Files:**
- Create: `src/opendomainmcp/codegraph/analyze.py`
- Modify: `src/opendomainmcp/codegraph/build.py` (`persist_codegraph` gains optional real-chunk-id mapping)
- Modify: `src/opendomainmcp/graph/store.py` (+ `delete_codegraph()` on Maria/Null), `tests/conftest.py` (FakeGraphStore.delete_codegraph)
- Test: `tests/test_codegraph_analyze.py`

**Interfaces:**
- Consumes: `build_codegraph`, `assemble_chains`, `bottom_up_levels` (T2), `ChainAnalyzer`/`FunctionSummary` (T3), `ChainItem` (T4), `get_extractor`.
- Produces:

```python
def analyze_corpus(root: str | Path, store, settings, graph_store,
                   progress: Optional[Callable[[dict], None]] = None,
                   analyzer: Optional[ChainAnalyzer] = None,
                   extractor=None) -> dict
# returns {"functions_analyzed", "chains_stored", "chunks_backfilled",
#          "fallback_extracted", "coverage", "errors": [...]}
```

Pipeline of the pass:
1. `graph = build_codegraph(root, settings)`; `chains = assemble_chains(graph, settings.codegraph_max_chain_depth)`; `levels = bottom_up_levels(graph)`.
2. **Sources:** slice each function's text from `root/fn.file` lines `[start_line, end_line]` (read file once per file, cache).
3. **Summaries (bottom-up, level-parallel):** per level, `ThreadPoolExecutor(max_workers=extract_concurrency)`. Context per function: direct-callee full sources until the running total exceeds `settings.codegraph_context_chars` (then stop adding sources — remaining direct callees fall back to summaries), deeper callees always summaries. Direct callees = internal `calls`/`executes_sql` edges from this function. LLM failure → `errors.append({"function": name, "error": repr(exc)})`, function gets no summary (callers see "(no summary)").
4. **Chunk backfill:** map repo-relative `fn.file` to stored sources by suffix match (`source == rel or source.endswith("/" + rel)` over `store.get_all_sources()`, computed once). For each matched source, load its items once (`get_ids_for_source` + `get_item`), find chunks overlapping the function's line range; rebuild `Chunk` from the item (text + metadata fields), attach `KnowledgeUnit(summary=fs.summary, concepts=fs.rules[:8], confidence=fs.confidence, review_status="pending" if settings.review_mode else "approved")`, and `store.upsert([chunk])` (re-embeds with the enriched text). Record function → real chunk ids.
5. **Chain items:** for each chain with at least one summarized member: `analyze_chain` → `ChainItem` (sources = `file:start-end` of members; member_chunk_ids from step 4) → upsert into `store.sibling(f"{collection}__chains")`. LLM failure → errors, chain skipped.
6. **Graph persistence with real ids:** `graph_store.delete_codegraph()` (removes prior `cg:%` edge/entity_chunks rows and code_functions), then `persist_codegraph(graph, graph_store, chunk_ids_by_function=...)` — extended signature: when a function has real chunk ids, emit one Entity/entity-chunk link per real id and use the first real id on its edges; otherwise fall back to `_synthetic_chunk_id`.
7. **Fallback per-chunk extraction:** code chunks under analyzed sources whose ids were NOT backfilled → `extractor.extract(...)` (default `get_extractor(settings)`) + `update_metadata`; count as `fallback_extracted`.
8. Coverage = backfilled / (backfilled + fallback_extracted) (0.0 when denominator 0); progress events per stage (`stage`: "codegraph", "summaries", "chains", "backfill", "fallback").

New store surface (all three stores):

```python
def delete_codegraph(self) -> None
    # Maria: DELETE FROM edges WHERE collection=%s AND chunk_id LIKE 'cg:%';
    #        same for entity_chunks; DELETE FROM code_functions WHERE collection=%s;
    #        entities of types function/procedure/endpoint/external whose last
    #        entity_chunks row vanished are left in place (harmless; re-upserted next persist).
    # Null: pass. Fake: mirror Maria semantics on the backing dicts.
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_codegraph_analyze.py
"""End-to-end chain analysis pass over a mixed corpus with fake LLM (plan 4B)."""

import json

from opendomainmcp.codegraph.analyze import analyze_corpus
from opendomainmcp.codegraph.analyze_llm import ChainAnalyzer
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
PLSQL = """CREATE OR REPLACE PACKAGE BODY pkg_billing AS
  PROCEDURE validate_amount(p_amt IN NUMBER) IS
  BEGIN
    NULL;
  END validate_amount;
END pkg_billing;
"""


def _fake_complete(system, user):
    if "call chain" in system:
        return json.dumps({"title": "Chain title", "body": "End to end.",
                           "rules": ["chain rule"]})
    return json.dumps({"summary": f"Summary.", "rules": ["amount >= 0"],
                       "confidence": 0.8})


def _setup(tmp_path, pipeline):
    (tmp_path / "BillingService.java").write_text(JAVA)
    (tmp_path / "pkg_billing.pkb").write_text(PLSQL)
    pipeline.ingest_path(tmp_path)


def test_analyze_backfills_chunks_and_stores_chains(tmp_path, pipeline, store,
                                                    fake_graph):
    _setup(tmp_path, pipeline)
    settings = Settings(codegraph_extract=True)
    analyzer = ChainAnalyzer(settings, complete=_fake_complete)
    result = analyze_corpus(tmp_path, store, settings, fake_graph,
                            analyzer=analyzer)

    assert result["functions_analyzed"] >= 3   # charge, validate, validate_amount
    assert result["chains_stored"] >= 1
    assert result["chunks_backfilled"] >= 1
    assert result["errors"] == []

    # backfilled chunk carries the summary in metadata (searchable enrichment)
    items = store.get_items(limit=50, where={"language": "java"})
    assert any(i["metadata"].get("summary") == "Summary." for i in items)

    # chain item retrievable from the sibling collection
    chains = store.sibling(f"{store.stats()['collection']}__chains")
    got = chains.get_items(limit=10)
    assert got and got[0]["metadata"]["kind"] == "chain"
    assert got[0]["metadata"]["title"] == "Chain title"

    # graph persisted with REAL chunk ids for backfilled functions
    ent = fake_graph.get_entity("com.acme.billing.billingservice.validate")
    assert ent and any(not c.startswith("cg:") for c in ent["chunk_ids"])


def test_analyze_reports_llm_failures_and_falls_back(tmp_path, pipeline, store,
                                                     fake_graph, fake_extractor):
    _setup(tmp_path, pipeline)

    def broken(system, user):
        raise RuntimeError("llm down")

    settings = Settings(codegraph_extract=True)
    result = analyze_corpus(tmp_path, store, settings, fake_graph,
                            analyzer=ChainAnalyzer(settings, complete=broken),
                            extractor=fake_extractor)
    assert result["functions_analyzed"] == 0
    assert result["errors"]                          # failures recorded
    assert result["fallback_extracted"] >= 1          # uncovered code chunks extracted
    assert result["chains_stored"] == 0


def test_delete_codegraph_clears_synthetic_rows(fake_graph):
    from opendomainmcp.graph.models import Edge, Entity

    fake_graph.upsert_entities([Entity(normalized_name="f", display_name="F",
                                       type="function", chunk_id="cg:abc")])
    fake_graph.upsert_edges([Edge(src="f", dst="g", relation_type="calls",
                                  chunk_id="cg:abc")])
    fake_graph.upsert_functions([{"qualified_name": "F", "file": "F.java",
                                  "start_line": 1, "end_line": 2,
                                  "language": "java", "signature": "F",
                                  "kind": "function"}])
    fake_graph.delete_codegraph()
    assert fake_graph.get_function("F") is None
    ent = fake_graph.get_entity("f")
    assert not ent or not any(c.startswith("cg:") for c in ent["chunk_ids"])
```

Note: `pipeline`/`store`/`fake_graph`/`fake_extractor` are the existing conftest fixtures. `Settings(codegraph_extract=True)` requires Task 6's field; if Task 6 hasn't landed when you start, use `Settings()` — the field is not read by analyze_corpus itself.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_codegraph_analyze.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

Write `codegraph/analyze.py` per the Interfaces contract (stages 1–8). Key skeleton:

```python
# src/opendomainmcp/codegraph/analyze.py
"""Bottom-up LLM chain analysis (plan 4B, spec stage 5).

Leaves first: each function is summarized with its direct callees' source
(token-bounded) and deeper callees' summaries, so cost stays bounded at any
chain depth and shared subtrees are analyzed once. Summaries backfill the
already-stored chunks (re-upsert re-embeds the enriched text); whole chains
become retrievable ChainItems; the code graph is re-persisted with real
chunk ids. Anything the LLM could not cover falls back to the legacy
per-chunk extractor — coverage is always reported (Fail Loud)."""
```

with functions: `_function_sources(root, graph)` (per-file cached line slicing), `_direct_callees(graph)` (internal calls/executes_sql adjacency), `_summarize_levels(...)` (ThreadPoolExecutor per level; errors list), `_backfill(...)` (suffix map of `store.get_all_sources()`; overlap match; rebuild Chunk exactly like `tasks/runners.py:run_extract` does; returns `chunk_ids_by_function`), `_store_chains(...)`, `_fallback_extract(...)`, and the public `analyze_corpus` gluing them with progress events.

`build.py::persist_codegraph` — extended signature:

```python
def persist_codegraph(graph: CodeGraph, store,
                      chunk_ids_by_function: Optional[dict[str, list[str]]] = None) -> dict:
```

for each function: `ids = (chunk_ids_by_function or {}).get(fn.qualified_name) or [_synthetic_chunk_id(fn.qualified_name)]` — one Entity per id (entity_chunks accumulates them); edges use `ids[0]`. Everything else unchanged.

`graph/store.py` — `delete_codegraph` on `NullGraphStore` (pass) and `MariaGraphStore`:

```python
    def delete_codegraph(self) -> None:
        """Remove codegraph rows: synthetic cg:* links and all function provenance."""
        with self._connect() as conn:  # match the class's actual connection helper
            with conn.cursor() as cur:
                cur.execute("DELETE FROM edges WHERE collection=%s AND chunk_id LIKE 'cg:%%'",
                            (self._collection,))
                cur.execute("DELETE FROM entity_chunks WHERE collection=%s AND chunk_id LIKE 'cg:%%'",
                            (self._collection,))
                cur.execute("DELETE FROM code_functions WHERE collection=%s",
                            (self._collection,))
            conn.commit()
```

(read the class first and mirror its real connection/commit idiom). FakeGraphStore: filter backing edges/entity_chunks with `chunk_id.startswith("cg:")`, clear `code_functions`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_codegraph_analyze.py tests/test_codegraph_build.py tests/test_graph_store_fake.py -v`
Expected: all pass

- [ ] **Step 5: Full suite**

Run: `.venv/bin/python -m pytest`
Expected: no regressions

- [ ] **Step 6: Commit**

```bash
git add src/opendomainmcp/codegraph/analyze.py src/opendomainmcp/codegraph/build.py src/opendomainmcp/graph/store.py tests/conftest.py tests/test_codegraph_analyze.py
git commit -m "feat: bottom-up chain analysis pass with backfill and real chunk ids"
```

---

### Task 6: Ingest gating — `codegraph_extract` mode

**Files:**
- Modify: `src/opendomainmcp/config.py` (field + EDITABLE_FIELDS)
- Modify: `src/opendomainmcp/ingest/pipeline.py` (`_extract_all` skips code chunks in codegraph mode)
- Modify: `src/opendomainmcp/ingest/checkpoint.py` (`extractor_signature` includes the flag)
- Test: `tests/test_pipeline.py` (append), `tests/test_ingest_checkpoint.py` (append)

**Interfaces:**
- Produces: `Settings.codegraph_extract: bool = False` (EDITABLE). When True, `_extract_all` filters `chunk.kind == "code"` chunks out of LLM extraction (they store with empty knowledge; the analyze pass backfills). Non-code chunks extract as today. `extractor_signature` gains a fourth segment `str(bool(settings.codegraph_extract))` so checkpoints invalidate when the mode flips.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:

```python
def test_codegraph_extract_mode_skips_code_chunks(store, fake_extractor,
                                                  fake_graph, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline

    (tmp_path / "billing.py").write_text("def charge():\n    return 1\n")
    (tmp_path / "notes.md").write_text("# Pricing\nRules for pricing.\n")
    settings = Settings(chunk_size=200, chunk_overlap=20, codegraph_extract=True)
    Pipeline(store, fake_extractor, settings, graph=fake_graph).ingest_path(tmp_path)

    code = store.get_items(limit=50, where={"kind": "code"})
    text = store.get_items(limit=50, where={"kind": "text"})
    assert code and all(not i["metadata"].get("summary") for i in code)
    assert text and any(i["metadata"].get("summary") for i in text)
```

Append to `tests/test_ingest_checkpoint.py`:

```python
def test_extractor_signature_includes_codegraph_mode():
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.checkpoint import extractor_signature

    on = extractor_signature(Settings(codegraph_extract=True))
    off = extractor_signature(Settings(codegraph_extract=False))
    assert on != off
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py::test_codegraph_extract_mode_skips_code_chunks tests/test_ingest_checkpoint.py -k codegraph -v`
Expected: FAIL — Settings has no field `codegraph_extract`

- [ ] **Step 3: Implement**

`config.py` — after `codegraph_max_chain_depth`:

```python
    # When on, code chunks skip per-chunk LLM extraction at ingest — the
    # codegraph chain-analysis pass (codegraph --analyze / analyze_chains task)
    # backfills their summaries with call-chain context instead. Documents are
    # unaffected. Off == today's behavior.
    codegraph_extract: bool = False
```

plus `"codegraph_extract",` in `EDITABLE_FIELDS`.

`ingest/pipeline.py` — top of `_extract_all`:

```python
        if getattr(self._settings, "codegraph_extract", False):
            # Code chunks are analyzed with call-chain context by the codegraph
            # analyze pass; only non-code content extracts per-chunk here.
            chunks = [c for c in chunks if c.kind != "code"]
            if not chunks:
                return
```

`ingest/checkpoint.py` — extend the signature list:

```python
        str(bool(getattr(settings, "codegraph_extract", False))),
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py tests/test_ingest_checkpoint.py tests/test_config.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/config.py src/opendomainmcp/ingest/pipeline.py src/opendomainmcp/ingest/checkpoint.py tests/test_pipeline.py tests/test_ingest_checkpoint.py
git commit -m "feat: codegraph_extract mode defers code extraction to chain analysis"
```

---

### Task 7: Surfaces — CLI `--analyze` and `analyze_chains` task

**Files:**
- Modify: `src/opendomainmcp/cli.py` (`codegraph` subcommand gains `--analyze`)
- Modify: `src/opendomainmcp/tasks/runners.py` (`run_analyze_chains` + RUNNERS)
- Modify: `src/opendomainmcp/api/task_routes.py` (`_title` entry)
- Test: `tests/test_cli.py` (append), `tests/test_task_runners.py` or the existing task-runner test file (append — find it with `ls tests | grep -i task`)

**Interfaces:**
- CLI: `opendomainmcp codegraph PATH --analyze [--persist] [--json]` — runs `analyze_corpus(path, ctx.store, ctx.settings, ctx.graph, progress=stderr-printer)`; `--analyze` implies persistence handled inside the pass (the separate `--persist` static path is unchanged for non-analyze runs); prints the result dict. `--analyze` with a NullGraphStore is allowed (graph writes no-op; store writes still real) but prints a warning line to stderr.
- Runner: `run_analyze_chains(ctx, store, task, is_cancelled)` — params `{"path": str}`; children = one entry per chain entry point (set after codegraph build via `set_children_names`); coarse cancellation checked between stages; result = the analyze_corpus dict. Registered as `RUNNERS["analyze_chains"]`; `_title` → `f"Analyze chains {params.get('path', '')}"`.
- Fail Loud: runner records analyze errors into the task's failures list.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py` (reuse the codegraph CLI fake-ctx pattern; the fake ctx's store must be the real fake `store` fixture so analyze can read/write):

```python
def test_codegraph_analyze_cli(tmp_path, capsys, monkeypatch, store, fake_graph):
    import opendomainmcp.cli as cli

    (tmp_path / "A.java").write_text(
        "public class A { public void run() { help(); } void help() {} }")

    called = {}

    def fake_analyze(root, st, settings, graph, progress=None, analyzer=None,
                     extractor=None):
        called["root"] = str(root)
        return {"functions_analyzed": 2, "chains_stored": 1,
                "chunks_backfilled": 0, "fallback_extracted": 0,
                "coverage": 0.0, "errors": []}

    monkeypatch.setattr("opendomainmcp.codegraph.analyze.analyze_corpus",
                        fake_analyze)
    # fake ctx per the file's established pattern, with .settings/.store/.graph
    ...
    rc = cli.main(["codegraph", str(tmp_path), "--analyze"])
    assert rc == 0 and called["root"] == str(tmp_path)
    assert "chains_stored" in capsys.readouterr().out
```

(the `...` is the file's existing fake-ctx + monkeypatched `build_context` wiring — copy it from the neighboring codegraph CLI tests; assertions stay).

Append to the task-runner test file, following its existing conventions:

```python
def test_run_analyze_chains_runner(tmp_path, monkeypatch):
    # build a minimal ctx/store/task per this file's existing pattern, then:
    from opendomainmcp.tasks.runners import RUNNERS

    assert "analyze_chains" in RUNNERS
    # monkeypatch analyze_corpus like the CLI test; run the runner; assert
    # task result contains the dict and children were set from chain entries.
```

(the plan intentionally defers to the file's local fixtures — read them first; the two assertions that must hold: registration in RUNNERS, and the analyze result dict lands in `task.result` via `store.update(..., result=...)`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k analyze -v`
Expected: FAIL — `unrecognized arguments: --analyze`

- [ ] **Step 3: Implement**

`cli.py` — add to the codegraph parser:

```python
    p_cg.add_argument("--analyze", action="store_true",
                      help="Run the LLM chain-analysis pass (backfills chunk "
                           "summaries and stores chain items)")
```

In `_cmd_codegraph`, before the static stats path:

```python
    if args.analyze:
        from .codegraph.analyze import analyze_corpus
        from .graph.store import NullGraphStore

        if isinstance(ctx.graph, NullGraphStore):
            print("warning: graph store not configured — chain analysis will "
                  "skip graph persistence", file=sys.stderr)

        def progress(event):
            print(f"[{event['stage']:>9}] {event.get('detail', '')}",
                  file=sys.stderr)

        result = analyze_corpus(args.path, ctx.store, ctx.settings, ctx.graph,
                                progress=progress)
        if args.json:
            print(_json.dumps(result, indent=2))
        else:
            for key, value in result.items():
                print(f"{key}: {value}")
        return 0
```

`tasks/runners.py` — mirror `run_extract`'s structure:

```python
def run_analyze_chains(ctx, store, task, is_cancelled) -> None:
    from ..codegraph.analyze import analyze_corpus
    from ..codegraph.build import build_codegraph
    from ..codegraph.chains import assemble_chains

    path = task.params["path"]
    graph = build_codegraph(path, ctx.settings)
    chains = assemble_chains(graph, ctx.settings.codegraph_max_chain_depth)
    store.set_children_names(task.id, [c.entry for c in chains])
    if is_cancelled():
        return
    result = analyze_corpus(path, ctx.store, ctx.settings, ctx.graph)
    failures = [{"name": e.get("function") or e.get("chain") or "?",
                 "status": "error"} for e in result.get("errors", [])]
    store.update(task.id, done=len(chains), failures=failures, result=result)
```

and `RUNNERS["analyze_chains"] = run_analyze_chains`. `api/task_routes.py::_title` gains:

```python
    if type == "analyze_chains":
        return f"Analyze chains {params.get('path', '')}"
```

- [ ] **Step 4: Run tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`, the task-runner file, then `.venv/bin/python -m pytest`
Expected: all pass, no regressions

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/cli.py src/opendomainmcp/tasks/runners.py src/opendomainmcp/api/task_routes.py tests/test_cli.py tests/
git commit -m "feat: codegraph --analyze CLI and analyze_chains task runner"
```

---

## Self-review notes

- **Spec coverage (stages 5–6):** bottom-up analysis with near-full-source/far-summary context ✔ (T2 ordering + T3 prompts + T5 context assembly, token-bounded); per-function summaries backfill chunk enrichment replacing isolated code extraction ✔ (T5 backfill + T6 gating); chain-level KnowledgeUnit as retrievable `kind='chain'` with entry + member chunk ids ✔ (T4 + T5); coverage fallback with Fail Loud stats ✔ (T5); documents untouched ✔ (T6 filters only code). 4A follow-ups: stale `cg:` cleanup ✔ (T5 `delete_codegraph`), real chunk ids ✔ (T5 persist extension), loader wiring ✔ (T1). Deferred (recorded): `.pks` spec/body collision, Java text_block scanning, truncation-site identity in chain edges, SPA button for the task.
- **Placeholder scan:** two intentional adapt-to-local-fixture notes (T7 runner test, T4 SearchResult shape) name exactly what to read and pin the assertions; everything else is complete code.
- **Type consistency:** `FunctionSummary` produced by T3, consumed by T5; `bottom_up_levels` (T2) → T5; `ChainItem` (T4) → T5; `persist_codegraph(graph, store, chunk_ids_by_function=None)` extended in T5 and backward-compatible with 4A callers (CLI static path passes nothing); `delete_codegraph()` uniform across three stores.
- **Known risks:** unified.py fusion extension must keep one `rrf_fuse` call (T4 instructs); `_parse` refactor in T3 is guarded by existing extract tests.
