# Batch Extraction (Haiku + Batch API) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut extraction API cost by making the cheaper-model choice a documented setting (Haiku) and adding an opt-in whole-corpus Batch API extraction path (50% off) for the Anthropic backend.

**Architecture:** A new `ingest/batch_extract.py` provides `BatchExtractor` (submit one Message Batch for all chunks, poll to completion, parse results) plus a `CachedExtractor` wrapper. The pipeline gains a pre-pass: when `extract_batch` is on, it load+splits every file once to collect chunk texts, batch-extracts them into a `{text_hash: KnowledgeUnit}` cache, then runs the **unchanged** per-file ingest loop with the extractor swapped for `CachedExtractor`. The proven `_ingest_file` flow is untouched; load+split runs twice (local only, no API cost).

**Tech Stack:** Python ≥3.11, anthropic SDK (Messages Batches API), pydantic-settings, pytest (offline — fake batch client).

## Global Constraints

- Settings use the `ODM_` prefix (pydantic `env_prefix="ODM_"`).
- Batch is **Anthropic-only**: `extract_batch` true with `llm_backend != "anthropic"` must Fail Loud (raise) before any work — never silently fall back to sync.
- `extract_batch` false → **zero behavior change** (pre-pass/CachedExtractor never constructed).
- Fail Loud: per-request batch failures are recorded and fall back to one live call; batch submit/poll API errors propagate.
- Batch and non-batch extraction must produce **identical** `KnowledgeUnit`s — reuse the existing `_SYSTEM` prompt and `_parse` from `extract/knowledge.py`; do not re-author them.
- Tests are fully offline — inject a fake `batches` client; no network, no real anthropic client.
- Match existing code style in `ingest/` and `extract/`.

---

### Task 1: Config setting + docs

**Files:**
- Modify: `src/opendomainmcp/config.py` (the `EDITABLE_FIELDS` tuple ~line 27-45, and the Embedding/extraction settings block ~line 85-92)
- Modify: `.env.example` (near `ODM_EMBEDDER_MODEL` line 20 for the Haiku note; near the extraction vars for `ODM_EXTRACT_BATCH`)
- Test: `tests/test_config.py` (create if absent)

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.extract_batch: bool = False`; `"extract_batch"` present in `EDITABLE_FIELDS`.

- [ ] **Step 1: Write the failing test**

Create or append to `tests/test_config.py`:

```python
from opendomainmcp.config import Settings, EDITABLE_FIELDS


def test_extract_batch_defaults_off_and_is_editable():
    s = Settings()
    assert s.extract_batch is False
    assert "extract_batch" in EDITABLE_FIELDS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_config.py::test_extract_batch_defaults_off_and_is_editable -v`
Expected: FAIL — `AttributeError`/missing `extract_batch`.

- [ ] **Step 3: Add the setting and register it as editable**

In `src/opendomainmcp/config.py`, add to the extraction settings block (right after `extract_structured_output: bool = False` near line 92):

```python
    # Opt-in: extract via the Anthropic Message Batches API (50% cheaper, async).
    # Anthropic backend only. See ingest/batch_extract.py.
    extract_batch: bool = False
```

Then add `"extract_batch",` to the `EDITABLE_FIELDS` tuple (alongside `"extract_concurrency"` and the other `extract_*` entries).

- [ ] **Step 4: Document both levers in `.env.example`**

After the `ODM_EMBEDDER_MODEL=...` line (~line 20), add the Haiku note. After the extraction vars, add the batch flag:

```bash
# Extraction model. A cheaper model (Haiku 4.5) cuts extraction cost ~3-5x:
#   ODM_EXTRACTION_MODEL=claude-haiku-4-5
# Opt-in: extract via the Anthropic Message Batches API (50% cheaper, runs
# asynchronously; Anthropic backend only). ingest blocks until the batch ends.
# ODM_EXTRACT_BATCH=false
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_config.py::test_extract_batch_defaults_off_and_is_editable -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/opendomainmcp/config.py .env.example tests/test_config.py
git commit -m "feat(config): add extract_batch setting + document Haiku option"
```

---

### Task 2: `_text_hash`, `BatchItem`, `CachedExtractor`

**Files:**
- Create: `src/opendomainmcp/ingest/batch_extract.py`
- Test: `tests/test_batch_extract.py`

**Interfaces:**
- Consumes: `KnowledgeUnit` from `..models`.
- Produces:
  - `_text_hash(text: str) -> str` — `sha256(text.encode("utf-8")).hexdigest()` (64 hex chars; within the Anthropic 64-char `custom_id` limit).
  - `BatchItem` — `@dataclass` with `text_hash: str`, `text: str`, `kind: str`, `language: str | None`.
  - `CachedExtractor(cache: dict[str, KnowledgeUnit], fallback)` with `extract(text, kind, language=None) -> KnowledgeUnit` returning `cache[_text_hash(text)]` on hit, else `fallback.extract(text, kind, language)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_batch_extract.py`:

```python
from opendomainmcp.ingest.batch_extract import _text_hash, BatchItem, CachedExtractor
from opendomainmcp.models import KnowledgeUnit


def test_text_hash_is_deterministic_64_hex():
    h = _text_hash("hello world")
    assert h == _text_hash("hello world")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
    assert _text_hash("other") != h


def test_cached_extractor_returns_hit():
    ku = KnowledgeUnit(summary="cached")
    cache = {_text_hash("abc"): ku}

    class BoomFallback:
        def extract(self, *a, **k):
            raise AssertionError("fallback should not be called on a hit")

    ext = CachedExtractor(cache, BoomFallback())
    assert ext.extract("abc", "text") is ku


def test_cached_extractor_falls_back_on_miss():
    calls = []

    class Fallback:
        def extract(self, text, kind, language=None):
            calls.append((text, kind))
            return KnowledgeUnit(summary="live")

    ext = CachedExtractor({}, Fallback())
    out = ext.extract("missing", "code", "python")
    assert out.summary == "live"
    assert calls == [("missing", "code")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_batch_extract.py -v`
Expected: FAIL — module `batch_extract` does not exist.

- [ ] **Step 3: Implement the helpers**

Create `src/opendomainmcp/ingest/batch_extract.py`:

```python
"""Whole-corpus extraction via the Anthropic Message Batches API (50% cheaper).

``BatchExtractor`` submits one batch for all chunk texts, polls to completion,
and parses results into ``KnowledgeUnit``s, reusing the same ``_SYSTEM`` prompt
and ``_parse`` as the synchronous ``ClaudeExtractor`` so output is identical.
``CachedExtractor`` lets the pipeline run its unchanged per-file loop against the
pre-computed results, falling back to a live call on a miss (Fail Loud).
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

from ..models import KnowledgeUnit

logger = logging.getLogger(__name__)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class BatchItem:
    text_hash: str
    text: str
    kind: str
    language: str | None = None


class CachedExtractor:
    """Extractor that serves pre-computed results; falls back to a live call."""

    def __init__(self, cache: dict[str, KnowledgeUnit], fallback):
        self._cache = cache
        self._fallback = fallback

    def extract(self, text: str, kind: str, language=None) -> KnowledgeUnit:
        hit = self._cache.get(_text_hash(text))
        if hit is not None:
            return hit
        return self._fallback.extract(text, kind, language)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_batch_extract.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/ingest/batch_extract.py tests/test_batch_extract.py
git commit -m "feat(extract): add _text_hash, BatchItem, CachedExtractor"
```

---

### Task 3: `BatchExtractor.extract_many`

**Files:**
- Modify: `src/opendomainmcp/ingest/batch_extract.py`
- Test: `tests/test_batch_extract.py`

**Interfaces:**
- Consumes: `_SYSTEM`, `_parse` from `..extract.knowledge`; `BatchItem`, `KnowledgeUnit`.
- Produces: `BatchExtractor(client, model: str, max_tokens: int = 900, poll_interval: float = 10.0)` with
  `extract_many(items: list[BatchItem], progress=None) -> dict[str, KnowledgeUnit]`.
  - Builds one request per item: `custom_id=item.text_hash`, params `model`, `max_tokens`, `system=_SYSTEM`, `messages=[{"role":"user","content": f"Snippet type: {label}\n\n{item.text}"}]` where `label = item.kind + (f" ({item.language})" if item.language else "")`.
  - Submits via `client.messages.batches.create(requests=...)`, polls `client.messages.batches.retrieve(id).processing_status` until `"ended"` (sleep `poll_interval` between polls), then iterates `client.messages.batches.results(id)`.
  - `succeeded` → `_parse(joined text)` into the map; any other result type → omitted (caller's `CachedExtractor` falls back).
  - `progress(detail: str)` called with a short status string when provided.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_batch_extract.py`:

```python
from opendomainmcp.ingest.batch_extract import BatchExtractor


def _msg(text):
    block = type("B", (), {"type": "text", "text": text})
    return type("M", (), {"content": [block]})


class _FakeBatches:
    """Mimics client.messages.batches: create / retrieve / results."""

    def __init__(self, results, ends_after=1):
        self._results = results          # list of (custom_id, type, text)
        self._ends_after = ends_after
        self._retrieves = 0
        self.created_requests = None

    def create(self, requests):
        self.created_requests = requests
        return type("Batch", (), {"id": "batch_1", "processing_status": "in_progress"})

    def retrieve(self, _id):
        self._retrieves += 1
        status = "ended" if self._retrieves >= self._ends_after else "in_progress"
        counts = type("C", (), {"processing": 0, "succeeded": len(self._results), "errored": 0})
        return type("Batch", (), {"processing_status": status, "request_counts": counts})

    def results(self, _id):
        for cid, rtype, text in self._results:
            if rtype == "succeeded":
                inner = type("R", (), {"type": "succeeded", "message": _msg(text)})
            else:
                inner = type("R", (), {"type": rtype})
            yield type("Res", (), {"custom_id": cid, "result": inner})


def _fake_client(fake_batches):
    messages = type("Messages", (), {"batches": fake_batches})
    return type("Client", (), {"messages": messages})()


def test_extract_many_builds_requests_and_parses_succeeded():
    good = '{"summary":"S","concepts":["c"],"relations":[],"knowledge_type":"Feature","audience":[],"confidence":1}'
    items = [BatchItem(text_hash="h1", text="alpha", kind="text"),
             BatchItem(text_hash="h2", text="beta", kind="code", language="python")]
    fake = _FakeBatches([("h1", "succeeded", good), ("h2", "errored", "")])
    ext = BatchExtractor(_fake_client(fake), "claude-haiku-4-5", poll_interval=0)

    out = ext.extract_many(items)

    # request assembly
    assert len(fake.created_requests) == 2
    r0 = fake.created_requests[0]
    cid = r0["custom_id"] if isinstance(r0, dict) else r0.custom_id
    assert cid == "h1"
    # succeeded parsed, errored omitted
    assert out["h1"].summary == "S"
    assert "h2" not in out


def test_extract_many_polls_until_ended():
    good = '{"summary":"S","concepts":[],"relations":[],"knowledge_type":"Feature","audience":[],"confidence":1}'
    fake = _FakeBatches([("h1", "succeeded", good)], ends_after=3)
    ext = BatchExtractor(_fake_client(fake), "claude-haiku-4-5", poll_interval=0)
    out = ext.extract_many([BatchItem(text_hash="h1", text="x", kind="text")])
    assert fake._retrieves == 3
    assert out["h1"].summary == "S"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_batch_extract.py -k extract_many -v`
Expected: FAIL — `BatchExtractor` has no `extract_many`.

- [ ] **Step 3: Implement `BatchExtractor`**

Append to `src/opendomainmcp/ingest/batch_extract.py`:

```python
class BatchExtractor:
    def __init__(self, client, model: str, max_tokens: int = 900,
                 poll_interval: float = 10.0):
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._poll_interval = poll_interval

    def _request(self, item: BatchItem) -> dict:
        from ..extract.knowledge import _SYSTEM

        label = item.kind + (f" ({item.language})" if item.language else "")
        return {
            "custom_id": item.text_hash,
            "params": {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "system": _SYSTEM,
                "messages": [{
                    "role": "user",
                    "content": f"Snippet type: {label}\n\n{item.text}",
                }],
            },
        }

    def extract_many(self, items: list[BatchItem], progress=None) -> dict:
        from ..extract.knowledge import _parse

        if not items:
            return {}
        batches = self._client.messages.batches
        batch = batches.create(requests=[self._request(i) for i in items])
        while True:
            status = batches.retrieve(batch.id)
            if progress is not None:
                c = getattr(status, "request_counts", None)
                if c is not None:
                    progress(f"{c.succeeded} done, {c.processing} processing, "
                             f"{c.errored} errored")
            if status.processing_status == "ended":
                break
            time.sleep(self._poll_interval)

        out: dict[str, KnowledgeUnit] = {}
        for res in batches.results(batch.id):
            if res.result.type != "succeeded":
                logger.warning("batch extraction failed for %s: %s",
                               res.custom_id, res.result.type)
                continue
            raw = "".join(b.text for b in res.result.message.content
                          if b.type == "text")
            try:
                out[res.custom_id] = _parse(raw)
            except Exception as exc:  # malformed output: omit -> live fallback
                logger.warning("batch parse failed for %s: %r", res.custom_id, exc)
        return out
```

> Note: `client.messages.batches.create` accepts either typed `Request`/`MessageCreateParamsNonStreaming` objects or plain dicts; plain dicts keep this module dependency-light and are what the test asserts on.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_batch_extract.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/ingest/batch_extract.py tests/test_batch_extract.py
git commit -m "feat(extract): BatchExtractor.extract_many via Message Batches API"
```

---

### Task 4: Refactor `_load_and_split` out of `_ingest_file`

**Files:**
- Modify: `src/opendomainmcp/ingest/pipeline.py:153-211` (`_ingest_file`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: existing `load_file`, `split_code`, `split_openapi`, `split_graphql`, `RecursiveTextSplitter`, `Chunk`.
- Produces: `Pipeline._load_and_split(self, path: Path) -> list[Chunk]` — loads and splits one file, assigns `chunk_index`, returns the chunk list (possibly empty). Raises `UnsupportedFileError` / other load errors to the caller. Emits no progress. `_ingest_file` is rewritten to call it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline.py`:

```python
def test_load_and_split_returns_indexed_chunks(pipeline, tmp_path):
    f = tmp_path / "calc.py"
    f.write_text("def add(a, b):\n    return a + b\n")
    chunks = pipeline._load_and_split(f)
    assert chunks and all(c.chunk_index == i for i, c in enumerate(chunks))
    assert all(c.kind == "code" for c in chunks)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_pipeline.py::test_load_and_split_returns_indexed_chunks -v`
Expected: FAIL — `Pipeline` has no `_load_and_split`.

- [ ] **Step 3: Extract the method and rewrite `_ingest_file`**

Replace the body of `_ingest_file` (lines 153-211) so that load+split lives in a new helper. The new helper (place it directly above `_ingest_file`):

```python
    def _load_and_split(self, path: Path) -> list[Chunk]:
        """Load and split one file into indexed chunks. Raises on load failure;
        returns [] for an empty document. Emits no progress (callers do)."""
        doc = load_file(path)
        if doc.kind == "code":
            chunks = split_code(doc.text, doc.language, str(path),
                                self._settings.code_max_chunk_chars)
        elif doc.kind == "api":
            if doc.language == "graphql":
                from .graphql import split_graphql

                chunks = split_graphql(doc.text, str(path))
            else:
                from .openapi import split_openapi

                chunks = split_openapi(doc.text, str(path))
        else:
            chunks = [Chunk(text=t, source=str(path), kind="text")
                      for t in self._splitter.split(doc.text)]
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i
        return chunks
```

Rewrite `_ingest_file` to use it (preserving every report/emit branch):

```python
    def _ingest_file(self, path: Path, report: IngestReport, progress: Optional[Progress]):
        self._emit(progress, "load", str(path))
        try:
            chunks = self._load_and_split(path)
        except UnsupportedFileError as exc:
            report.skipped.append({"path": str(path), "reason": str(exc)})
            self._emit(progress, "skip", str(path), detail=str(exc))
            return
        except Exception as exc:  # unexpected read error: report, keep going
            report.errors.append({"path": str(path), "error": repr(exc)})
            self._emit(progress, "error", str(path), detail=repr(exc))
            return

        self._emit(progress, "split", str(path))
        if not chunks:
            report.skipped.append({"path": str(path), "reason": "no content"})
            self._emit(progress, "skip", str(path), detail="no content")
            return

        self._emit(progress, "extract", str(path), detail=f"{len(chunks)} chunks")
        self._extract_all(chunks, path, report)

        new_ids = {c.id for c in chunks}
        stale = self._store.get_ids_for_source(str(path)) - new_ids
        if stale:
            self._store.delete_ids(stale)
            self._graph.delete_for_chunks(stale)
            report.chunks_pruned += len(stale)
            self._emit(progress, "prune", str(path), detail=f"{len(stale)} stale")

        self._emit(progress, "embed", str(path))
        stored = self._store.upsert(chunks)
        self._write_graph(chunks)
        self._write_deps(chunks)
        self._write_workflow(chunks)
        self._emit(progress, "store", str(path), detail=f"{stored} chunks")

        report.files_indexed += 1
        report.chunks_indexed += stored
```

- [ ] **Step 4: Run the new test + the full pipeline suite (refactor safety net)**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_pipeline.py -v`
Expected: the new test passes and all pre-existing pipeline tests still pass (behavior unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/ingest/pipeline.py tests/test_pipeline.py
git commit -m "refactor(pipeline): extract _load_and_split from _ingest_file"
```

---

### Task 5: Batch pre-pass + extractor swap + gating

**Files:**
- Modify: `src/opendomainmcp/ingest/pipeline.py` (`_ingest` ~line 123; add helpers near `_extract_all`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `BatchExtractor`, `CachedExtractor`, `BatchItem`, `_text_hash` from `.batch_extract` (Tasks 2-3); `_load_and_split` (Task 4); `Settings.extract_batch`, `Settings.llm_backend`, `Settings.extraction_model` (Task 1).
- Produces: pipeline behavior — when `extract_batch` is on and backend is anthropic, all extractable chunks are batch-extracted before the per-file loop runs with a `CachedExtractor`; non-anthropic backend raises `ValueError`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:

```python
def test_batch_mode_uses_prepass_cache(store, fake_graph, tmp_path):
    import opendomainmcp.ingest.pipeline as pipeline_mod
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline
    from opendomainmcp.ingest.batch_extract import _text_hash
    from opendomainmcp.models import KnowledgeUnit

    (tmp_path / "notes.md").write_text(
        "# Vector databases\n\nEmbeddings power similarity search for RAG.\n"
    )

    class BoomExtractor:  # live extraction must NOT happen in batch mode
        def extract(self, *a, **k):
            raise AssertionError("live extract called; cache miss in batch mode")

    settings = Settings(chunk_size=200, chunk_overlap=20,
                        extract_batch=True, llm_backend="anthropic")
    pipe = Pipeline(store, BoomExtractor(), settings, graph=fake_graph)

    # Fake batch extractor: cache every chunk text the pre-pass collects.
    class FakeBatch:
        def extract_many(self, items, progress=None):
            return {it.text_hash: KnowledgeUnit(summary=f"batch {it.kind}")
                    for it in items}

    pipe._build_batch_extractor = lambda: FakeBatch()

    report = pipe.ingest_path(tmp_path)
    assert report.files_indexed == 1
    items = store.get_items(limit=10)
    assert items and all(i["metadata"]["summary"].startswith("batch")
                         for i in items if "summary" in i["metadata"])


def test_batch_mode_requires_anthropic_backend(store, fake_graph, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline

    (tmp_path / "notes.md").write_text("# x\n\nsome content here for a chunk.\n")
    settings = Settings(chunk_size=200, chunk_overlap=20,
                        extract_batch=True, llm_backend="openai")
    pipe = Pipeline(store, None, settings, graph=fake_graph)

    import pytest
    with pytest.raises(ValueError, match="anthropic"):
        pipe.ingest_path(tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_pipeline.py -k "batch_mode" -v`
Expected: FAIL — no pre-pass; live extractor used / no gating.

- [ ] **Step 3: Wire the pre-pass into `_ingest` and add helpers**

In `_ingest`, replace the per-file loop (lines 123-124) with a gated pre-pass + swap:

```python
        with self._batch_prepass(files, report, progress):
            for file_path in files:
                self._ingest_file(file_path, report, progress)
```

Add these helpers near `_extract_all` (and `import contextlib` at the top of the file, next to the other stdlib imports):

```python
    @contextlib.contextmanager
    def _batch_prepass(self, files, report: IngestReport,
                       progress: Optional[Progress]):
        """When extract_batch is on, batch-extract all chunk texts up front and
        run the per-file loop with a CachedExtractor. No-op otherwise."""
        if not getattr(self._settings, "extract_batch", False):
            yield
            return
        if self._settings.llm_backend.lower() != "anthropic":
            raise ValueError(
                "ODM_EXTRACT_BATCH requires the anthropic LLM backend"
            )
        cache = self._batch_extract_files(files, report, progress)
        from .batch_extract import CachedExtractor

        original = self._extractor
        self._extractor = CachedExtractor(cache, original)
        try:
            yield
        finally:
            self._extractor = original

    def _batch_extract_files(self, files, report: IngestReport,
                             progress: Optional[Progress]) -> dict:
        from .batch_extract import BatchItem, _text_hash

        items: dict[str, BatchItem] = {}
        for f in files:
            try:
                chunks = self._load_and_split(f)
            except Exception:
                continue  # the real per-file pass records skip/error
            for c in chunks:
                if c.knowledge and c.knowledge.knowledge_type:
                    continue  # pre-classified; not LLM-extracted
                h = _text_hash(c.text)
                if h not in items:
                    items[h] = BatchItem(text_hash=h, text=c.text,
                                         kind=c.kind, language=c.language)
        if not items:
            return {}
        self._emit(progress, "batch", "extraction",
                   detail=f"{len(items)} chunks submitted")
        extractor = self._build_batch_extractor()
        return extractor.extract_many(
            list(items.values()),
            progress=lambda d: self._emit(progress, "batch", "extraction", detail=d),
        )

    def _build_batch_extractor(self):
        import anthropic

        from .batch_extract import BatchExtractor

        client = anthropic.Anthropic(
            timeout=self._settings.request_timeout,
            max_retries=self._settings.max_retries,
        )
        return BatchExtractor(client, self._settings.extraction_model)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_pipeline.py -k "batch_mode" -v`
Expected: 2 passed.

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `PYTHONPATH=. .venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/opendomainmcp/ingest/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): whole-corpus batch extraction pre-pass with gating"
```

---

## Self-Review

**Spec coverage:**
- #2 Haiku documented (config-only) → Task 1 (`.env.example` note).
- #4 caching dropped (no-op) → not built; noted in spec only. No task needed. ✓
- `extract_batch` setting + EDITABLE_FIELDS → Task 1.
- `_text_hash` / `BatchItem` / `CachedExtractor` → Task 2.
- `BatchExtractor.extract_many` (submit/poll/parse, reuse `_SYSTEM`/`_parse`, errored→omit) → Task 3.
- `_load_and_split` refactor (no double-split logic drift) → Task 4.
- Whole-corpus pre-pass + extractor swap (try/finally) + skip pre-classified + Anthropic-only Fail-Loud gating → Task 5.
- Identical extraction batch vs non-batch → Tasks 3 (reuse `_SYSTEM`/`_parse`).
- Per-request failure → cache miss → live fallback → Tasks 2 (CachedExtractor) + 3 (omit on failure).
- Offline tests (fake batch client) → Tasks 2, 3, 5.

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `_text_hash(text)->str`, `BatchItem(text_hash,text,kind,language)`, `CachedExtractor(cache,fallback).extract`, `BatchExtractor(client,model,max_tokens,poll_interval).extract_many(items,progress)`, `Pipeline._load_and_split(path)->list[Chunk]`, `_build_batch_extractor()->BatchExtractor` — used consistently across Tasks 2-5. Setting name `extract_batch` consistent across Tasks 1 and 5.
