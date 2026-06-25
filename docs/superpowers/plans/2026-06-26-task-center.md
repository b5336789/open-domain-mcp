# Task Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A server-persisted Task Center — a serial background queue for ingest / synthesize / re-extract tasks, surfaced by a top-right button + slide-over panel that shows progress, supports cancel, and survives page reloads.

**Architecture:** A JSON-backed `TaskStore` (atomic writes, lock) holds lightweight task *summaries* (counts only). A single daemon `TaskWorker` thread runs queued tasks one at a time, dispatching to per-type runners that update progress through the store. Child detail (files/topics/sources) is enumerated once into a names file and paginated on demand; per-task status derives from a `done` count + sparse `failures` list (work is processed in order), so progress writes stay O(1) and the design scales to ~100k-file codebases. The ingest runner reuses the existing `ingest/checkpoint.py` for resume.

**Tech Stack:** Python 3.11, FastAPI, dataclasses, threading; React + TypeScript (Vite), Tailwind. All tests offline (no network) using existing conftest fakes.

## Global Constraints

- Backend lives under `src/opendomainmcp/`; tests under `tests/`; run with `pytest` (offline).
- Atomic file writes: temp file + `os.replace` (mirror `ingest/checkpoint.py`).
- Fail Loud: per-child failures recorded (never dropped); runner exceptions → task `status="error"` with `error` set.
- Task history cap: **100,000** tasks (evict oldest *finished* beyond cap).
- Progress persistence throttled to **≤ once per 2 seconds or every 100 children** (whichever first).
- Serial execution: exactly one task runs at a time.
- Cancellation is parent-level (cooperative; checked between children).
- Frontend must pass `npx tsc --noEmit` and `npm run build` (run from `web/`).
- Task store is global (across collections); each task records its `collection`.

---

### Task 1: Task data model (`tasks/models.py`)

**Files:**
- Create: `src/opendomainmcp/tasks/__init__.py`
- Create: `src/opendomainmcp/tasks/models.py`
- Test: `tests/test_task_models.py`

**Interfaces:**
- Produces:
  - `TaskStatus` literals: `"queued" | "running" | "done" | "error" | "cancelled"`.
  - `@dataclass Task` with fields: `id: str`, `type: str` (`ingest|synthesize|extract`), `title: str`, `collection: str`, `status: str = "queued"`, `created_at: float = 0.0`, `started_at: float | None = None`, `finished_at: float | None = None`, `total: int = 0`, `done: int = 0`, `failures: list[dict] = []` (each `{"name", "status"}`), `cancel_requested: bool = False`, `error: str | None = None`, `result: dict | None = None`, `params: dict = {}`.
  - `Task.to_dict() -> dict` (all fields) and `Task.from_dict(d: dict) -> Task`.
  - `Task.is_terminal() -> bool` → `status in {"done","error","cancelled"}`.
  - `derive_child_status(index: int, done: int, running: bool, failure: str | None) -> str`: returns `failure` if not None, else `"done"` if `index < done`, else `"running"` if `index == done and running`, else `"pending"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_task_models.py
from opendomainmcp.tasks.models import Task, derive_child_status


def test_task_round_trip_and_terminal():
    t = Task(id="a1", type="ingest", title="Ingest /x", collection="c",
             total=3, done=1, params={"path": "/x"})
    d = t.to_dict()
    assert d["type"] == "ingest" and d["total"] == 3
    t2 = Task.from_dict(d)
    assert t2.id == "a1" and t2.params == {"path": "/x"}
    assert not t.is_terminal()
    t.status = "done"
    assert t.is_terminal()


def test_derive_child_status_prefix_and_failure():
    # done=2, running -> indices 0,1 done; index 2 running; 3+ pending
    assert derive_child_status(0, 2, True, None) == "done"
    assert derive_child_status(2, 2, True, None) == "running"
    assert derive_child_status(3, 2, True, None) == "pending"
    assert derive_child_status(2, 2, False, None) == "pending"  # task not running
    assert derive_child_status(1, 2, True, "error") == "error"  # failure overrides
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_task_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'opendomainmcp.tasks'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/opendomainmcp/tasks/__init__.py
"""Task Center: serial background queue for ingest/synthesize/extract."""
```

```python
# src/opendomainmcp/tasks/models.py
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

TERMINAL = {"done", "error", "cancelled"}


@dataclass
class Task:
    id: str
    type: str               # ingest | synthesize | extract
    title: str
    collection: str
    status: str = "queued"  # queued | running | done | error | cancelled
    created_at: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    total: int = 0
    done: int = 0
    failures: list = field(default_factory=list)   # [{"name","status"}]
    cancel_requested: bool = False
    error: Optional[str] = None
    result: Optional[dict] = None
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        known = {f: d[f] for f in cls.__dataclass_fields__ if f in d}
        return cls(**known)

    def is_terminal(self) -> bool:
        return self.status in TERMINAL


def derive_child_status(index: int, done: int, running: bool,
                        failure: Optional[str]) -> str:
    if failure is not None:
        return failure
    if index < done:
        return "done"
    if index == done and running:
        return "running"
    return "pending"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_task_models.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/tasks/__init__.py src/opendomainmcp/tasks/models.py tests/test_task_models.py
git commit -m "feat(tasks): Task data model + child-status derivation"
```

---

### Task 2: Task store (`tasks/store.py`)

**Files:**
- Create: `src/opendomainmcp/tasks/store.py`
- Test: `tests/test_task_store.py`

**Interfaces:**
- Consumes: `Task`, `derive_child_status` from Task 1.
- Produces `TaskStore(data_dir)` with:
  - `create(type: str, title: str, collection: str, params: dict) -> Task` — status `queued`, `created_at` set, persisted; returns the Task.
  - `list() -> list[Task]` — non-terminal first (queued/running), then terminal; within each group newest `created_at` first.
  - `get(task_id: str) -> Task | None`.
  - `update(task_id, throttle=False, **fields) -> None` — apply fields to the task; persist immediately unless `throttle` (then persist only if ≥2s since last write or ≥100 `done` advanced).
  - `next_queued() -> Task | None` — oldest `queued` task.
  - `request_cancel(task_id) -> bool`.
  - `clear_finished() -> int` — remove terminal tasks; returns count.
  - `set_children_names(task_id, names: list[str]) -> None` — write `<data_dir>/.tasks/<id>.names.json` once; sets `total=len(names)`.
  - `read_children(task_id, offset=0, limit=100) -> dict` — `{"children":[{"name","status"}], "total": int}` deriving status from `done`/`failures`/`status` via `derive_child_status`.
  - Internal: cap eviction to 100_000 (drop oldest terminal) on persist.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_task_store.py
from opendomainmcp.tasks.store import TaskStore


def test_create_list_ordering_and_persistence(tmp_path):
    s = TaskStore(tmp_path)
    a = s.create("ingest", "A", "c", {"path": "/a"})
    b = s.create("synthesize", "B", "c", {})
    s.update(a.id, status="done")
    # reload from disk (new instance) -> persisted
    s2 = TaskStore(tmp_path)
    ids = [t.id for t in s2.list()]
    assert ids[0] == b.id          # queued before terminal
    assert a.id in ids
    assert s2.get(a.id).status == "done"


def test_next_queued_is_oldest(tmp_path):
    s = TaskStore(tmp_path)
    a = s.create("ingest", "A", "c", {})
    s.create("ingest", "B", "c", {})
    assert s.next_queued().id == a.id


def test_children_names_and_derived_status(tmp_path):
    s = TaskStore(tmp_path)
    t = s.create("ingest", "A", "c", {})
    s.set_children_names(t.id, ["f0", "f1", "f2", "f3"])
    s.update(t.id, status="running", done=2,
             failures=[{"name": "f1", "status": "skipped"}])
    page = s.read_children(t.id, offset=0, limit=10)
    assert page["total"] == 4
    by_name = {c["name"]: c["status"] for c in page["children"]}
    assert by_name == {"f0": "done", "f1": "skipped", "f2": "running", "f3": "pending"}


def test_read_children_pagination(tmp_path):
    s = TaskStore(tmp_path)
    t = s.create("ingest", "A", "c", {})
    s.set_children_names(t.id, [f"f{i}" for i in range(250)])
    page = s.read_children(t.id, offset=100, limit=50)
    assert page["total"] == 250
    assert [c["name"] for c in page["children"]][:2] == ["f100", "f101"]
    assert len(page["children"]) == 50


def test_clear_finished_keeps_active(tmp_path):
    s = TaskStore(tmp_path)
    a = s.create("ingest", "A", "c", {})
    b = s.create("ingest", "B", "c", {})
    s.update(a.id, status="done")
    assert s.clear_finished() == 1
    remaining = [t.id for t in s.list()]
    assert remaining == [b.id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_task_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'opendomainmcp.tasks.store'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/opendomainmcp/tasks/store.py
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from .models import Task, derive_child_status

HISTORY_CAP = 100_000
THROTTLE_SECONDS = 2.0
THROTTLE_COUNT = 100


class TaskStore:
    def __init__(self, data_dir):
        self._dir = Path(data_dir)
        self._index = self._dir / "tasks.json"
        self._children_dir = self._dir / ".tasks"
        self._lock = threading.RLock()
        self._tasks: dict[str, Task] = {}
        self._last_write: dict[str, tuple[float, int]] = {}  # id -> (ts, done)
        self._load()

    # -- persistence ----------------------------------------------------
    def _load(self) -> None:
        if self._index.exists():
            data = json.loads(self._index.read_text(encoding="utf-8"))
            for d in data.get("tasks", []):
                t = Task.from_dict(d)
                self._tasks[t.id] = t

    def _persist(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._evict()
        payload = {"tasks": [t.to_dict() for t in self._tasks.values()]}
        tmp = self._index.with_name(self._index.name + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self._index)

    def _evict(self) -> None:
        if len(self._tasks) <= HISTORY_CAP:
            return
        finished = sorted(
            (t for t in self._tasks.values() if t.is_terminal()),
            key=lambda t: t.created_at,
        )
        for t in finished[: len(self._tasks) - HISTORY_CAP]:
            self._tasks.pop(t.id, None)

    # -- CRUD -----------------------------------------------------------
    def create(self, type: str, title: str, collection: str, params: dict) -> Task:
        with self._lock:
            t = Task(id=uuid.uuid4().hex, type=type, title=title,
                     collection=collection, params=params, created_at=time.time())
            self._tasks[t.id] = t
            self._persist()
            return t

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list(self) -> list[Task]:
        items = list(self._tasks.values())
        items.sort(key=lambda t: (t.is_terminal(), -t.created_at))
        return items

    def next_queued(self) -> Optional[Task]:
        queued = [t for t in self._tasks.values() if t.status == "queued"]
        queued.sort(key=lambda t: t.created_at)
        return queued[0] if queued else None

    def update(self, task_id: str, throttle: bool = False, **fields) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return
            for k, v in fields.items():
                setattr(t, k, v)
            if throttle and not self._should_flush(t):
                return
            self._last_write[task_id] = (time.time(), t.done)
            self._persist()

    def _should_flush(self, t: Task) -> bool:
        ts, done = self._last_write.get(t.id, (0.0, 0))
        return (time.time() - ts) >= THROTTLE_SECONDS or (t.done - done) >= THROTTLE_COUNT

    def request_cancel(self, task_id: str) -> bool:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None or t.is_terminal():
                return False
            t.cancel_requested = True
            self._persist()
            return True

    def clear_finished(self) -> int:
        with self._lock:
            finished = [t.id for t in self._tasks.values() if t.is_terminal()]
            for tid in finished:
                self._tasks.pop(tid, None)
                names = self._children_dir / f"{tid}.names.json"
                if names.exists():
                    names.unlink()
            self._persist()
            return len(finished)

    # -- children -------------------------------------------------------
    def set_children_names(self, task_id: str, names: list[str]) -> None:
        with self._lock:
            self._children_dir.mkdir(parents=True, exist_ok=True)
            path = self._children_dir / f"{task_id}.names.json"
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(names), encoding="utf-8")
            os.replace(tmp, path)
            t = self._tasks.get(task_id)
            if t is not None:
                t.total = len(names)
                self._persist()

    def read_children(self, task_id: str, offset: int = 0, limit: int = 100) -> dict:
        t = self._tasks.get(task_id)
        path = self._children_dir / f"{task_id}.names.json"
        if t is None or not path.exists():
            return {"children": [], "total": t.total if t else 0}
        names = json.loads(path.read_text(encoding="utf-8"))
        fail = {f["name"]: f["status"] for f in t.failures}
        running = t.status == "running"
        out = []
        for i in range(offset, min(offset + limit, len(names))):
            name = names[i]
            out.append({"name": name,
                        "status": derive_child_status(i, t.done, running, fail.get(name))})
        return {"children": out, "total": len(names)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_task_store.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/tasks/store.py tests/test_task_store.py
git commit -m "feat(tasks): persistent TaskStore with paginated children + history cap"
```

---

### Task 3: Task runners (`tasks/runners.py`)

**Files:**
- Create: `src/opendomainmcp/tasks/runners.py`
- Modify: `src/opendomainmcp/ingest/pipeline.py` (add `list_files` public helper)
- Test: `tests/test_task_runners.py`

**Interfaces:**
- Consumes: `TaskStore` (Task 2); `Context` (`ctx.store`, `ctx.pipeline`, `ctx.settings`, `ctx.graph`); `ingest/checkpoint.py` `Checkpoint`/`extractor_signature`; `synthesis.synthesize_articles`; `extract.knowledge.get_extractor`; `models.Chunk`.
- Produces:
  - `Pipeline.list_files(path: str | Path) -> list[str]` — the file paths `ingest_path` would process, in processing order (reuses `_walk`); a single file returns `[path]`.
  - `run_ingest(ctx, store, task, is_cancelled) -> None`
  - `run_synthesize(ctx, store, task, is_cancelled) -> None`
  - `run_extract(ctx, store, task, is_cancelled) -> None`
  - `RUNNERS: dict[str, callable]` mapping type → runner.
  Each runner updates `store` progress (throttled) and sets `result`; raises on fatal error (worker turns it into `status="error"`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_task_runners.py
from opendomainmcp.config import Settings
from opendomainmcp.context import Context
from opendomainmcp.models import Chunk, KnowledgeUnit
from opendomainmcp.tasks.runners import run_ingest, run_synthesize, run_extract
from opendomainmcp.tasks.store import TaskStore


def _never_cancel():
    return False


def test_pipeline_list_files(pipeline, tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    (d / "a.py").write_text("def a():\n    return 1\n")
    (d / "b.md").write_text("Beta.\n")
    files = pipeline.list_files(str(d))
    assert sorted(Path(f).name for f in files) == ["a.py", "b.md"]


def test_run_ingest_enumerates_children_and_reports(store, pipeline, fake_graph, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def a():\n    return 1\n")
    (src / "b.md").write_text("Beta billing.\n")
    ctx = Context(settings=Settings(data_dir=tmp_path), store=store,
                  pipeline=pipeline, graph=fake_graph)
    ts = TaskStore(tmp_path)
    task = ts.create("ingest", "Ingest", "c", {"path": str(src), "sync": False})

    run_ingest(ctx, ts, task, _never_cancel)

    t = ts.get(task.id)
    assert t.total == 2 and t.done == 2
    assert t.result["files_indexed"] == 2
    page = ts.read_children(task.id, 0, 10)
    assert {c["name"] for c in page["children"]} == {str(src / "a.py"), str(src / "b.md")}


def test_run_extract_reextracts_without_reembedding(store, fake_graph, tmp_path, monkeypatch):
    # Seed one code chunk, then re-extract should update metadata via update_metadata.
    ku = KnowledgeUnit(summary="old", concepts=["x"], knowledge_type="Code")
    store.upsert([Chunk(text="def f(): pass", source="m.py", kind="code",
                        start_line=1, end_line=1, knowledge=ku)])
    ctx = Context(settings=Settings(data_dir=tmp_path), store=store,
                  pipeline=None, graph=fake_graph)

    class _Ext:
        def extract(self, text, kind, language=None):
            return KnowledgeUnit(summary="new summary", concepts=["y"],
                                 knowledge_type="Code")
    monkeypatch.setattr("opendomainmcp.tasks.runners.get_extractor", lambda s: _Ext())

    ts = TaskStore(tmp_path)
    task = ts.create("extract", "Re-extract", "c", {"source": "m.py"})
    run_extract(ctx, ts, task, _never_cancel)

    t = ts.get(task.id)
    assert t.done == 1 and t.total == 1
    item = next(i for i in store.get_items(limit=10) if i["metadata"]["source"] == "m.py")
    assert item["metadata"]["summary"] == "new summary"
```

(Add `from pathlib import Path` at the top of the test file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_task_runners.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'opendomainmcp.tasks.runners'`

- [ ] **Step 3a: Add `list_files` to the pipeline**

In `src/opendomainmcp/ingest/pipeline.py`, add this method to `Pipeline` (after `ingest_path`):

```python
    def list_files(self, path: str | Path) -> list[str]:
        """The file paths ingest_path would process, in processing order.
        Used by the Task Center to enumerate child entries up front."""
        p = Path(path)
        if p.is_dir():
            return [str(f) for f in self._walk(p)]
        if p.is_file():
            return [str(p)]
        return []
```

- [ ] **Step 3b: Write the runners**

```python
# src/opendomainmcp/tasks/runners.py
from __future__ import annotations

from ..extract.knowledge import get_extractor
from ..ingest.checkpoint import Checkpoint, extractor_signature
from ..models import Chunk
from ..synthesis import synthesize_articles

_TERMINAL_STAGES = {"store", "skip", "error"}  # per-file terminal ingest events


def run_ingest(ctx, store, task, is_cancelled) -> None:
    path = task.params["path"]
    sync = bool(task.params.get("sync", False))
    names = ctx.pipeline.list_files(path)
    store.set_children_names(task.id, names)

    cp = Checkpoint.new(ctx.settings.data_dir, task.id, path, sync,
                        extractor_signature(ctx.settings))
    cp.save()

    done = {"n": 0}
    failures: list[dict] = []

    def progress(event):
        stage = event.get("stage", "")
        if stage in _TERMINAL_STAGES:
            done["n"] += 1
            if stage in ("skip", "error"):
                failures.append({"name": event.get("path", ""),
                                 "status": "skipped" if stage == "skip" else "error"})
            store.update(task.id, throttle=True, done=done["n"], failures=list(failures))
        if is_cancelled():
            cp.request_cancel()

    report = ctx.pipeline.ingest_path(path, progress, sync, None, cp)
    store.update(task.id, done=len(names), failures=failures,
                 result=report.to_dict())


def run_synthesize(ctx, store, task, is_cancelled) -> None:
    limit = task.params.get("limit")
    dry_run = bool(task.params.get("dry_run", False))
    done = {"n": 0}
    failures: list[dict] = []
    seen_total = {"n": 0}

    def on_event(event):
        stage = event.get("stage", "")
        if stage == "start":
            seen_total["n"] = event.get("total", 0)
            store.set_children_names(
                task.id, [f"topic {i+1}" for i in range(seen_total["n"])])
        elif stage in ("stored", "rejected", "topic_error"):
            done["n"] += 1
            if stage != "stored":
                failures.append({"name": event.get("topic", ""),
                                 "status": "error" if stage == "topic_error" else "skipped"})
            store.update(task.id, throttle=True, done=done["n"], failures=list(failures))
        if is_cancelled():
            raise _Cancelled()

    try:
        report = synthesize_articles(ctx.store, ctx.settings, graph=ctx.graph,
                                     limit=limit, dry_run=dry_run, on_event=on_event)
    except _Cancelled:
        store.update(task.id, done=done["n"], failures=failures)
        return
    store.update(task.id, done=done["n"], failures=failures, result=report.to_dict())


def run_extract(ctx, store, task, is_cancelled) -> None:
    source = task.params["source"]
    ids = sorted(ctx.store.get_ids_for_source(source))
    store.set_children_names(task.id, ids)
    extractor = get_extractor(ctx.settings)
    done = 0
    failures: list[dict] = []
    for item_id in ids:
        if is_cancelled():
            store.update(task.id, done=done, failures=failures)
            return
        item = ctx.store.get_item(item_id)
        if item is None:
            done += 1
            continue
        meta = item.get("metadata", {})
        chunk = Chunk(text=item.get("text", ""), source=meta.get("source", source),
                      kind=meta.get("kind", "text"), language=meta.get("language"),
                      symbol=meta.get("symbol"), node_type=meta.get("node_type"),
                      start_line=meta.get("start_line"), end_line=meta.get("end_line"))
        try:
            chunk.knowledge = extractor.extract(chunk.text, chunk.kind, chunk.language)
            ctx.store.update_metadata(item_id, chunk.metadata())
        except Exception as exc:  # noqa: BLE001 - Fail Loud into the report
            failures.append({"name": item_id, "status": "error"})
        done += 1
        store.update(task.id, throttle=True, done=done, failures=list(failures))
    store.update(task.id, done=done, failures=failures,
                 result={"reextracted": done - len(failures), "errors": len(failures)})


class _Cancelled(Exception):
    pass


RUNNERS = {"ingest": run_ingest, "synthesize": run_synthesize, "extract": run_extract}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_task_runners.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/tasks/runners.py src/opendomainmcp/ingest/pipeline.py tests/test_task_runners.py
git commit -m "feat(tasks): ingest/synthesize/extract runners + Pipeline.list_files"
```

---

### Task 4: Serial worker (`tasks/worker.py`)

**Files:**
- Create: `src/opendomainmcp/tasks/worker.py`
- Test: `tests/test_task_worker.py`

**Interfaces:**
- Consumes: `TaskStore` (Task 2).
- Produces `TaskWorker(store, run_one)` where `run_one(task, is_cancelled) -> None` executes a task (raises on error). Methods:
  - `recover() -> None` — any `status="running"` task is re-queued (`status="queued"`, `cancel_requested=False`). Call before/at start.
  - `start() -> None` — spawn one daemon thread (idempotent: a second call is a no-op).
  - `wake() -> None` — signal the loop to re-check the queue immediately.
  - `stop() -> None` — stop the loop (for tests).
  - Loop: pop `next_queued()`; set `running`/`started_at`; call `run_one(task, is_cancelled)`; on return set `cancelled` if `cancel_requested` else `done`; on exception set `error`+`error` text; always set `finished_at`; idle-wait on an `Event` (1s timeout) when the queue is empty.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_task_worker.py
import time

from opendomainmcp.tasks.store import TaskStore
from opendomainmcp.tasks.worker import TaskWorker


def _wait_for(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


def test_worker_runs_tasks_serially_in_order(tmp_path):
    s = TaskStore(tmp_path)
    order = []

    def run_one(task, is_cancelled):
        order.append(task.title)

    w = TaskWorker(s, run_one)
    a = s.create("ingest", "A", "c", {})
    b = s.create("ingest", "B", "c", {})
    w.start()
    w.wake()
    assert _wait_for(lambda: s.get(b.id).status == "done")
    assert order == ["A", "B"]
    assert s.get(a.id).status == "done"
    w.stop()


def test_worker_marks_error_on_exception(tmp_path):
    s = TaskStore(tmp_path)

    def run_one(task, is_cancelled):
        raise RuntimeError("boom")

    w = TaskWorker(s, run_one)
    t = s.create("ingest", "X", "c", {})
    w.start(); w.wake()
    assert _wait_for(lambda: s.get(t.id).status == "error")
    assert "boom" in s.get(t.id).error
    w.stop()


def test_worker_cancellation_marks_cancelled(tmp_path):
    s = TaskStore(tmp_path)

    def run_one(task, is_cancelled):
        for _ in range(100):
            if is_cancelled():
                return
            time.sleep(0.01)

    w = TaskWorker(s, run_one)
    t = s.create("ingest", "X", "c", {})
    w.start(); w.wake()
    assert _wait_for(lambda: s.get(t.id).status == "running")
    s.request_cancel(t.id)
    assert _wait_for(lambda: s.get(t.id).status == "cancelled")
    w.stop()


def test_recover_requeues_stale_running(tmp_path):
    s = TaskStore(tmp_path)
    t = s.create("ingest", "X", "c", {})
    s.update(t.id, status="running")
    TaskWorker(s, lambda task, c: None).recover()
    assert s.get(t.id).status == "queued"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_task_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'opendomainmcp.tasks.worker'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/opendomainmcp/tasks/worker.py
from __future__ import annotations

import threading
import time


class TaskWorker:
    def __init__(self, store, run_one):
        self._store = store
        self._run_one = run_one
        self._wake = threading.Event()
        self._stop = False
        self._thread: threading.Thread | None = None

    def recover(self) -> None:
        for t in self._store.list():
            if t.status == "running":
                self._store.update(t.id, status="queued", cancel_requested=False)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.recover()
        self._stop = False
        self._thread = threading.Thread(target=self._loop, name="task-worker",
                                        daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop:
            task = self._store.next_queued()
            if task is None:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            self._run(task)

    def _run(self, task) -> None:
        self._store.update(task.id, status="running", started_at=time.time())

        def is_cancelled():
            t = self._store.get(task.id)
            return bool(t and t.cancel_requested)

        try:
            self._run_one(task, is_cancelled)
            status = "cancelled" if is_cancelled() else "done"
            self._store.update(task.id, status=status, finished_at=time.time())
        except Exception as exc:  # noqa: BLE001 - Fail Loud onto the task record
            self._store.update(task.id, status="error", error=repr(exc),
                               finished_at=time.time())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_task_worker.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/tasks/worker.py tests/test_task_worker.py
git commit -m "feat(tasks): serial TaskWorker with cancel + crash recovery"
```

---

### Task 5: API routes + app wiring (`api/task_routes.py`)

**Files:**
- Create: `src/opendomainmcp/api/task_routes.py`
- Modify: `src/opendomainmcp/api/app.py` (import + register routes; lazy worker bootstrap)
- Test: `tests/test_task_api.py`

**Interfaces:**
- Consumes: `TaskStore`, `TaskWorker`, `RUNNERS`, `get_ctx`, `build_context`/`context_factory`.
- Produces endpoints (registered via `register_task_routes(app)`):
  - `POST /api/tasks` body `{type: str, params: dict}` → 201-ish JSON `Task.to_dict()`; rejects unknown `type` with 400; lazily starts the worker; titles derived server-side.
  - `GET /api/tasks` → `{"tasks": [Task.to_dict()...]}`.
  - `GET /api/tasks/{id}/children?offset=&limit=` → `read_children(...)`; 404 if unknown id.
  - `DELETE /api/tasks/{id}` → `{"cancelled": bool}`; 404 if unknown id.
  - `POST /api/tasks/clear` → `{"cleared": int}`.
- App wiring: store + worker live on `app.state`; `run_one` resolves a Context per task's collection (reuse the per-collection context like `get_ctx`) and calls `RUNNERS[task.type]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_task_api.py
import time

import pytest
from fastapi.testclient import TestClient

from opendomainmcp.api.app import create_app
from opendomainmcp.config import Settings
from opendomainmcp.context import Context


@pytest.fixture
def client(store, pipeline, fake_graph, tmp_path):
    settings = Settings(data_dir=tmp_path)
    ctx = Context(settings=settings, store=store, pipeline=pipeline, graph=fake_graph)
    app = create_app(context=ctx, context_factory=lambda: ctx)
    return TestClient(app), ctx, tmp_path


def _wait(tc, job_id, statuses, tries=200):
    for _ in range(tries):
        tasks = tc.get("/api/tasks").json()["tasks"]
        t = next((x for x in tasks if x["id"] == job_id), None)
        if t and t["status"] in statuses:
            return t
        time.sleep(0.02)
    raise AssertionError(f"task {job_id} never reached {statuses}")


def test_create_ingest_task_runs_to_done(client, tmp_path):
    tc, _, _ = client
    src = tmp_path / "corpus"
    src.mkdir()
    (src / "a.py").write_text("def a():\n    return 1\n")
    (src / "b.md").write_text("Beta billing.\n")

    resp = tc.post("/api/tasks", json={"type": "ingest",
                                       "params": {"path": str(src)}})
    assert resp.status_code == 200
    job_id = resp.json()["id"]
    t = _wait(tc, job_id, {"done"})
    assert t["result"]["files_indexed"] == 2

    page = tc.get(f"/api/tasks/{job_id}/children").json()
    assert page["total"] == 2

    res = tc.post("/api/search", json={"query": "billing"}).json()
    assert isinstance(res, list)


def test_unknown_type_is_400(client):
    tc, _, _ = client
    assert tc.post("/api/tasks", json={"type": "nope", "params": {}}).status_code == 400


def test_children_unknown_id_404(client):
    tc, _, _ = client
    assert tc.get("/api/tasks/zzz/children").status_code == 404


def test_clear_finished(client, tmp_path):
    tc, _, _ = client
    src = tmp_path / "c2"
    src.mkdir()
    (src / "a.py").write_text("x = 1\n")
    job_id = tc.post("/api/tasks", json={"type": "ingest",
                                         "params": {"path": str(src)}}).json()["id"]
    _wait(tc, job_id, {"done"})
    cleared = tc.post("/api/tasks/clear").json()["cleared"]
    assert cleared >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_task_api.py -v`
Expected: FAIL (404 on `/api/tasks` — route not registered)

- [ ] **Step 3a: Write the routes module**

```python
# src/opendomainmcp/api/task_routes.py
from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from ..context import Context
from ..tasks.runners import RUNNERS
from ..tasks.store import TaskStore
from ..tasks.worker import TaskWorker
from .deps import get_ctx


class TaskCreate(BaseModel):
    type: str
    params: dict = {}


def _title(type: str, params: dict) -> str:
    if type == "ingest":
        return f"Ingest {params.get('path', '')}"
    if type == "synthesize":
        return "Synthesize articles"
    if type == "extract":
        return f"Re-extract {params.get('source', '')}"
    return type


def register_task_routes(app, resolve_ctx) -> None:
    """resolve_ctx(collection) -> Context. Store + worker live on app.state."""

    def _store(ctx: Context) -> TaskStore:
        if getattr(app.state, "task_store", None) is None:
            app.state.task_store = TaskStore(ctx.settings.data_dir)
        return app.state.task_store

    def _worker(ctx: Context) -> TaskWorker:
        store = _store(ctx)
        if getattr(app.state, "task_worker", None) is None:
            def run_one(task, is_cancelled):
                tctx = resolve_ctx(task.collection)
                RUNNERS[task.type](tctx, store, task, is_cancelled)
            app.state.task_worker = TaskWorker(store, run_one)
            app.state.task_worker.start()
        return app.state.task_worker

    @app.post("/api/tasks")
    def create_task(body: TaskCreate, ctx: Context = Depends(get_ctx)):
        if body.type not in RUNNERS:
            raise HTTPException(status_code=400, detail=f"unknown task type {body.type!r}")
        store = _store(ctx)
        collection = ctx.store.stats().get("collection", "")
        task = store.create(body.type, _title(body.type, body.params),
                            collection, body.params)
        _worker(ctx).wake()
        return task.to_dict()

    @app.get("/api/tasks")
    def list_tasks(ctx: Context = Depends(get_ctx)):
        return {"tasks": [t.to_dict() for t in _store(ctx).list()]}

    @app.get("/api/tasks/{task_id}/children")
    def task_children(task_id: str, offset: int = 0, limit: int = 100,
                      ctx: Context = Depends(get_ctx)):
        store = _store(ctx)
        if store.get(task_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown task {task_id}")
        return store.read_children(task_id, offset, limit)

    @app.delete("/api/tasks/{task_id}")
    def cancel_task(task_id: str, ctx: Context = Depends(get_ctx)):
        store = _store(ctx)
        if store.get(task_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown task {task_id}")
        return {"cancelled": store.request_cancel(task_id)}

    @app.post("/api/tasks/clear")
    def clear_tasks(ctx: Context = Depends(get_ctx)):
        return {"cleared": _store(ctx).clear_finished()}
```

- [ ] **Step 3b: Wire into `create_app`**

In `src/opendomainmcp/api/app.py`, add the import near the other `from . import ...`:

```python
from .task_routes import register_task_routes
```

Then, inside `create_app`, after the existing routes are defined (just before `return app` / static mount), add:

```python
    def _resolve_ctx(collection):
        # Reuse the same per-collection context resolution as get_ctx: a pinned
        # context (tests) ignores the collection; otherwise build per collection.
        if app.state.context is not None:
            return app.state.context
        cached = app.state.contexts.get(collection)
        if cached is None:
            cached = app.state.context_factory()
            app.state.contexts[collection] = cached
        return cached

    register_task_routes(app, _resolve_ctx)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_task_api.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full backend suite (no regressions)**

Run: `pytest -q`
Expected: all pass (existing + new).

- [ ] **Step 6: Commit**

```bash
git add src/opendomainmcp/api/task_routes.py src/opendomainmcp/api/app.py tests/test_task_api.py
git commit -m "feat(tasks): /api/tasks endpoints + lazy worker bootstrap"
```

---

### Task 6: Frontend API client (`web/src/api.ts`)

**Files:**
- Modify: `web/src/api.ts`
- Test: typecheck only (`npx tsc --noEmit` from `web/`)

**Interfaces:**
- Produces:
  - `export interface TaskItem { id; type: "ingest"|"synthesize"|"extract"; title; collection; status: "queued"|"running"|"done"|"error"|"cancelled"; total: number; done: number; failures: {name:string;status:string}[]; error: string|null; result: Record<string,unknown>|null; }`
  - `export interface TaskChild { name: string; status: string }`
  - `api.createTask(type, params) -> Promise<TaskItem>`
  - `api.listTasks() -> Promise<{tasks: TaskItem[]}>`
  - `api.taskChildren(id, offset, limit) -> Promise<{children: TaskChild[]; total: number}>`
  - `api.cancelTask(id) -> Promise<{cancelled: boolean}>`
  - `api.clearTasks() -> Promise<{cleared: number}>`

- [ ] **Step 1: Add the types** (near the other `export interface` blocks)

```typescript
export interface TaskItem {
  id: string;
  type: "ingest" | "synthesize" | "extract";
  title: string;
  collection: string;
  status: "queued" | "running" | "done" | "error" | "cancelled";
  total: number;
  done: number;
  failures: { name: string; status: string }[];
  error: string | null;
  result: Record<string, unknown> | null;
}

export interface TaskChild {
  name: string;
  status: string;
}
```

- [ ] **Step 2: Add the API methods** (inside the `api` object)

```typescript
  // -- task center --------------------------------------------------------
  createTask: (type: string, params: Record<string, unknown>) =>
    fetch(withCollection("/api/tasks"), {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ type, params }),
    }).then(json<TaskItem>),

  listTasks: () =>
    fetch(withCollection("/api/tasks"), { headers: headers() }).then(
      json<{ tasks: TaskItem[] }>
    ),

  taskChildren: (id: string, offset = 0, limit = 100) =>
    fetch(
      withCollection(
        `/api/tasks/${encodeURIComponent(id)}/children?offset=${offset}&limit=${limit}`
      ),
      { headers: headers() }
    ).then(json<{ children: TaskChild[]; total: number }>),

  cancelTask: (id: string) =>
    fetch(withCollection(`/api/tasks/${encodeURIComponent(id)}`), {
      method: "DELETE",
      headers: headers(),
    }).then(json<{ cancelled: boolean }>),

  clearTasks: () =>
    fetch(withCollection("/api/tasks/clear"), {
      method: "POST",
      headers: headers(),
    }).then(json<{ cleared: number }>),
```

- [ ] **Step 3: Typecheck**

Run (from `web/`): `npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add web/src/api.ts
git commit -m "feat(web): task-center API client methods + types"
```

---

### Task 7: Task Center UI (`web/src/components/TaskCenter.tsx` + `App.tsx`)

**Files:**
- Create: `web/src/components/TaskCenter.tsx`
- Modify: `web/src/App.tsx` (render a global top-right bar containing `<TaskCenter />`)
- Modify: `web/src/components/icons.tsx` (add `IconTasks` if none fits — reuse an existing icon otherwise)
- Test: typecheck + build

**Interfaces:**
- Consumes: `api.listTasks`, `api.taskChildren`, `api.cancelTask`, `api.clearTasks`, `TaskItem`, `TaskChild` (Task 6).
- Produces: `export default function TaskCenter()` — a button with an active-count badge that opens a right-side slide-over panel listing tasks (progress bar, status, cancel, expandable lazily-loaded children) and a "Clear finished" action; polls `listTasks` every 1500ms while open or while any task is active.

- [ ] **Step 1: Write the component**

```tsx
// web/src/components/TaskCenter.tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { api, TaskChild, TaskItem } from "../api";
import { Button, IconButton } from "./ui";
import { IconClose } from "./icons";

const ACTIVE = new Set(["queued", "running"]);

const STATUS_TONE: Record<string, string> = {
  queued: "text-slate-400",
  running: "text-emerald-500",
  done: "text-brand-500",
  error: "text-red-500",
  cancelled: "text-amber-500",
};

export default function TaskCenter() {
  const [open, setOpen] = useState(false);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    try {
      setTasks((await api.listTasks()).tasks);
    } catch {
      /* transient */
    }
  }, []);

  const activeCount = tasks.filter((t) => ACTIVE.has(t.status)).length;

  // Poll while the panel is open or any task is active.
  useEffect(() => {
    refresh();
    const shouldPoll = open || activeCount > 0;
    if (shouldPoll && !timer.current) {
      timer.current = setInterval(refresh, 1500);
    } else if (!shouldPoll && timer.current) {
      clearInterval(timer.current);
      timer.current = null;
    }
    return () => {
      if (timer.current) {
        clearInterval(timer.current);
        timer.current = null;
      }
    };
  }, [open, activeCount, refresh]);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="relative inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm transition hover:border-slate-300 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
        title="Task Center"
      >
        Tasks
        {activeCount > 0 && (
          <span className="inline-flex h-5 min-w-[20px] items-center justify-center rounded-full bg-brand-600 px-1.5 text-xs font-semibold text-white">
            {activeCount}
          </span>
        )}
      </button>

      {open && (
        <div className="fixed inset-0 z-50">
          <div
            className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm animate-fade-in"
            onClick={() => setOpen(false)}
          />
          <div className="absolute inset-y-0 right-0 flex w-[26rem] max-w-[90vw] flex-col border-l border-slate-200 bg-white shadow-xl animate-fade-in dark:border-slate-800 dark:bg-slate-900">
            <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800">
              <span className="font-semibold text-slate-900 dark:text-white">
                Task Center
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="secondary"
                  onClick={() => api.clearTasks().then(refresh)}
                >
                  Clear finished
                </Button>
                <IconButton onClick={() => setOpen(false)} aria-label="Close">
                  <IconClose />
                </IconButton>
              </div>
            </div>
            <div className="scroll-thin flex-1 space-y-3 overflow-auto p-4">
              {tasks.length === 0 && (
                <p className="text-sm text-slate-400">No tasks yet.</p>
              )}
              {tasks.map((t) => (
                <TaskCard key={t.id} task={t} onChanged={refresh} />
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function TaskCard({ task, onChanged }: { task: TaskItem; onChanged: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<TaskChild[] | null>(null);
  const pct = task.total > 0 ? Math.round((task.done / task.total) * 100) : 0;
  const active = task.status === "queued" || task.status === "running";

  async function toggle() {
    const next = !expanded;
    setExpanded(next);
    if (next && children === null) {
      setChildren((await api.taskChildren(task.id, 0, 100)).children);
    }
  }

  return (
    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-800">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
            {task.title}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-xs">
            <span className={STATUS_TONE[task.status] ?? "text-slate-400"}>
              {task.status}
            </span>
            <span className="text-slate-400">
              {task.done}/{task.total}
            </span>
            <span className="rounded bg-slate-100 px-1.5 text-[10px] text-slate-500 dark:bg-slate-800">
              {task.collection}
            </span>
          </div>
        </div>
        {active && (
          <Button variant="secondary" onClick={() => api.cancelTask(task.id).then(onChanged)}>
            Cancel
          </Button>
        )}
      </div>

      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div
          className="h-full bg-brand-500 transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>

      {task.error && (
        <p className="mt-2 break-words text-xs text-red-500">{task.error}</p>
      )}

      {task.total > 0 && (
        <button
          onClick={toggle}
          className="mt-2 text-xs text-slate-400 hover:text-slate-600 dark:hover:text-slate-200"
        >
          {expanded ? "Hide" : "Show"} items
        </button>
      )}
      {expanded && children && (
        <ul className="mt-1.5 max-h-40 space-y-0.5 overflow-auto font-mono text-[11px]">
          {children.map((c) => (
            <li key={c.name} className="flex justify-between gap-2">
              <span className="truncate text-slate-500">{c.name}</span>
              <span className={STATUS_TONE[c.status] ?? "text-slate-400"}>{c.status}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Render it in a global top-right bar**

In `web/src/App.tsx`, import the component near the top:

```tsx
import TaskCenter from "./components/TaskCenter";
```

Then inside the `<main className="flex-1">`, wrap the content with a top bar. Replace:

```tsx
      <main className="flex-1">
        <div className="mx-auto w-full max-w-5xl p-5 sm:p-8">
          <div key={location.pathname} className="animate-fade-in-up">
            <Outlet />
          </div>
        </div>
      </main>
```

with:

```tsx
      <main className="flex-1">
        <div className="sticky top-0 z-20 flex justify-end border-b border-slate-200 bg-white/80 px-5 py-2.5 backdrop-blur dark:border-slate-800 dark:bg-slate-900/80">
          <TaskCenter />
        </div>
        <div className="mx-auto w-full max-w-5xl p-5 sm:p-8">
          <div key={location.pathname} className="animate-fade-in-up">
            <Outlet />
          </div>
        </div>
      </main>
```

- [ ] **Step 3: Typecheck + build**

Run (from `web/`): `npx tsc --noEmit && npm run build`
Expected: exit 0; build writes assets.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/TaskCenter.tsx web/src/App.tsx
git commit -m "feat(web): Task Center button + slide-over panel with polling"
```

---

### Task 8: Wire triggers (Ingest / Articles / Browse)

**Files:**
- Modify: `web/src/pages/Ingest.tsx` ("Run in background" → create an ingest task)
- Modify: `web/src/pages/Articles.tsx` ("Synthesize now" → create a synthesize task)
- Modify: `web/src/pages/Dashboard.tsx` (add a "Re-extract" action to each `SourceRow` → create an extract task)
- Test: typecheck + build

**Interfaces:**
- Consumes: `api.createTask` (Task 6). After creating a task, show a toast pointing to the Task Center; do not block the page.

- [ ] **Step 1: Ingest — route "Run in background" to a task**

In `web/src/pages/Ingest.tsx`, replace the body of `runBackground` so it creates a task instead of calling the old async-ingest endpoint:

```tsx
  async function runBackground(target: string) {
    try {
      await api.createTask("ingest", { path: target, sync });
      toast.show("Queued in Task Center (top-right) — you can leave this page", "green");
    } catch (e) {
      toast.show(String(e), "red");
    }
  }
```

Add `const toast = useToast();` to the component if not already present, and import `useToast` from `../components/ui`. Remove the now-unused `job`/`pollRef`/`cancelJob` state and the old polling code, and drop the "Cancel" button that referenced `job` (cancellation now lives in the Task Center). Keep the inline live-SSE `run()` path and its `Ingest` button unchanged.

- [ ] **Step 2: Articles — route "Synthesize now" to a task**

In `web/src/pages/Articles.tsx`, replace the body of `runSynthesize` with:

```tsx
  function runSynthesize() {
    api
      .createTask("synthesize", {})
      .then(() =>
        toast.show("Synthesis queued in Task Center (top-right)", "green"),
      )
      .catch((e) => toast.show(String(e), "red"));
  }
```

Remove the now-unused `running`/`log`/`report` synthesis state, the `synthesizeStream` import, the synthesis live-log card, and the synthesis report card (cancellation + progress now live in the Task Center). Keep the article list/detail and the "Synthesize now" button (it now enqueues).

- [ ] **Step 3: Dashboard — add a "Re-extract" action to each source row**

Sources are listed in `web/src/pages/Dashboard.tsx` by the `SourceRow` component (it receives a `source: SourceInfo` prop and renders a delete `IconButton`). Two changes:

1. Thread a toast + handler down to `SourceRow`. In `SourceRow`, add a `Re-extract` button next to the delete control, using `source.source` as the source string:

```tsx
<Button
  variant="secondary"
  onClick={() =>
    api
      .createTask("extract", { source: source.source })
      .then(() =>
        toast.show("Re-extract queued (refreshes knowledge, not vectors)", "green"),
      )
      .catch((e: unknown) => toast.show(String(e), "red"))
  }
>
  Re-extract
</Button>
```

2. `SourceRow` needs `api`, `Button`, and a `toast`. Import `Button` and `api` if not already imported in the file, and pass `toast` from the parent `Dashboard` component into `SourceRow` as a prop (the parent already calls `const toast = useToast()` at line ~59). Update `SourceRow`'s prop type to include `toast: ReturnType<typeof useToast>`.

- [ ] **Step 4: Typecheck + build**

Run (from `web/`): `npx tsc --noEmit && npm run build`
Expected: exit 0.

- [ ] **Step 5: Live smoke (manual)**

Start the app per the project's run skill, create an ingest task on a small directory, confirm it appears in the Task Center, runs to `done`, children expand, and "Clear finished" removes it. Confirm a refresh mid-run keeps the task visible.

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/Ingest.tsx web/src/pages/Articles.tsx web/src/pages/Browse.tsx
git commit -m "feat(web): route ingest/synthesize/re-extract through the Task Center"
```

---

## Self-Review Notes

- **Spec coverage:** §3 scalability → Task 1 (`derive_child_status`) + Task 2 (`set_children_names`/`read_children` + throttled `update` + 100k cap) + Task 3 (names written once, `done`+`failures` progress). §4 data model → Task 1. §5.1 store → Task 2. §5.2 worker → Task 4. §5.3 runners → Task 3. §5.4 API → Task 5. §5.5 (fold async ingest) → Task 8 Step 1 reroutes "Run in background"; the old `/api/ingest/async` endpoints remain but are no longer used by the UI (left in place to avoid churn; SSE flows kept). §6 frontend → Tasks 6–8. §7 Fail Loud → Task 3 per-child failures + Task 4 exception→error. §8 testing → each task's tests.
- **Cancellation:** ingest runner sets `cp.request_cancel()` when `is_cancelled()`, and the pipeline breaks between files (existing behavior from the prior PR); synthesize raises `_Cancelled`; extract returns early. Worker marks `cancelled` when `cancel_requested` is set after the runner returns.
- **Throttle:** `store.update(throttle=True)` coalesces frequent per-child updates; terminal `update(...)` calls (no throttle) always flush final counts/result.
- **Type consistency:** `TaskItem`/`TaskChild` (TS) mirror `Task.to_dict()`/`read_children` (Py). `RUNNERS` keys (`ingest|synthesize|extract`) match `_title` and the `POST /api/tasks` validation.
- **Worker in tests:** lazily created only on first `POST /api/tasks`, so existing API tests that never hit `/api/tasks` spawn no worker thread.
