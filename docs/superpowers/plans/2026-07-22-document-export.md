# Document Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export the corpus's Articles, RuleItems, and workflows as formatted
Markdown documents (domain-organized tree + merged handbook), with optional LLM
outline and Chinese translation passes, from both the CLI and the web UI.

**Architecture:** New `src/opendomainmcp/export/` package with four independent
stages — `collect` (store/graph → `ExportBundle`, zero LLM) → `organize` (LLM
outline, cached) → `translate` (LLM zh translation, cached) → `render` (pure
templates → files). `export_documents()` in `export/__init__.py` wires them; the
CLI subcommand and a `run_export` task runner are thin adapters over it.

**Tech Stack:** Python stdlib only (dataclasses, json, hashlib, shutil, pathlib);
LLM calls reuse the existing `synthesis.llm._caller` provider wiring; FastAPI
`FileResponse` for the zip download; React (existing `TaskCenter.tsx`) for the
web button.

**Spec:** `docs/superpowers/specs/2026-07-22-document-export-design.md`

**One deliberate deviation from the spec:** task creation reuses the existing
generic `POST /api/tasks` endpoint with `type: "export"` (how every other
background job is created) instead of a bespoke `POST /api/export`. Only the
download endpoint `GET /api/export/{task_id}/download` is new. Same capability,
less surface.

## Global Constraints

- Python ≥ 3.11, venv at `.venv`; run tests with `.venv/bin/python -m pytest` (offline — no network, no model downloads).
- No new pip dependencies.
- All LLM transports are injectable callables; tests always inject fakes.
- Fail Loud: every skip/failure lands in `ExportReport`; nothing silently dropped.
- Match existing style: snake_case, plain dataclasses without logic-heavy methods, `from __future__ import annotations` at top of each new module.
- Commit after every task on the current branch `docs/document-export-design` (rename not needed; PR later).
- Metadata `kind` values in the store: `"article"`, `"rule"` (chunks are `"code"`/`"text"`, chains live in a sibling collection and are NOT exported).

---

### Task 1: Export models + collect stage

**Files:**
- Create: `src/opendomainmcp/export/__init__.py` (empty for now — just docstring)
- Create: `src/opendomainmcp/export/models.py`
- Create: `src/opendomainmcp/export/collect.py`
- Test: `tests/test_export_collect.py`

**Interfaces:**
- Consumes: `ChromaStore.get_items(limit, offset, where) -> list[dict]` (items
  are `{"id", "text", "metadata", "evidence"?}`); graph store
  `list_workflows(q=None, limit=50) -> [{"name": ...}]` and
  `get_workflow(name) -> {"workflow_name", "prerequisites": [str], "steps":
  [{"order", "text", "precondition", "chunk_id"}]} | None`.
- Produces (used by every later task):
  - `ExportArticle(id, title, topic, body, sources: list[str], source_chunk_ids: list[str], untranslated: bool = False)`
  - `ExportRule(id, statement, trust, corroborations: int, layers: list[str], sources: list[str], evidence: list[dict], review_status: str, untranslated: bool = False)`
  - `ExportWorkflow(name, display_name, prerequisites: list[str], steps: list[dict], untranslated: bool = False)` — `name` is the stable key (slugs/outline refs); `display_name` is what headings show (translation changes only `display_name`/step texts).
  - `ExportBundle(articles: list[ExportArticle], rules: list[ExportRule], workflows: list[ExportWorkflow], stats: dict, graph_enabled: bool)`
  - `ExportReport(counts: dict, translate_errors: list[dict], outline_warnings: list[str], unassigned: dict, skipped: list[str], errors: list[dict], out_dir: str = "", zip_path: str = "")` with `to_dict()`
  - `collect_bundle(store, graph, graph_enabled: bool) -> ExportBundle`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_export_collect.py
from opendomainmcp.export.collect import collect_bundle


class FakeStore:
    """Pages like ChromaStore.get_items: filters on where["kind"]."""

    def __init__(self, items):
        self._items = items

    def get_items(self, limit=50, offset=0, where=None):
        kind = (where or {}).get("kind")
        rows = [i for i in self._items if i["metadata"].get("kind") == kind]
        return rows[offset:offset + limit]

    def stats(self):
        return {"count": len(self._items), "collection": "test"}


class FakeGraph:
    def __init__(self, workflows):
        self._wf = workflows

    def list_workflows(self, q=None, limit=50):
        return [{"name": n} for n in self._wf]

    def get_workflow(self, name):
        wf = self._wf.get(name)
        if wf is None:
            return None
        return {"workflow_name": name, "prerequisites": wf["prereqs"],
                "steps": wf["steps"]}


def _article_item(i):
    return {"id": f"a{i}", "text": f"body {i}", "metadata": {
        "kind": "article", "title": f"Title {i}", "topic": f"topic-{i}",
        "sources": "a.py | b.py", "source_chunk_ids": "c1, c2"}}


def _rule_item(i, trust="normal"):
    return {"id": f"r{i}", "text": "ignored", "metadata": {
        "kind": "rule", "statement": f"Rule {i}", "trust": trust,
        "corroborations": 2, "layers": "code, docs",
        "sources": "a.py:1-5 | b.vb:9-20", "review_status": "approved"},
        "evidence": [{"claim": "c", "quote": "q"}]}


def test_collect_pages_all_kinds_completely():
    # 120 articles + 90 rules forces >1 page per kind (page size 100)
    items = [_article_item(i) for i in range(120)] + [_rule_item(i) for i in range(90)]
    items.append({"id": "x", "text": "chunk", "metadata": {"kind": "code"}})
    bundle = collect_bundle(FakeStore(items), FakeGraph({}), graph_enabled=False)
    assert len(bundle.articles) == 120
    assert len(bundle.rules) == 90
    assert bundle.stats["count"] == len(items)
    assert bundle.graph_enabled is False
    assert bundle.workflows == []


def test_collect_parses_metadata_fields():
    bundle = collect_bundle(FakeStore([_article_item(1), _rule_item(1, "conflicted")]),
                            FakeGraph({}), graph_enabled=False)
    a, r = bundle.articles[0], bundle.rules[0]
    assert a.title == "Title 1" and a.topic == "topic-1" and a.body == "body 1"
    assert a.sources == ["a.py", "b.py"]
    assert a.source_chunk_ids == ["c1", "c2"]
    assert r.statement == "Rule 1" and r.trust == "conflicted"
    assert r.corroborations == 2 and r.layers == ["code", "docs"]
    assert r.sources == ["a.py:1-5", "b.vb:9-20"]
    assert r.evidence == [{"claim": "c", "quote": "q"}]


def test_collect_reads_workflows_from_graph():
    graph = FakeGraph({"Order Fulfillment": {
        "prereqs": ["stock synced"],
        "steps": [{"order": 1, "text": "pick", "precondition": "", "chunk_id": "c1"},
                  {"order": 2, "text": "ship", "precondition": "picked", "chunk_id": "c2"}]}})
    bundle = collect_bundle(FakeStore([]), graph, graph_enabled=True)
    assert len(bundle.workflows) == 1
    wf = bundle.workflows[0]
    assert wf.name == "Order Fulfillment" and wf.display_name == "Order Fulfillment"
    assert wf.prerequisites == ["stock synced"]
    assert [s["text"] for s in wf.steps] == ["pick", "ship"]


def test_collect_skips_graph_when_disabled():
    graph = FakeGraph({"W": {"prereqs": [], "steps": []}})
    bundle = collect_bundle(FakeStore([]), graph, graph_enabled=False)
    assert bundle.workflows == [] and bundle.graph_enabled is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_export_collect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'opendomainmcp.export'`

- [ ] **Step 3: Implement models and collect**

```python
# src/opendomainmcp/export/__init__.py
"""Document export: corpus → formatted Markdown documents."""
```

```python
# src/opendomainmcp/export/models.py
"""Plain dataclasses moved between export stages. No logic beyond parsing help."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ExportArticle:
    id: str
    title: str
    topic: str
    body: str
    sources: list[str] = field(default_factory=list)
    source_chunk_ids: list[str] = field(default_factory=list)
    untranslated: bool = False


@dataclass
class ExportRule:
    id: str
    statement: str
    trust: str = "normal"
    corroborations: int = 1
    layers: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    review_status: str = "approved"
    untranslated: bool = False


@dataclass
class ExportWorkflow:
    # ``name`` is the stable key used for slugs and outline references;
    # translation only ever touches ``display_name``/steps/prerequisites.
    name: str
    display_name: str
    prerequisites: list[str] = field(default_factory=list)
    # steps: [{"order": int, "text": str, "precondition": str, "chunk_id": str}]
    steps: list[dict] = field(default_factory=list)
    untranslated: bool = False


@dataclass
class ExportBundle:
    articles: list[ExportArticle] = field(default_factory=list)
    rules: list[ExportRule] = field(default_factory=list)
    workflows: list[ExportWorkflow] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    graph_enabled: bool = False


@dataclass
class OutlineFlow:
    workflow: str                    # ExportWorkflow.name
    articles: list[str] = field(default_factory=list)   # ExportArticle.topic
    rules: list[str] = field(default_factory=list)      # ExportRule.id


@dataclass
class OutlineDomain:
    name: str
    flows: list[OutlineFlow] = field(default_factory=list)
    articles: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)


@dataclass
class Outline:
    domains: list[OutlineDomain] = field(default_factory=list)
    # Computed leftovers (never trusted from the LLM):
    unassigned_articles: list[str] = field(default_factory=list)
    unassigned_workflows: list[str] = field(default_factory=list)
    unassigned_rules: list[str] = field(default_factory=list)


@dataclass
class ExportReport:
    counts: dict = field(default_factory=dict)
    translate_errors: list[dict] = field(default_factory=list)
    outline_warnings: list[str] = field(default_factory=list)
    unassigned: dict = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    out_dir: str = ""
    zip_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def split_pipe(value) -> list[str]:
    return [p.strip() for p in str(value or "").split("|") if p.strip()]


def split_comma(value) -> list[str]:
    return [p.strip() for p in str(value or "").split(",") if p.strip()]
```

```python
# src/opendomainmcp/export/collect.py
"""Read-only stage: store/graph → ExportBundle. Zero LLM, zero writes."""
from __future__ import annotations

from .models import (ExportArticle, ExportBundle, ExportRule, ExportWorkflow,
                     split_comma, split_pipe)

_PAGE = 100


def _page_kind(store, kind: str) -> list[dict]:
    items, offset = [], 0
    while True:
        page = store.get_items(limit=_PAGE, offset=offset, where={"kind": kind})
        items.extend(page)
        if len(page) < _PAGE:
            return items
        offset += _PAGE


def collect_bundle(store, graph, graph_enabled: bool) -> ExportBundle:
    articles = []
    for it in _page_kind(store, "article"):
        m = it["metadata"]
        articles.append(ExportArticle(
            id=it["id"], title=str(m.get("title", "")),
            topic=str(m.get("topic", "")), body=it["text"],
            sources=split_pipe(m.get("sources")),
            source_chunk_ids=split_comma(m.get("source_chunk_ids"))))

    rules = []
    for it in _page_kind(store, "rule"):
        m = it["metadata"]
        rules.append(ExportRule(
            id=it["id"], statement=str(m.get("statement", "")),
            trust=str(m.get("trust", "normal")),
            corroborations=int(m.get("corroborations", 1) or 1),
            layers=split_comma(m.get("layers")),
            sources=split_pipe(m.get("sources")),
            evidence=it.get("evidence", []),
            review_status=str(m.get("review_status", ""))))

    workflows = []
    if graph_enabled:
        for row in graph.list_workflows(limit=500):
            wf = graph.get_workflow(row["name"])
            if wf is None:
                continue
            workflows.append(ExportWorkflow(
                name=wf["workflow_name"], display_name=wf["workflow_name"],
                prerequisites=list(wf.get("prerequisites", [])),
                steps=list(wf.get("steps", []))))

    return ExportBundle(articles=articles, rules=rules, workflows=workflows,
                        stats=store.stats(), graph_enabled=graph_enabled)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_export_collect.py -v`
Expected: 4 passed

- [ ] **Step 5: Run the full suite to catch import breakage**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (no existing tests touch `export/`)

- [ ] **Step 6: Commit**

```bash
git add src/opendomainmcp/export tests/test_export_collect.py
git commit -m "feat(export): export models and collect stage"
```

---

### Task 2: Translate stage (LLM pass + content-hash cache)

**Files:**
- Create: `src/opendomainmcp/export/translate.py`
- Test: `tests/test_export_translate.py`

**Interfaces:**
- Consumes: `ExportBundle`, `ExportReport` from Task 1.
- Produces:
  - `TranslationCache(path: Path)` with `get(text) -> str | None`, `put(text, translated)`, `save()`
  - `translate_bundle(bundle, translate, cache, report) -> None` — mutates the
    bundle in place; `translate` is `Callable[[str], str]` (raises on failure).
  - `get_translator(settings) -> Callable[[str], str]` — default LLM-backed
    translator (never called in tests).

**Fields translated:** `article.title`, `article.body`; `rule.statement`;
`workflow.display_name`, each step's `text` and `precondition`, each
prerequisite. Keys (`article.topic`, `workflow.name`, ids) are never touched.
Any failure inside one object → that object keeps ALL original text,
`untranslated=True`, one entry in `report.translate_errors`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_export_translate.py
import pytest

from opendomainmcp.export.models import (ExportArticle, ExportBundle,
                                         ExportReport, ExportRule,
                                         ExportWorkflow)
from opendomainmcp.export.translate import TranslationCache, translate_bundle


def _bundle():
    return ExportBundle(
        articles=[ExportArticle(id="a1", title="Order flow", topic="orders",
                                body="Orders ship after approval.")],
        rules=[ExportRule(id="r1", statement="Credit limit must be checked.")],
        workflows=[ExportWorkflow(name="Fulfillment", display_name="Fulfillment",
                                  prerequisites=["stock synced"],
                                  steps=[{"order": 1, "text": "pick items",
                                          "precondition": "paid", "chunk_id": "c"}])])


def test_translate_all_fields_and_fills_cache(tmp_path):
    cache = TranslationCache(tmp_path / "t.json")
    calls = []

    def fake(text):
        calls.append(text)
        return f"譯{text}"

    report = ExportReport()
    b = _bundle()
    translate_bundle(b, fake, cache, report)
    assert b.articles[0].title == "譯Order flow"
    assert b.articles[0].body == "譯Orders ship after approval."
    assert b.articles[0].topic == "orders"          # key untouched
    assert b.rules[0].statement == "譯Credit limit must be checked."
    assert b.workflows[0].display_name == "譯Fulfillment"
    assert b.workflows[0].name == "Fulfillment"     # key untouched
    assert b.workflows[0].steps[0]["text"] == "譯pick items"
    assert b.workflows[0].steps[0]["precondition"] == "譯paid"
    assert b.workflows[0].prerequisites == ["譯stock synced"]
    assert report.translate_errors == []
    cache.save()
    assert (tmp_path / "t.json").exists()


def test_cache_hit_skips_llm_call(tmp_path):
    path = tmp_path / "t.json"
    calls = []

    def fake(text):
        calls.append(text)
        return f"譯{text}"

    c = TranslationCache(path)
    translate_bundle(_bundle(), fake, c, ExportReport())
    first = len(calls)
    assert first > 0
    c.save()
    translate_bundle(_bundle(), fake, TranslationCache(path), ExportReport())
    assert len(calls) == first  # warm cache from disk: zero new calls


def test_failure_keeps_original_marks_and_reports(tmp_path):
    def boom(text):
        if "Credit" in text:
            raise RuntimeError("api down")
        return f"譯{text}"

    b = _bundle()
    report = ExportReport()
    translate_bundle(b, boom, TranslationCache(tmp_path / "t.json"), report)
    r = b.rules[0]
    assert r.statement == "Credit limit must be checked."   # original kept
    assert r.untranslated is True
    assert len(report.translate_errors) == 1
    assert report.translate_errors[0]["id"] == "r1"
    # other objects still translated
    assert b.articles[0].title == "譯Order flow"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_export_translate.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` on `translate`

- [ ] **Step 3: Implement**

```python
# src/opendomainmcp/export/translate.py
"""Optional LLM pass: translate bundle content to Chinese, content-hash cached.

Per-object failure keeps the original text, sets ``untranslated`` and records
the error (Fail Loud); it never aborts the export.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable

from ..config import Settings
from .models import ExportBundle, ExportReport

_SYSTEM = (
    "You translate technical/business documentation from English to Traditional "
    "Chinese (繁體中文). Keep code identifiers, file paths, API names and [n] "
    "citations exactly as-is. Respond with ONLY the translation, no preamble."
)


class TranslationCache:
    """sha256(source text) → translated text, persisted as one JSON file."""

    def __init__(self, path: Path):
        self._path = Path(path)
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}
        self._dirty = False

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get(self, text: str):
        return self._data.get(self._key(text))

    def put(self, text: str, translated: str) -> None:
        self._data[self._key(text)] = translated
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False)
        os.replace(tmp, self._path)
        self._dirty = False


def _cached(text: str, translate: Callable[[str], str],
            cache: TranslationCache) -> str:
    if not text.strip():
        return text
    hit = cache.get(text)
    if hit is not None:
        return hit
    out = translate(text).strip()
    cache.put(text, out)
    return out


def translate_bundle(bundle: ExportBundle, translate: Callable[[str], str],
                     cache: TranslationCache, report: ExportReport,
                     progress=None) -> None:
    total = len(bundle.articles) + len(bundle.rules) + len(bundle.workflows)
    done = 0

    def _tick():
        nonlocal done
        done += 1
        if progress:
            progress({"stage": "translate", "done": done, "total": total})

    for a in bundle.articles:
        try:
            title, body = _cached(a.title, translate, cache), _cached(a.body, translate, cache)
            a.title, a.body = title, body
        except Exception as exc:  # noqa: BLE001 - one bad item must not kill the export
            a.untranslated = True
            report.translate_errors.append({"id": a.id, "kind": "article",
                                            "error": str(exc)})
        _tick()

    for r in bundle.rules:
        try:
            r.statement = _cached(r.statement, translate, cache)
        except Exception as exc:  # noqa: BLE001
            r.untranslated = True
            report.translate_errors.append({"id": r.id, "kind": "rule",
                                            "error": str(exc)})
        _tick()

    for w in bundle.workflows:
        try:
            display = _cached(w.display_name, translate, cache)
            prereqs = [_cached(p, translate, cache) for p in w.prerequisites]
            steps = []
            for s in w.steps:
                steps.append({**s,
                              "text": _cached(s.get("text", ""), translate, cache),
                              "precondition": _cached(s.get("precondition", ""),
                                                      translate, cache)})
            w.display_name, w.prerequisites, w.steps = display, prereqs, steps
        except Exception as exc:  # noqa: BLE001
            w.untranslated = True
            report.translate_errors.append({"id": w.name, "kind": "workflow",
                                            "error": str(exc)})
        _tick()


def get_translator(settings: Settings) -> Callable[[str], str]:
    """LLM-backed translator on the synthesis provider settings."""
    from ..synthesis.llm import _caller
    c = _caller(settings.resolved_synthesize_provider(),
                model=settings.resolved_synthesize_model(), system=_SYSTEM,
                max_tokens=2000, timeout=settings.request_timeout,
                max_retries=settings.max_retries,
                base_url=settings.synthesize_base_url or None)
    return c._call
```

Note: partial translation inside one object is possible before an exception
(e.g. title translated, body raises). The `try` block assigns only at the end
for articles/workflows precisely so a failure keeps the object fully original —
keep that assignment-at-the-end shape.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_export_translate.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/export/translate.py tests/test_export_translate.py
git commit -m "feat(export): translation pass with content-hash cache"
```

---

### Task 3: Organize stage (LLM outline, cached, validated)

**Files:**
- Create: `src/opendomainmcp/export/organize.py`
- Test: `tests/test_export_organize.py`

**Interfaces:**
- Consumes: `ExportBundle`, `Outline`, `OutlineDomain`, `OutlineFlow`,
  `ExportReport` from Task 1; `parse_llm_json` from
  `opendomainmcp.extract.knowledge` (existing helper that extracts a JSON
  object from raw LLM text).
- Produces:
  - `build_outline(bundle, complete, cache_path, report) -> Outline | None` —
    `complete` is `Callable[[str], str]` (user prompt → raw LLM text). Returns
    `None` when `complete` is `None` (no-LLM mode) or the LLM output has no
    usable domains (with a report warning).
  - `get_organizer(settings) -> Callable[[str], str]`

**Reference tokens:** rules are referenced as `r1`, `r2`, … (index order) in
the prompt and the LLM's response; articles by `topic`; workflows by `name`.
Validation maps tokens back, drops unknowns with a warning, and computes
unassigned as the leftover set — the LLM is never trusted for "unassigned".

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_export_organize.py
import json

from opendomainmcp.export.models import (ExportArticle, ExportBundle,
                                         ExportReport, ExportRule,
                                         ExportWorkflow)
from opendomainmcp.export.organize import build_outline


def _bundle():
    return ExportBundle(
        articles=[ExportArticle(id="a1", title="T1", topic="orders", body="b"),
                  ExportArticle(id="a2", title="T2", topic="billing", body="b")],
        rules=[ExportRule(id="R-LONG-1", statement="s1"),
               ExportRule(id="R-LONG-2", statement="s2")],
        workflows=[ExportWorkflow(name="Fulfillment", display_name="Fulfillment")])


def _llm_response():
    return json.dumps({"domains": [{
        "name": "訂單管理",
        "flows": [{"workflow": "Fulfillment", "articles": ["orders"],
                   "rules": ["r1"]}],
        "articles": [], "rules": ["r2", "r99"]}]})  # r99 is unknown


def test_outline_maps_tokens_and_flags_unknown(tmp_path):
    report = ExportReport()
    outline = build_outline(_bundle(), lambda prompt: _llm_response(),
                            tmp_path / "o.json", report)
    d = outline.domains[0]
    assert d.name == "訂單管理"
    assert d.flows[0].workflow == "Fulfillment"
    assert d.flows[0].articles == ["orders"]
    assert d.flows[0].rules == ["R-LONG-1"]        # r1 → real id
    assert d.rules == ["R-LONG-2"]                 # r2 → real id, r99 dropped
    assert any("r99" in w for w in report.outline_warnings)


def test_outline_computes_unassigned_leftovers(tmp_path):
    outline = build_outline(_bundle(), lambda p: _llm_response(),
                            tmp_path / "o.json", ExportReport())
    assert outline.unassigned_articles == ["billing"]
    assert outline.unassigned_workflows == []
    assert outline.unassigned_rules == []


def test_outline_cache_hit_skips_call(tmp_path):
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return _llm_response()

    path = tmp_path / "o.json"
    build_outline(_bundle(), llm, path, ExportReport())
    build_outline(_bundle(), llm, path, ExportReport())
    assert len(calls) == 1  # second run served from cache


def test_no_llm_returns_none(tmp_path):
    report = ExportReport()
    assert build_outline(_bundle(), None, tmp_path / "o.json", report) is None


def test_garbage_output_returns_none_with_warning(tmp_path):
    report = ExportReport()
    outline = build_outline(_bundle(), lambda p: "not json at all",
                            tmp_path / "o.json", report)
    assert outline is None
    assert report.outline_warnings
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_export_organize.py -v`
Expected: FAIL — import error on `organize`

- [ ] **Step 3: Implement**

```python
# src/opendomainmcp/export/organize.py
"""Optional LLM pass: organize items into a business-domain outline.

One call per export over titles/one-liners only (never full bodies). The JSON
response is validated against the bundle; unknown references are dropped with
a warning and leftovers become the unassigned set (computed, never trusted
from the LLM). Cached by sha256 of the input listing.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable, Optional

from ..config import Settings
from ..extract.knowledge import parse_llm_json
from .models import (ExportBundle, ExportReport, Outline, OutlineDomain,
                     OutlineFlow)

_SYSTEM = (
    "You organize a legacy system's knowledge items into a business-oriented "
    "outline: functional domains (use Traditional Chinese domain names), each "
    "containing its main workflows, with related articles and rules attached "
    "to the workflow they belong to (or to the domain directly). Use ONLY the "
    "identifiers given. Respond with ONLY a JSON object:\n"
    '{"domains": [{"name": str, '
    '"flows": [{"workflow": str, "articles": [topic, ...], "rules": [rN, ...]}], '
    '"articles": [topic, ...], "rules": [rN, ...]}]}\n'
    "Every item should appear at most once. No prose outside the JSON."
)


def _listing(bundle: ExportBundle) -> str:
    lines = ["WORKFLOWS (refer by name):"]
    lines += [f"- {w.name}" for w in bundle.workflows] or ["(none)"]
    lines.append("ARTICLES (refer by topic):")
    lines += [f"- {a.topic}: {a.title}" for a in bundle.articles] or ["(none)"]
    lines.append("RULES (refer by rN token):")
    lines += [f"- r{i + 1}: {r.statement[:160]}"
              for i, r in enumerate(bundle.rules)] or ["(none)"]
    return "\n".join(lines)


def _load_cache(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _validated(raw: dict, bundle: ExportBundle,
               report: ExportReport) -> Optional[Outline]:
    topics = {a.topic for a in bundle.articles}
    wf_names = {w.name for w in bundle.workflows}
    rule_ids = {f"r{i + 1}": r.id for i, r in enumerate(bundle.rules)}

    def _warn(token, where):
        report.outline_warnings.append(
            f"outline referenced unknown {where} {token!r}; dropped")

    def _rules(tokens):
        out = []
        for t in tokens or []:
            if t in rule_ids:
                out.append(rule_ids[t])
            else:
                _warn(t, "rule")
        return out

    def _topics(tokens):
        out = []
        for t in tokens or []:
            if t in topics:
                out.append(t)
            else:
                _warn(t, "article")
        return out

    domains = []
    for d in raw.get("domains", []) or []:
        name = str(d.get("name", "")).strip()
        if not name:
            continue
        flows = []
        for f in d.get("flows", []) or []:
            wf = str(f.get("workflow", "")).strip()
            if wf not in wf_names:
                if wf:
                    _warn(wf, "workflow")
                continue
            flows.append(OutlineFlow(workflow=wf,
                                     articles=_topics(f.get("articles")),
                                     rules=_rules(f.get("rules"))))
        domains.append(OutlineDomain(name=name, flows=flows,
                                     articles=_topics(d.get("articles")),
                                     rules=_rules(d.get("rules"))))
    if not domains:
        report.outline_warnings.append("outline had no usable domains; "
                                       "falling back to flat layout")
        return None

    placed_topics = {t for d in domains
                     for t in d.articles + [t2 for f in d.flows for t2 in f.articles]}
    placed_wfs = {f.workflow for d in domains for f in d.flows}
    placed_rules = {r for d in domains
                    for r in d.rules + [r2 for f in d.flows for r2 in f.rules]}
    return Outline(
        domains=domains,
        unassigned_articles=[a.topic for a in bundle.articles
                             if a.topic not in placed_topics],
        unassigned_workflows=[w.name for w in bundle.workflows
                              if w.name not in placed_wfs],
        unassigned_rules=[r.id for r in bundle.rules if r.id not in placed_rules])


def build_outline(bundle: ExportBundle, complete: Optional[Callable[[str], str]],
                  cache_path, report: ExportReport) -> Optional[Outline]:
    if complete is None:
        return None
    listing = _listing(bundle)
    key = hashlib.sha256(listing.encode("utf-8")).hexdigest()
    cache = _load_cache(cache_path)
    raw = cache.get(key)
    if raw is None:
        raw = parse_llm_json(complete(listing))
        if raw:
            _save_cache(cache_path, {key: raw})
    if not isinstance(raw, dict) or not raw:
        report.outline_warnings.append("outline LLM returned no parseable JSON; "
                                       "falling back to flat layout")
        return None
    return _validated(raw, bundle, report)


def get_organizer(settings: Settings) -> Callable[[str], str]:
    from ..synthesis.llm import _caller
    c = _caller(settings.resolved_synthesize_provider(),
                model=settings.resolved_synthesize_model(), system=_SYSTEM,
                max_tokens=4000, timeout=settings.request_timeout,
                max_retries=settings.max_retries,
                base_url=settings.synthesize_base_url or None)
    return c._call
```

**Check before coding:** confirm `parse_llm_json`'s exact name/signature in
`src/opendomainmcp/extract/knowledge.py` (it is imported there by
`codegraph/analyze_llm.py`). If it raises on garbage instead of returning `{}`,
wrap the call in `try/except` and treat as empty. Also confirm `_caller`'s
positional signature in `src/opendomainmcp/synthesis/llm.py`
(`_caller(backend, **kw)`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_export_organize.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/export/organize.py tests/test_export_organize.py
git commit -m "feat(export): LLM outline pass with validation and cache"
```

---

### Task 4: Render stage (domain tree, flat fallback, handbook)

**Files:**
- Create: `src/opendomainmcp/export/render.py`
- Test: `tests/test_export_render.py`

**Interfaces:**
- Consumes: `ExportBundle`, `Outline | None`, `ExportReport` from Tasks 1/3.
- Produces: `render_export(bundle, outline, out_dir: Path, report) -> None` —
  writes the file tree, fills `report.counts` / `report.unassigned` /
  `report.skipped`, sets `report.out_dir`. Also `slugify(name, used: set) -> str`.

**Layout with outline:** `index.md`, `domains/<域slug>/README.md`,
`domains/<域slug>/<flow-slug>.md`, `misc/README.md` (only when unassigned
exists), `rules-conflicted.md` (only when conflicted rules exist),
`handbook.md`. **Flat fallback (outline None):** `index.md`,
`articles/<topic-slug>.md`, `workflows/<name-slug>.md`, `rules.md`,
`rules-conflicted.md`, `handbook.md`.

**Per-document template rules (uniform):** body first (business language),
then `## 相關規則` table where applicable (trust badge 🟢 high / 🟡 normal +
corroborations), then `## 技術對照` appendix (sources file:line, verbatim
evidence quotes, chunk ids). Objects with `untranslated=True` get a
`〔未翻譯〕` marker after their heading. Conflicted rules appear ONLY in
`rules-conflicted.md`, listed with their sources side by side.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_export_render.py
from pathlib import Path

from opendomainmcp.export.models import (ExportArticle, ExportBundle,
                                         ExportReport, ExportRule,
                                         ExportWorkflow, Outline,
                                         OutlineDomain, OutlineFlow)
from opendomainmcp.export.render import render_export, slugify


def _bundle():
    return ExportBundle(
        articles=[ExportArticle(id="a1", title="訂單審批", topic="orders",
                                body="內文", sources=["a.py"],
                                source_chunk_ids=["c1"])],
        rules=[
            ExportRule(id="rh", statement="高信心規則", trust="high",
                       corroborations=3, sources=["a.py:1-5"],
                       evidence=[{"claim": "cl", "quote": "qt"}]),
            ExportRule(id="rn", statement="一般規則", trust="normal"),
            ExportRule(id="rc", statement="衝突規則", trust="conflicted",
                       sources=["a.py:1-5", "b.vb:2-9"]),
        ],
        workflows=[ExportWorkflow(
            name="Fulfillment", display_name="出貨流程",
            prerequisites=["庫存同步"],
            steps=[{"order": 1, "text": "揀貨", "precondition": "",
                    "chunk_id": "c1"}])],
        stats={"count": 10, "collection": "test"}, graph_enabled=True)


def _outline():
    return Outline(domains=[OutlineDomain(
        name="訂單管理",
        flows=[OutlineFlow(workflow="Fulfillment", articles=["orders"],
                           rules=["rh"])],
        rules=["rn"])],
        unassigned_articles=[], unassigned_workflows=[], unassigned_rules=[])


def test_domain_tree_layout(tmp_path):
    report = ExportReport()
    render_export(_bundle(), _outline(), tmp_path, report)
    domain_dir = tmp_path / "domains" / slugify("訂單管理", set())
    flow = (domain_dir / "fulfillment.md").read_text(encoding="utf-8")
    assert "出貨流程" in flow                 # display name in heading
    assert "揀貨" in flow                     # step table
    assert "訂單審批" in flow                 # article attached to flow
    assert "高信心規則" in flow and "🟢" in flow
    assert "技術對照" in flow and "a.py:1-5" in flow and "qt" in flow
    readme = (domain_dir / "README.md").read_text(encoding="utf-8")
    assert "一般規則" in readme and "🟡" in readme
    assert (tmp_path / "index.md").exists()
    assert report.out_dir == str(tmp_path)


def test_conflicted_only_in_dedicated_chapter(tmp_path):
    render_export(_bundle(), _outline(), tmp_path, ExportReport())
    conflicted = (tmp_path / "rules-conflicted.md").read_text(encoding="utf-8")
    assert "衝突規則" in conflicted and "b.vb:2-9" in conflicted
    for md in tmp_path.rglob("*.md"):
        if md.name in ("rules-conflicted.md", "handbook.md"):
            continue
        assert "衝突規則" not in md.read_text(encoding="utf-8")


def test_handbook_contains_every_section(tmp_path):
    render_export(_bundle(), _outline(), tmp_path, ExportReport())
    hb = (tmp_path / "handbook.md").read_text(encoding="utf-8")
    for needle in ("訂單管理", "出貨流程", "訂單審批", "高信心規則", "衝突規則"):
        assert needle in hb


def test_flat_fallback_without_outline(tmp_path):
    report = ExportReport()
    render_export(_bundle(), None, tmp_path, report)
    assert (tmp_path / "articles" / "orders.md").exists()
    assert (tmp_path / "workflows" / "fulfillment.md").exists()
    rules = (tmp_path / "rules.md").read_text(encoding="utf-8")
    assert "高信心規則" in rules and "一般規則" in rules
    assert "衝突規則" not in rules
    assert (tmp_path / "rules-conflicted.md").exists()


def test_unassigned_render_to_misc_and_report(tmp_path):
    outline = _outline()
    outline.unassigned_articles = ["orders"]
    outline.domains[0].flows[0].articles = []
    report = ExportReport()
    render_export(_bundle(), outline, tmp_path, report)
    misc = (tmp_path / "misc" / "README.md").read_text(encoding="utf-8")
    assert "訂單審批" in misc
    assert report.unassigned == {"articles": 1, "workflows": 0, "rules": 0}


def test_untranslated_marker(tmp_path):
    b = _bundle()
    b.articles[0].untranslated = True
    render_export(b, _outline(), tmp_path, ExportReport())
    text = "".join(p.read_text(encoding="utf-8")
                   for p in tmp_path.rglob("*.md"))
    assert "〔未翻譯〕" in text


def test_slugify_collisions():
    used = set()
    assert slugify("Order Flow", used) == "order-flow"
    assert slugify("Order Flow", used) == "order-flow-2"
    assert slugify("訂單管理", used)  # non-ascii yields a non-empty slug


def test_graph_disabled_notes_in_index(tmp_path):
    b = _bundle()
    b.workflows, b.graph_enabled = [], False
    report = ExportReport()
    render_export(b, None, tmp_path, report)
    assert "圖庫未啟用" in (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "workflows (graph store not enabled)" in " ".join(report.skipped)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_export_render.py -v`
Expected: FAIL — import error on `render`

- [ ] **Step 3: Implement**

Full implementation — the templates are the deliverable, so write them exactly:

```python
# src/opendomainmcp/export/render.py
"""Pure-template stage: (bundle, outline) → Markdown tree + merged handbook.

No LLM, no store access. Business-language body first; every document ends
with a 技術對照 appendix for engineers. Conflicted rules render ONLY into
rules-conflicted.md.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Optional

from .models import (ExportArticle, ExportBundle, ExportReport, ExportRule,
                     ExportWorkflow, Outline)

_BADGE = {"high": "🟢 high", "normal": "🟡 normal"}


def slugify(name: str, used: set) -> str:
    norm = unicodedata.normalize("NFKC", str(name)).strip().lower()
    slug = re.sub(r"[^\w一-鿿-]+", "-", norm).strip("-") or "item"
    base, n = slug, 1
    while slug in used:
        n += 1
        slug = f"{base}-{n}"
    used.add(slug)
    return slug


def _mark(obj) -> str:
    return " 〔未翻譯〕" if getattr(obj, "untranslated", False) else ""


def _rule_row(r: ExportRule) -> str:
    badge = _BADGE.get(r.trust, r.trust)
    src = ", ".join(r.sources) or "—"
    return f"| {r.statement}{_mark(r)} | {badge} | {r.corroborations} | {src} |"


def _rules_table(rules: list[ExportRule]) -> list[str]:
    if not rules:
        return []
    lines = ["| 規則 | 信心 | 佐證數 | 出處 |", "| --- | --- | --- | --- |"]
    order = {"high": 0, "normal": 1}
    for r in sorted(rules, key=lambda r: order.get(r.trust, 2)):
        lines.append(_rule_row(r))
    return lines


def _tech_appendix(sources: list[str], chunk_ids: list[str],
                   evidence: list[dict]) -> list[str]:
    lines = ["", "## 技術對照", ""]
    if sources:
        lines.append("**來源位置：** " + ", ".join(f"`{s}`" for s in sources))
    if evidence:
        lines.append("")
        lines.append("**佐證引文：**")
        for ev in evidence:
            claim = str(ev.get("claim", "")).strip()
            quote = str(ev.get("quote", "")).strip()
            lines.append(f"- {claim}：`{quote}`" if claim else f"- `{quote}`")
    if chunk_ids:
        lines.append("")
        lines.append("**關聯 chunk：** " + ", ".join(f"`{c}`" for c in chunk_ids))
    if len(lines) == 3:  # nothing to show
        return []
    return lines


def _render_article(a: ExportArticle, level: int = 1) -> str:
    h = "#" * level
    lines = [f"{h} {a.title}{_mark(a)}", "", a.body]
    lines += _tech_appendix(a.sources, a.source_chunk_ids, [])
    return "\n".join(lines) + "\n"


def _render_workflow(w: ExportWorkflow, articles: list[ExportArticle],
                     rules: list[ExportRule]) -> str:
    lines = [f"# {w.display_name}{_mark(w)}", ""]
    if w.prerequisites:
        lines.append("**前置條件：** " + "、".join(w.prerequisites))
        lines.append("")
    if w.steps:
        lines += ["| 步驟 | 內容 | 前置 |", "| --- | --- | --- |"]
        for s in w.steps:
            lines.append(f"| {s.get('order', '')} | {s.get('text', '')} "
                         f"| {s.get('precondition', '') or '—'} |")
        lines.append("")
    for a in articles:
        lines.append(_render_article(a, level=2))
    if rules:
        lines += ["## 相關規則", ""] + _rules_table(rules) + [""]
    chunk_ids = [s.get("chunk_id", "") for s in w.steps if s.get("chunk_id")]
    src = [e for r in rules for e in r.sources]
    ev = [e for r in rules for e in r.evidence]
    lines += _tech_appendix(src, chunk_ids, ev)
    return "\n".join(lines) + "\n"


def _render_rules_page(title: str, rules: list[ExportRule]) -> str:
    lines = [f"# {title}", ""] + _rules_table(rules)
    return "\n".join(lines) + "\n"


def _render_conflicted(rules: list[ExportRule]) -> str:
    lines = ["# 待釐清規則（conflicted）", "",
             "以下規則在不同來源間存在衝突，需人工裁決後再採信。", ""]
    for r in rules:
        lines.append(f"## {r.statement}{_mark(r)}")
        lines.append("")
        lines.append("衝突來源：")
        for s in r.sources or ["(來源不明)"]:
            lines.append(f"- `{s}`")
        lines += _tech_appendix([], [], r.evidence)
        lines.append("")
    return "\n".join(lines) + "\n"


def _write(path: Path, text: str, handbook: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    handbook.append(text)


def render_export(bundle: ExportBundle, outline: Optional[Outline],
                  out_dir: Path, report: ExportReport) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    handbook: list[str] = []
    articles = {a.topic: a for a in bundle.articles}
    rules = {r.id: r for r in bundle.rules}
    workflows = {w.name: w for w in bundle.workflows}
    active = [r for r in bundle.rules if r.trust != "conflicted"]
    conflicted = [r for r in bundle.rules if r.trust == "conflicted"]

    if outline is not None:
        dom_slugs: set = set()
        for d in outline.domains:
            ddir = out / "domains" / slugify(d.name, dom_slugs)
            flow_slugs: set = set()
            flow_lines = []
            for f in d.flows:
                w = workflows[f.workflow]
                fslug = slugify(f.workflow, flow_slugs)
                fa = [articles[t] for t in f.articles if t in articles]
                fr = [rules[i] for i in f.rules
                      if i in rules and rules[i].trust != "conflicted"]
                _write(ddir / f"{fslug}.md", _render_workflow(w, fa, fr), handbook)
                flow_lines.append(f"- [{w.display_name}]({fslug}.md)")
            dr = [rules[i] for i in d.rules
                  if i in rules and rules[i].trust != "conflicted"]
            da = [articles[t] for t in d.articles if t in articles]
            readme = [f"# {d.name}", "", "## 主流程", ""] + \
                (flow_lines or ["（無）"]) + [""]
            for a in da:
                readme.append(_render_article(a, level=2))
            if dr:
                readme += ["## 領域規則", ""] + _rules_table(dr)
            _write(ddir / "README.md", "\n".join(readme) + "\n", handbook)

        un_a = [articles[t] for t in outline.unassigned_articles if t in articles]
        un_w = [workflows[n] for n in outline.unassigned_workflows
                if n in workflows]
        un_r = [rules[i] for i in outline.unassigned_rules
                if i in rules and rules[i].trust != "conflicted"]
        report.unassigned = {"articles": len(un_a), "workflows": len(un_w),
                             "rules": len(un_r)}
        if un_a or un_w or un_r:
            misc = ["# 未分類", "", "大綱未能歸類的項目，內容仍完整保留。", ""]
            for a in un_a:
                misc.append(_render_article(a, level=2))
            for w in un_w:
                misc.append(_render_workflow(w, [], []))
            if un_r:
                misc += ["## 未分類規則", ""] + _rules_table(un_r)
            _write(out / "misc" / "README.md", "\n".join(misc) + "\n", handbook)
    else:
        a_slugs: set = set()
        for a in bundle.articles:
            _write(out / "articles" / f"{slugify(a.topic, a_slugs)}.md",
                   _render_article(a), handbook)
        w_slugs: set = set()
        for w in bundle.workflows:
            _write(out / "workflows" / f"{slugify(w.name, w_slugs)}.md",
                   _render_workflow(w, [], []), handbook)
        if active:
            _write(out / "rules.md", _render_rules_page("業務規則", active),
                   handbook)
        report.unassigned = {}

    if conflicted:
        _write(out / "rules-conflicted.md", _render_conflicted(conflicted),
               handbook)

    if not bundle.graph_enabled:
        report.skipped.append("workflows (graph store not enabled)")

    index = ["# 知識庫匯出總覽", "",
             f"- 索引物件總數：{bundle.stats.get('count', 0)}",
             f"- 文章：{len(bundle.articles)}　規則：{len(bundle.rules)}"
             f"（含待釐清 {len(conflicted)}）　流程：{len(bundle.workflows)}",
             "- 信心圖例：🟢 high（多來源佐證）　🟡 normal　🔴 conflicted（見待釐清專章）",
             ""]
    if not bundle.graph_enabled:
        index.append("> 注意：圖庫未啟用，本次匯出不含流程章節。")
        index.append("")
    if outline is not None:
        index.append("## 領域目錄")
        index.append("")
        seen: set = set()
        for d in outline.domains:
            index.append(f"- [{d.name}](domains/{slugify(d.name, seen)}/README.md)")
    (out / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")

    hb = "\n\n---\n\n".join(["# Handbook", ((out / 'index.md')
                             .read_text(encoding='utf-8'))] + handbook)
    (out / "handbook.md").write_text(hb, encoding="utf-8")

    report.counts = {"articles": len(bundle.articles), "rules": len(active),
                     "conflicted_rules": len(conflicted),
                     "workflows": len(bundle.workflows),
                     "domains": len(outline.domains) if outline else 0}
    report.out_dir = str(out)
```

Note on `test_domain_tree_layout`: the domain slug in the test is computed with
a fresh `set()`, and the renderer also starts from a fresh set — they agree
because slugify is deterministic for the first occurrence.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_export_render.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/export/render.py tests/test_export_render.py
git commit -m "feat(export): render stage — domain tree, flat fallback, handbook"
```

---

### Task 5: `export_documents()` entry point + CLI subcommand

**Files:**
- Modify: `src/opendomainmcp/export/__init__.py`
- Modify: `src/opendomainmcp/cli.py` (add `_cmd_export` near `_cmd_synthesize`
  ~line 202, and the parser entry after `p_consolidate` ~line 320)
- Test: `tests/test_export_documents.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces:
  - `ExportError(Exception)`
  - `export_documents(ctx, out_dir, *, translate=True, use_llm=True, zip_output=False, translator=None, organizer=None, progress=None) -> ExportReport`
    — `translator`/`organizer` are injectable for tests; when `None` and the
    respective pass is enabled, they're built from settings via
    `get_translator` / `get_organizer`. `use_llm=False` disables BOTH passes.
  - CLI: `opendomainmcp export --out DIR [--no-translate] [--no-llm] [--zip]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_export_documents.py
import pytest

from opendomainmcp.export import ExportError, export_documents
from tests.test_export_collect import FakeGraph, FakeStore, _article_item, _rule_item


class FakeCtx:
    def __init__(self, store, graph, data_dir):
        self.store, self.graph = store, graph
        self.settings = type("S", (), {"data_dir": str(data_dir)})()


def test_empty_corpus_fails_loud(tmp_path):
    ctx = FakeCtx(FakeStore([]), FakeGraph({}), tmp_path)
    with pytest.raises(ExportError, match="synthesize"):
        export_documents(ctx, tmp_path / "out", use_llm=False)


def test_end_to_end_no_llm(tmp_path):
    ctx = FakeCtx(FakeStore([_article_item(1), _rule_item(1)]),
                  FakeGraph({}), tmp_path)
    report = export_documents(ctx, tmp_path / "out", use_llm=False)
    assert (tmp_path / "out" / "index.md").exists()
    assert (tmp_path / "out" / "handbook.md").exists()
    assert report.counts["articles"] == 1


def test_end_to_end_with_fake_llms_and_zip(tmp_path):
    ctx = FakeCtx(FakeStore([_article_item(1), _rule_item(1)]),
                  FakeGraph({}), tmp_path)
    report = export_documents(
        ctx, tmp_path / "out", zip_output=True,
        translator=lambda t: f"譯{t}",
        organizer=lambda p: '{"domains": [{"name": "領域", "flows": [], '
                            '"articles": ["topic-1"], "rules": ["r1"]}]}')
    assert report.zip_path.endswith(".zip")
    from pathlib import Path
    assert Path(report.zip_path).exists()
    assert (tmp_path / "out" / "domains").is_dir()
    # translation cache persisted under data_dir
    assert (tmp_path / "translation_cache.json").exists()
```

Note: `FakeStore`'s `get_items` in Task 1's test ignores non-kind `where` keys —
that shape is exactly what collect uses, so reuse via import (matches existing
test-suite practice of small local fakes; importing between test modules is
already done elsewhere in this suite — if the import feels wrong, copy the
three helpers instead; do NOT redesign them).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_export_documents.py -v`
Expected: FAIL — `ImportError: cannot import name 'export_documents'`

- [ ] **Step 3: Implement the entry point**

```python
# src/opendomainmcp/export/__init__.py  (replace file content)
"""Document export: corpus → formatted Markdown documents.

Single path used by the CLI and the web task runner:
collect → organize (optional LLM) → translate (optional LLM) → render.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .collect import collect_bundle
from .models import ExportReport
from .organize import build_outline, get_organizer
from .render import render_export
from .translate import TranslationCache, get_translator, translate_bundle

__all__ = ["ExportError", "export_documents"]


class ExportError(Exception):
    pass


def _graph_enabled(ctx) -> bool:
    from ..graph.store import NullGraphStore
    return not isinstance(ctx.graph, NullGraphStore)


def export_documents(ctx, out_dir, *, translate: bool = True,
                     use_llm: bool = True, zip_output: bool = False,
                     translator=None, organizer=None, progress=None) -> ExportReport:
    report = ExportReport()
    if progress:
        progress({"stage": "collect"})
    bundle = collect_bundle(ctx.store, ctx.graph, _graph_enabled(ctx))
    if not bundle.articles and not bundle.rules:
        raise ExportError(
            "Nothing to export: no articles and no rules in the collection. "
            "Run `synthesize` and/or `consolidate` first.")

    data_dir = Path(ctx.settings.data_dir)
    outline = None
    if use_llm:
        if organizer is None:
            organizer = get_organizer(ctx.settings)
        if progress:
            progress({"stage": "organize"})
        outline = build_outline(bundle, organizer,
                                data_dir / "outline_cache.json", report)
        if translate:
            if translator is None:
                translator = get_translator(ctx.settings)
            cache = TranslationCache(data_dir / "translation_cache.json")
            translate_bundle(bundle, translator, cache, report, progress=progress)
            cache.save()

    if progress:
        progress({"stage": "render"})
    render_export(bundle, outline, Path(out_dir), report)

    if zip_output:
        report.zip_path = shutil.make_archive(str(Path(out_dir)), "zip",
                                              root_dir=str(out_dir))
    return report
```

**Check before coding:** `_graph_enabled` imports `NullGraphStore` from
`opendomainmcp.graph.store` — confirm the class name/location with
`grep -n "class NullGraphStore" src/opendomainmcp/graph/store.py`. The test's
`FakeCtx.graph` is a `FakeGraph`, which is not a `NullGraphStore`, so
`graph_enabled=True` — but `FakeGraph({})` lists no workflows, which is fine.

- [ ] **Step 4: Add the CLI command**

In `src/opendomainmcp/cli.py`, after `_cmd_synthesize` (~line 218) add:

```python
def _cmd_export(ctx, args) -> int:
    from .export import ExportError, export_documents

    def progress(event):
        stage = event.get("stage", "")
        if stage == "translate":
            print(f"\r  translate {event['done']}/{event['total']}",
                  end="", flush=True)
        else:
            print(f"[{stage}]")

    try:
        report = export_documents(
            ctx, args.out, translate=not args.no_translate,
            use_llm=not args.no_llm, zip_output=args.zip, progress=progress)
    except ExportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print()
    c = report.counts
    print(f"Exported {c.get('articles', 0)} article(s), {c.get('rules', 0)} "
          f"rule(s) (+{c.get('conflicted_rules', 0)} conflicted), "
          f"{c.get('workflows', 0)} workflow(s) → {report.out_dir}")
    if report.zip_path:
        print(f"Zip: {report.zip_path}")
    for w in report.outline_warnings:
        print(f"  outline: {w}", file=sys.stderr)
    if report.translate_errors:
        print(f"Translate errors: {len(report.translate_errors)}", file=sys.stderr)
        for e in report.translate_errors:
            print(f"  {e['kind']} {e['id']}: {e['error']}", file=sys.stderr)
    for s in report.skipped:
        print(f"  skipped: {s}", file=sys.stderr)
    return 0
```

In `build_parser()`, after the `p_consolidate` block (~line 326) add:

```python
    p_export = sub.add_parser(
        "export",
        help="Export articles/rules/workflows as formatted Markdown documents")
    p_export.add_argument("--out", required=True, help="Output directory")
    p_export.add_argument("--no-translate", action="store_true",
                          help="Skip the Chinese translation pass")
    p_export.add_argument("--no-llm", action="store_true",
                          help="Skip ALL LLM passes (outline + translation); "
                               "deterministic flat layout")
    p_export.add_argument("--zip", action="store_true",
                          help="Also produce <out>.zip")
    p_export.set_defaults(func=_cmd_export)
```

Add a CLI test to `tests/test_export_documents.py`:

```python
def test_cli_export_no_llm(tmp_path, monkeypatch, capsys):
    from opendomainmcp import cli

    ctx = FakeCtx(FakeStore([_article_item(1), _rule_item(1)]),
                  FakeGraph({}), tmp_path)
    monkeypatch.setattr(cli, "build_context", lambda collection=None: ctx)
    rc = cli.main(["export", "--out", str(tmp_path / "out"), "--no-llm"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Exported 1 article(s)" in out
```

**Check before coding:** how `tests/test_cli.py` fakes `build_context`
(monkeypatch target and `main(argv)` calling convention) — mirror that exact
pattern instead of the sketch above if it differs.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_export_documents.py -v`
Expected: 4 passed

Run: `.venv/bin/python -m pytest -q`
Expected: full suite passes

- [ ] **Step 6: Commit**

```bash
git add src/opendomainmcp/export/__init__.py src/opendomainmcp/cli.py tests/test_export_documents.py
git commit -m "feat(export): export_documents entry point and export CLI subcommand"
```

---

### Task 6: Web surface — task runner, download endpoint, UI button

**Files:**
- Modify: `src/opendomainmcp/tasks/runners.py` (add `run_export`, register in `RUNNERS` ~line 165)
- Create: `src/opendomainmcp/api/export_routes.py`
- Modify: `src/opendomainmcp/api/app.py` (include the router — mirror how `insight_routes.router` / `quality_routes` are included; find with `grep -n "include_router" src/opendomainmcp/api/app.py`)
- Modify: `web/src/api.ts` (add `exportDownloadUrl`)
- Modify: `web/src/components/TaskCenter.tsx` (匯出文件 button + download link)
- Test: `tests/test_export_api.py`

**Interfaces:**
- Consumes: `export_documents` from Task 5; existing task framework
  (`RUNNERS[type](ctx, store, task, is_cancelled)`, `store.update(task.id, ...)`,
  generic `POST /api/tasks {type, params}`).
- Produces:
  - `run_export(ctx, store, task, is_cancelled)` — writes to
    `<data_dir>/exports/<task_id>/`, always zips (the web flow needs the zip),
    stores `report.to_dict()` as the task result. Params (all optional):
    `{"no_translate": bool, "no_llm": bool}`.
  - `GET /api/export/{task_id}/download` → zip `FileResponse`
    (404 until the zip exists).

- [ ] **Step 1: Write the failing API test**

The existing `tests/test_api.py` `client` fixture (reuse it — it lives in that
file, so either import-style reuse or copy the 6-line fixture into the new
file) returns `(TestClient, ctx, tmp_path)` with `Settings(data_dir=tmp_path)`
and conftest-provided `store`/`pipeline`/`fake_graph`:

```python
# tests/test_export_api.py
"""Export task runner + download endpoint."""
from pathlib import Path

from opendomainmcp.tasks.runners import RUNNERS, run_export


class FakeTask:
    id = "t1"
    params = {"no_llm": True}


class RecordingStore:
    def __init__(self):
        self.updates = []

    def update(self, task_id, throttle=False, **kw):
        self.updates.append(kw)


def test_export_registered_in_runners():
    assert RUNNERS["export"] is run_export


def test_run_export_writes_zip_and_result(tmp_path):
    from tests.test_export_documents import FakeCtx
    from tests.test_export_collect import FakeGraph, FakeStore, _article_item, _rule_item

    ctx = FakeCtx(FakeStore([_article_item(1), _rule_item(1)]),
                  FakeGraph({}), tmp_path)
    store = RecordingStore()
    run_export(ctx, store, FakeTask(), is_cancelled=lambda: False)
    zip_path = Path(tmp_path) / "exports" / "t1.zip"
    assert zip_path.exists()
    result = store.updates[-1]["result"]
    assert result["counts"]["articles"] == 1
    assert result["zip_path"] == str(zip_path)


def test_download_404_before_export(client):
    tc, _, _ = client
    assert tc.get("/api/export/nope/download").status_code == 404


def test_download_serves_zip(client):
    import zipfile

    tc, _, tmp_path = client            # Settings(data_dir=tmp_path)
    exports = tmp_path / "exports"
    exports.mkdir(parents=True)
    with zipfile.ZipFile(exports / "t9.zip", "w") as z:
        z.writestr("index.md", "# hi")
    resp = tc.get("/api/export/t9/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_export_api.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_export'`

- [ ] **Step 3: Implement runner and routes**

Append to `src/opendomainmcp/tasks/runners.py` (before `RUNNERS`):

```python
def run_export(ctx, store, task, is_cancelled) -> None:
    from pathlib import Path

    from ..export import export_documents

    out_dir = Path(ctx.settings.data_dir) / "exports" / task.id

    def progress(event):
        if event.get("stage") == "translate":
            store.update(task.id, throttle=True, done=event["done"])

    report = export_documents(
        ctx, out_dir,
        translate=not task.params.get("no_translate", False),
        use_llm=not task.params.get("no_llm", False),
        zip_output=True, progress=progress)
    store.update(task.id, result=report.to_dict())
```

and register it:

```python
RUNNERS = {
    "ingest": run_ingest,
    "synthesize": run_synthesize,
    "extract": run_extract,
    "analyze_chains": run_analyze_chains,
    "consolidate": run_consolidate,
    "export": run_export,
}
```

Create `src/opendomainmcp/api/export_routes.py`:

```python
"""Download endpoint for finished export tasks. Task creation goes through the
generic POST /api/tasks with type "export" like every other background job."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..context import Context
from .deps import get_ctx

router = APIRouter()


@router.get("/api/export/{task_id}/download")
def download_export(task_id: str, ctx: Context = Depends(get_ctx)):
    zip_path = Path(ctx.settings.data_dir) / "exports" / f"{task_id}.zip"
    if not zip_path.is_file():
        raise HTTPException(status_code=404, detail="export not found")
    return FileResponse(zip_path, media_type="application/zip",
                        filename="knowledge-export.zip")
```

Register in `src/opendomainmcp/api/app.py` next to the existing router
includes (grep `include_router`; mirror the `insight_routes` line exactly,
including any auth dependency wrapping the routers there).

- [ ] **Step 4: Run backend tests**

Run: `.venv/bin/python -m pytest tests/test_export_api.py -v`
Expected: 4 passed

Run: `.venv/bin/python -m pytest -q`
Expected: full suite passes

- [ ] **Step 5: Frontend — button + download link**

In `web/src/api.ts`, next to `createTask` (~line 702), add:

```typescript
  exportDownloadUrl: (id: string) =>
    withCollection(`/api/export/${encodeURIComponent(id)}/download`),
```

In `web/src/components/TaskCenter.tsx`:
1. Add a "匯出文件" button next to the existing task-creation controls; on
   click: `api.createTask("export", {})` and refresh the task list — copy the
   exact call/refresh pattern the component (or `Ingest.tsx:71`) already uses
   for other task types.
2. In the task-row rendering, when `task.type === "export" &&
   task.status === "done"`, render a download anchor:

```tsx
<a href={api.exportDownloadUrl(task.id)} download>下載 zip</a>
```

Follow the component's existing styling/classNames; no new components.

- [ ] **Step 6: Build the frontend**

Run: `cd web && npm run build`
Expected: build succeeds, outputs to `src/opendomainmcp/api/static/`

- [ ] **Step 7: Verify in the running app**

Use the `run-open-domain-mcp` skill: start `./run.sh web`, open the dashboard,
trigger 匯出文件 from the task center on a small ingested corpus with
`{"no_llm": true}` (or temporarily via
`curl -X POST localhost:8000/api/tasks -H 'content-type: application/json' -d '{"type":"export","params":{"no_llm":true}}'`),
wait for done, download the zip, confirm `index.md`/`handbook.md` inside.

- [ ] **Step 8: Commit**

```bash
git add src/opendomainmcp/tasks/runners.py src/opendomainmcp/api/export_routes.py src/opendomainmcp/api/app.py web/src/api.ts web/src/components/TaskCenter.tsx src/opendomainmcp/api/static tests/test_export_api.py
git commit -m "feat(export): background export task, zip download endpoint, task-center button"
```

---

## Final checks (after all tasks)

- [ ] `.venv/bin/python -m pytest -q` — entire suite green.
- [ ] `opendomainmcp export --out /tmp/odm-export --no-llm` against a real
  ingested collection: tree renders, report prints counts.
- [ ] Update `CLAUDE.md` Part 3 (add `./run.sh export …` line) and Part 4
  (one sentence for the export subsystem); commit as `docs: document export in CLAUDE.md`.
- [ ] Open a PR from `docs/document-export-design` to `main`.
