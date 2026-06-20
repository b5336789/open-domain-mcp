# Article-Augmented Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ask` and `search` retrieve from both the chunk collection and the `<base>__articles` collection, fused with RRF, gated by a default-on runtime setting.

**Architecture:** A new `retrieval/unified.py:search_unified()` helper searches both collections and fuses their ranked id lists with the existing `rrf_fuse`. `ask` (`query/rag.py`) and `search` (CLI `cli.py` + web `api/app.py`) call it; the low-level `ChromaStore.search`, MCP views, and the advisor are untouched. When the flag is off or no articles exist, behavior is byte-for-byte identical to today.

**Tech Stack:** Python ≥ 3.11, ChromaDB, pydantic-settings, pytest (offline).

## Global Constraints

- Behavior with `retrieve_include_articles=False` OR an empty `__articles` collection MUST be identical to the current `store.search(...)` path.
- Fusion lives in `retrieval/unified.py` only — do NOT modify `ChromaStore.search`, MCP views, or the advisor.
- Reuse the existing `rrf_fuse(rankings: list[list[str]], top_k: int, k: int = 60) -> list[tuple[str, float]]` from `retrieval/lexical.py` (exported via `retrieval`); do not write a new fuser.
- Articles are identified by `metadata["kind"] == "article"`; chunks by `"code"`/`"text"`. Article metadata has NO `source` key — it has `title`, `topic`, and `sources` (a `" | "`-joined `file:line` string).
- Fail Loud: formatting an article source must never raise on missing metadata (fall back to `topic` then `id`). No new network paths; `ask` tests stay offline via the injected `synthesize`/`synthesize_stream` callables.
- snake_case, plain functions, offline tests using the conftest `store` fixture + `.sibling(...)`.

Spec: `docs/superpowers/specs/2026-06-20-article-augmented-retrieval-design.md`

## File Structure

- Modify `src/opendomainmcp/config.py` — add `retrieve_include_articles: bool = True` and register it runtime-editable.
- Create `src/opendomainmcp/retrieval/unified.py` — `search_unified(...)` fusion helper.
- Modify `src/opendomainmcp/retrieval/__init__.py` — export `search_unified`.
- Modify `src/opendomainmcp/query/rag.py` — call `search_unified`; render article sources + citation `type`.
- Modify `src/opendomainmcp/cli.py` — `_cmd_search` uses `search_unified`, marks article hits.
- Modify `src/opendomainmcp/api/app.py` — `/api/search` uses `search_unified`.
- Tests: `tests/test_retrieval_unified.py`, extend `tests/test_rag.py`, extend `tests/test_cli.py`, extend `tests/test_api.py`.

---

### Task 1: `retrieve_include_articles` setting

**Files:**
- Modify: `src/opendomainmcp/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings.retrieve_include_articles: bool` (default `True`), present in the runtime-editable tuple (the one ending with `"retrieve_approved_only",`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py — add
def test_retrieve_include_articles_defaults_on_and_is_editable():
    from opendomainmcp.config import Settings
    s = Settings()
    assert s.retrieve_include_articles is True
    # runtime-editable: update_editable must accept it without "Not editable"
    updated = s.update_editable({"retrieve_include_articles": False})
    assert updated.retrieve_include_articles is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py::test_retrieve_include_articles_defaults_on_and_is_editable -v`
Expected: FAIL — `AttributeError`/`ValidationError` (field missing) or "Not editable".

- [ ] **Step 3: Write minimal implementation**

In `config.py`, add the field near the other retrieval flags (`retrieve_approved_only` is around line 101):

```python
    # Include synthesized articles (the <base>__articles collection) in ask/search
    # retrieval, fused with chunks. Off or no-articles == today's behavior.
    retrieve_include_articles: bool = True
```

And add `"retrieve_include_articles"` to the runtime-editable tuple, immediately after the `"retrieve_approved_only",` entry:

```python
    "retrieve_approved_only",
    "retrieve_include_articles",
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py::test_retrieve_include_articles_defaults_on_and_is_editable -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/config.py tests/test_config.py
git commit -m "feat(config): add runtime-editable retrieve_include_articles (default on)"
```

---

### Task 2: `search_unified` fusion helper

**Files:**
- Create: `src/opendomainmcp/retrieval/unified.py`
- Modify: `src/opendomainmcp/retrieval/__init__.py`
- Test: `tests/test_retrieval_unified.py`

**Interfaces:**
- Consumes: `ChromaStore` (`search`, `sibling`, `stats`); `Settings.retrieve_include_articles`; `rrf_fuse` from `..retrieval`.
- Produces: `search_unified(store, query, *, top_k=5, mode="vector", settings, where=None, source_contains=None) -> list[SearchResult]`. Searches the chunk store and the `f"{store.stats()['collection']}__articles"` sibling, RRF-fuses the two ranked id lists, returns up to `top_k` `SearchResult`s with their original scores, ordered by fused rank. Article results carry `metadata["kind"] == "article"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_retrieval_unified.py
from opendomainmcp.config import Settings
from opendomainmcp.models import Article, Chunk, KnowledgeUnit
from opendomainmcp.retrieval import search_unified


def _arts(store):
    return store.sibling(f"{store.stats()['collection']}__articles")


def _seed_chunks(store):
    store.upsert([
        Chunk(text="orders over 10k require manager approval", source="rules.md",
              kind="text", start_line=1, end_line=1),
        Chunk(text="def approve(order): ...", source="approve.py", kind="code",
              start_line=1, end_line=2),
    ])


def _seed_article(store):
    _arts(store).upsert([Article(
        title="Order Approval Rule", topic="order approval",
        body="Orders above $10k require manager sign-off [1].",
        source_chunk_ids=["a"], sources=["rules.md:1"])])


def test_fusion_includes_articles_and_chunks(store):
    _seed_chunks(store)
    _seed_article(store)
    results = search_unified(store, "order approval over 10k", top_k=5,
                             mode="hybrid", settings=Settings())
    kinds = {r.metadata.get("kind") for r in results}
    assert "article" in kinds            # the synthesized article competes
    assert kinds & {"code", "text"}      # chunks still present


def test_flag_off_is_identical_to_plain_search(store):
    _seed_chunks(store)
    _seed_article(store)
    s = Settings(retrieve_include_articles=False)
    unified = search_unified(store, "order approval", top_k=5, mode="vector", settings=s)
    plain = store.search("order approval", top_k=5, mode="vector")
    assert [r.id for r in unified] == [r.id for r in plain]
    assert all(r.metadata.get("kind") != "article" for r in unified)


def test_no_articles_is_identical_to_plain_search(store):
    _seed_chunks(store)  # no article seeded → empty sibling
    unified = search_unified(store, "approval", top_k=5, mode="vector", settings=Settings())
    plain = store.search("approval", top_k=5, mode="vector")
    assert [r.id for r in unified] == [r.id for r in plain]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_retrieval_unified.py -v`
Expected: FAIL — `ImportError: cannot import name 'search_unified'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/opendomainmcp/retrieval/unified.py
"""Unified retrieval: fuse chunk hits with synthesized-article hits.

Used by `ask` and `search`. The low-level store, MCP views, and the advisor are
intentionally NOT routed through here. When articles are disabled or none exist,
this returns exactly the plain chunk search.
"""
from __future__ import annotations

from ..models import SearchResult
from . import rrf_fuse


def search_unified(store, query, *, top_k=5, mode="vector", settings,
                   where=None, source_contains=None) -> list[SearchResult]:
    chunk_hits = store.search(query, top_k=top_k, where=where, mode=mode,
                              source_contains=source_contains)
    if not getattr(settings, "retrieve_include_articles", True):
        return chunk_hits

    article_store = store.sibling(f"{store.stats()['collection']}__articles")
    if article_store.stats()["count"] == 0:
        return chunk_hits

    article_hits = article_store.search(query, top_k=top_k, where=where, mode=mode,
                                        source_contains=source_contains)
    if not article_hits:
        return chunk_hits

    pool = {r.id: r for r in chunk_hits}
    pool.update({r.id: r for r in article_hits})
    fused = rrf_fuse([[h.id for h in chunk_hits], [h.id for h in article_hits]],
                     top_k=top_k)
    return [pool[_id] for _id, _ in fused if _id in pool]
```

```python
# src/opendomainmcp/retrieval/__init__.py — extend
from .lexical import LexicalIndex, rrf_fuse, tokenize
from .rerank import CrossEncoderReranker, get_reranker
from .unified import search_unified

__all__ = ["LexicalIndex", "rrf_fuse", "tokenize", "CrossEncoderReranker",
           "get_reranker", "search_unified"]
```

Note: `unified.py` imports `rrf_fuse` from `.` (the package `__init__`); since
`__init__` imports `lexical` before `unified`, `rrf_fuse` is already bound — no
circular-import problem.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_retrieval_unified.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/retrieval/unified.py src/opendomainmcp/retrieval/__init__.py tests/test_retrieval_unified.py
git commit -m "feat(retrieval): search_unified fuses chunks and articles via RRF"
```

---

### Task 3: `ask` uses unified retrieval + article-aware citations

**Files:**
- Modify: `src/opendomainmcp/query/rag.py`
- Test: `tests/test_rag.py`

**Interfaces:**
- Consumes: `search_unified` (Task 2), `Settings.retrieve_include_articles`.
- Produces: `answer_question` / `answer_question_stream` retrieve via `search_unified(store, query, top_k=top_k, mode=settings.search_mode, settings=settings)`. `_format_sources` renders an article source by `title`; `_citations` entries gain `"type"` (`"article"`/`"chunk"`), and an article citation's `source` is its `title`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rag.py — add
from opendomainmcp.config import Settings
from opendomainmcp.models import Article, Chunk
from opendomainmcp.query.rag import answer_question


def _arts(store):
    return store.sibling(f"{store.stats()['collection']}__articles")


def test_ask_includes_article_body_and_marks_citation_type(store):
    store.upsert([Chunk(text="approval needs a manager", source="r.md", kind="text",
                        start_line=1, end_line=1)])
    _arts(store).upsert([Article(
        title="Order Approval Rule", topic="order approval",
        body="Orders above $10k require manager sign-off.",
        source_chunk_ids=["a"], sources=["r.md:1"])])

    captured = {}

    def fake_synth(model, system, user):
        captured["user"] = user
        return "Per the rule [1]."

    # signature is answer_question(query, store, settings, top_k=..., synthesize=...)
    out = answer_question("when is approval needed?", store, Settings(),
                          top_k=5, synthesize=fake_synth)
    # the article body reached the LLM prompt
    assert "manager sign-off" in captured["user"]
    types = {c["type"] for c in out["citations"]}
    assert "article" in types
    art_cite = next(c for c in out["citations"] if c["type"] == "article")
    assert art_cite["source"] == "Order Approval Rule"


def test_format_sources_handles_article_metadata_without_source_key():
    from opendomainmcp.models import SearchResult
    from opendomainmcp.query.rag import _format_sources, _citations
    r = SearchResult(id="x", text="body", score=0.5,
                     metadata={"kind": "article", "title": "T", "topic": "tp",
                               "sources": "f.py:1 | g.md:2"})
    block = _format_sources([r])
    assert "T" in block and "body" in block          # title used as the label
    cites = _citations([r])
    assert cites[0]["type"] == "article" and cites[0]["source"] == "T"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_rag.py -k "article" -v`
Expected: FAIL — `KeyError: 'type'` in citations / article body absent (still using `store.search`).

- [ ] **Step 3: Write minimal implementation**

In `rag.py`, replace the two `store.search(query, top_k=top_k, mode=settings.search_mode)` calls (in `answer_question` and `answer_question_stream`) with:

```python
    from ..retrieval import search_unified
    results = search_unified(store, query, top_k=top_k,
                             mode=settings.search_mode, settings=settings)
```

Update `_format_sources` and `_citations`:

```python
def _source_label(r) -> str:
    meta = r.metadata
    if meta.get("kind") == "article":
        return meta.get("title") or meta.get("topic") or r.id
    loc = meta.get("source", "?")
    if meta.get("symbol"):
        loc += f"::{meta['symbol']}"
    return loc


def _format_sources(results: list[SearchResult]) -> str:
    blocks = []
    for i, r in enumerate(results, 1):
        blocks.append(f"[{i}] {_source_label(r)}\n{r.text}")
    return "\n\n".join(blocks)


def _citations(results: list[SearchResult]) -> list[dict]:
    cites = []
    for i, r in enumerate(results, 1):
        is_article = r.metadata.get("kind") == "article"
        cites.append({
            "n": i,
            "id": r.id,
            "source": _source_label(r),
            "symbol": None if is_article else r.metadata.get("symbol"),
            "score": r.score,
            "type": "article" if is_article else "chunk",
        })
    return cites
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_rag.py -v`
Expected: PASS (existing rag tests still green — chunk citations now also carry `type == "chunk"`; if an existing test asserts exact citation dict equality, update it to include `"type": "chunk"`).

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/query/rag.py tests/test_rag.py
git commit -m "feat(ask): retrieve via search_unified; article-aware sources and citations"
```

---

### Task 4: `search` surfaces (CLI + web) use unified retrieval

**Files:**
- Modify: `src/opendomainmcp/cli.py` (`_cmd_search`)
- Modify: `src/opendomainmcp/api/app.py` (`/api/search`)
- Test: `tests/test_cli.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `search_unified` (Task 2).
- Produces: both search surfaces fuse articles; CLI prints `[article] <title>` for an article hit (chunk output unchanged). The API returns article hits as normal `SearchResult.to_dict()` entries (frontend distinguishes by `metadata.kind`; no API shape change).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py — add (reuse this file's context-faking + capsys pattern)
def test_cli_search_includes_article_with_marker(monkeypatch, capsys):
    from opendomainmcp import cli
    from opendomainmcp.models import SearchResult

    def fake_unified(store, query, *, top_k, mode, settings, where=None,
                     source_contains=None):
        return [SearchResult(id="art", text="body", score=0.9,
                             metadata={"kind": "article", "title": "Order Rule"})]

    monkeypatch.setattr(cli, "build_context", lambda **kw: _FakeCtx())
    monkeypatch.setattr("opendomainmcp.retrieval.search_unified", fake_unified)
    rc = cli.main(["search", "approval"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[article]" in out and "Order Rule" in out
```

```python
# tests/test_api.py — add (reuse this file's TestClient fixture/pattern)
def test_api_search_returns_article_hit(client, monkeypatch):
    from opendomainmcp.models import SearchResult

    def fake_unified(store, query, *, top_k, mode, settings, where=None,
                     source_contains=None):
        return [SearchResult(id="art", text="body", score=0.9,
                             metadata={"kind": "article", "title": "Order Rule"})]

    monkeypatch.setattr("opendomainmcp.retrieval.search_unified", fake_unified)
    resp = client.post("/api/search", json={"query": "approval", "top_k": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert any(item["metadata"].get("kind") == "article" for item in data)
```

(If `tests/test_api.py` builds its `client` against a real in-memory context, the monkeypatch target must match how `app.py` imports `search_unified` — patch `opendomainmcp.retrieval.search_unified` and have `app.py` call it as `from ..retrieval import search_unified` inside the route, so the patched module attribute is resolved at call time.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -k "search_includes_article" tests/test_api.py -k "article_hit" -v`
Expected: FAIL — CLI has no `[article]` output; API still calls `ctx.store.search`.

- [ ] **Step 3: Write minimal implementation**

In `cli.py` `_cmd_search`, replace the `ctx.store.search(...)` call and the per-result print:

```python
def _cmd_search(ctx, args) -> int:
    from .store import build_where
    from .retrieval import search_unified

    where = build_where({"kind": args.kind, "language": args.language, "symbol": args.symbol})
    results = search_unified(
        ctx.store, args.query, top_k=args.top_k, where=where,
        mode=ctx.settings.search_mode, settings=ctx.settings,
        source_contains=args.source,
    )
    if not results:
        print("No results.")
        return 0
    for i, r in enumerate(results, 1):
        meta = r.metadata
        if meta.get("kind") == "article":
            print(f"\n#{i}  score={r.score:.3f}  [article] {meta.get('title', '?')}")
        else:
            loc = meta.get("source", "?")
            if meta.get("symbol"):
                loc += f"::{meta['symbol']}"
            print(f"\n#{i}  score={r.score:.3f}  {loc}")
        if meta.get("summary"):
            print(f"    summary: {meta['summary']}")
        snippet = r.text.strip().replace("\n", " ")
        print(f"    {snippet[:200]}")
    return 0
```

In `api/app.py` `/api/search`, replace the `ctx.store.search(...)` call:

```python
        from ..store import build_where
        from ..retrieval import search_unified

        filters = {"kind": req.kind, "language": req.language, "symbol": req.symbol}
        if ctx.settings.retrieve_approved_only:
            filters["review_status"] = "approved"
        where = build_where(filters)
        results = search_unified(
            ctx.store, req.query, top_k=req.top_k, where=where,
            mode=ctx.settings.search_mode, settings=ctx.settings,
            source_contains=req.source_contains,
        )
        out = [r.to_dict() for r in results]
        insight_routes.record_retrieval(ctx, "search", req.query, out)
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -k search tests/test_api.py -k search -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: PASS (all green, no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/opendomainmcp/cli.py src/opendomainmcp/api/app.py tests/test_cli.py tests/test_api.py
git commit -m "feat(search): CLI and web search retrieve via search_unified"
```

---

## Self-Review Notes

- **Spec coverage:** `search_unified` fusion via `rrf_fuse` (Task 2) ✓; flag-off / no-articles == today (Task 2 tests) ✓; `ask` integration + article-aware `_format_sources`/`_citations` with `type` (Task 3) ✓; CLI + web `search` surfaces, article `[article]` marker, no API shape change (Task 4) ✓; setting `retrieve_include_articles` default-on + runtime-editable (Task 1) ✓; views/advisor/low-level `store.search` untouched (no task modifies them) ✓; Fail-Loud formatting fallback `title → topic → id` (Task 3 `_source_label`) ✓.
- **Resolved decisions honored:** default on (Task 1); only articles marked, chunk output unchanged (Task 4); top_k each then fuse to top_k (Task 2 `search_unified`).
- **Spec refinement during planning:** `search_unified` also takes `where`/`source_contains` (the web route and CLI pass filters); when a chunk-only filter is set, articles naturally drop out (kind=article won't match a `kind`/`language`/`symbol`/`review_status` where, and articles have no `source`), which is the desired behavior.
- **Type consistency:** `search_unified(store, query, *, top_k, mode, settings, where=None, source_contains=None) -> list[SearchResult]` used identically in Tasks 3 and 4; citation dicts gain `"type"` consistently; `_source_label` shared by `_format_sources` and `_citations`.
- **Implementer note:** in `test_rag.py` the `answer_question` call signature is `answer_question(query, store, settings, top_k=..., synthesize=...)` — positional `query` first (drop the `if False else` scaffolding in the example and call it directly). For `test_cli.py`/`test_api.py`, reuse this repo's existing `_FakeCtx` / `client` fixtures rather than inventing new ones; patch `opendomainmcp.retrieval.search_unified` and call it via `from ..retrieval import search_unified` inside the route/handler so the patch resolves at call time. If an existing rag/citation test asserts an exact citation dict, add `"type": "chunk"` to its expectation.
