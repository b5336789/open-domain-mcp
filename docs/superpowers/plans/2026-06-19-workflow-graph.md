# Workflow Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 從 Workflow/Runbook 類型知識抽取有序步驟與前置條件，持久化到 MariaDB，並提供結構化、已排序的 `get_workflow_steps` / `list_workflows` 查詢（API + MCP）。

**Architecture:** 萃取在現有單次 LLM 呼叫中疊加一個條件式 `workflow` 物件；`graph/workflow.py` 把它轉成步驟；步驟與前置條件存進 MariaDB `GraphStore` 兩張新表（沿用 collection 隔離與 chunk 生命週期同步）；查詢層純讀，跨 chunk 依 `chunk_index, step_order` 合併排序。

**Tech Stack:** Python 3.11+、PyMySQL、MariaDB、FastAPI、pytest。建在已完成的 Entity Graph 基礎（PR #12）上。

## Global Constraints

- 萃取仍是**每 chunk 單次 LLM 呼叫**；`workflow` 是條件式疊加欄位，其他類型回 `{}`。
- 既有自由文字/entities/typed_relations 欄位**不動**；新增全部帶預設、向後相容。
- `Chunk.chunk_index` 是純位置 metadata，**不可**進 `content_hash`/`id`。
- 工作流資料**依 collection 隔離**（沿用 Entity Graph）；隨 chunk prune（`delete_for_chunks`/`delete_collection`/API item CRUD）同步刪除。
- 同 collection 內**同名工作流跨 chunk 合併**；工作流名稱以 `normalize_name`（小寫/去空白）為 `workflow_key`，原文存 `workflow_name`（display，first-seen）。
- 查詢層**純讀、無 LLM、無寫入**；步驟依 `(chunk_index, step_order)` 排序、prerequisites 去重。
- 測試基線：`source .venv/bin/activate && ODM_EXTRACT_KNOWLEDGE=true python -m pytest -q` → 起點 **166 passed, 2 skipped**（本機 `.env` 關閉 extraction，故須加 `ODM_EXTRACT_KNOWLEDGE=true` 取得乾淨基線）。MariaDB 整合測試標 `@pytest.mark.integration`，無 `GRAPH_DB_HOST` 時 skip。

---

## File Structure

| 檔案 | 職責 | 動作 |
|------|------|------|
| `src/opendomainmcp/models.py` | `KnowledgeUnit.workflow`、`Chunk.chunk_index` | Modify |
| `src/opendomainmcp/graph/models.py` | `WorkflowStep` dataclass | Modify |
| `src/opendomainmcp/extract/knowledge.py` | `_SYSTEM` workflow key + `_parse_workflow` | Modify |
| `src/opendomainmcp/graph/workflow.py` | `build_workflow` 純邏輯 | Create |
| `src/opendomainmcp/graph/store.py` | 兩表 + `upsert_workflow`/`get_workflow`/`list_workflows` + delete 同步 | Modify |
| `src/opendomainmcp/ingest/pipeline.py` | 賦值 `chunk_index` + `_write_workflow` | Modify |
| `src/opendomainmcp/api/app.py` | `/api/graph/workflow/{name}`、`/api/graph/workflows` | Modify |
| `src/opendomainmcp/server.py` | `get_workflow_steps`/`list_workflows` MCP 工具 | Modify |
| `tests/conftest.py` | `FakeGraphStore` workflow 鏡像 | Modify |

---

## Task 1: 資料模型與萃取（workflow 欄位 + chunk_index + 解析）

**Files:**
- Modify: `src/opendomainmcp/models.py` (`KnowledgeUnit`, `Chunk`)
- Modify: `src/opendomainmcp/graph/models.py` (`WorkflowStep`)
- Modify: `src/opendomainmcp/extract/knowledge.py` (`_SYSTEM`, `_parse_workflow`, `_parse`)
- Test: `tests/test_extract_workflow.py`, `tests/test_workflow_models.py`

**Interfaces:**
- Produces:
  - `KnowledgeUnit.workflow: dict`（`{}` 預設；形如 `{"name": str, "prerequisites": [str], "steps": [{"order": int, "text": str, "precondition": str}]}`）
  - `Chunk.chunk_index: Optional[int] = None`（不進 `content_hash`）
  - `graph.models.WorkflowStep(step_order: int, text: str, precondition: str = "")`
  - `extract.knowledge._parse_workflow(value) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_workflow_models.py
from opendomainmcp.models import KnowledgeUnit, Chunk
from opendomainmcp.graph.models import WorkflowStep


def test_knowledge_unit_workflow_defaults_empty():
    k = KnowledgeUnit()
    assert k.workflow == {}
    assert k.is_empty() is True  # workflow must not change emptiness semantics


def test_chunk_index_defaults_none_and_not_in_hash():
    a = Chunk(text="x", source="s", start_line=1, end_line=2)
    b = Chunk(text="x", source="s", start_line=1, end_line=2)
    a.chunk_index = 0
    b.chunk_index = 9
    assert a.chunk_index == 0
    assert a.id == b.id  # chunk_index must NOT affect content hash / id


def test_workflow_step_dataclass():
    s = WorkflowStep(step_order=1, text="do it")
    assert s.precondition == ""
```

```python
# tests/test_extract_workflow.py
from opendomainmcp.extract.knowledge import _parse_workflow


def test_parse_workflow_happy_path():
    out = _parse_workflow({
        "name": "Deploy to prod",
        "prerequisites": ["deploy permission", "CI green"],
        "steps": [{"order": 1, "text": "run tests"},
                  {"order": 2, "text": "deploy", "precondition": "tests passed"}],
    })
    assert out["name"] == "Deploy to prod"
    assert out["prerequisites"] == ["deploy permission", "CI green"]
    assert out["steps"] == [
        {"order": 1, "text": "run tests", "precondition": ""},
        {"order": 2, "text": "deploy", "precondition": "tests passed"}]


def test_parse_workflow_requires_name_and_steps():
    assert _parse_workflow({"steps": [{"order": 1, "text": "x"}]}) == {}   # no name
    assert _parse_workflow({"name": "X", "steps": []}) == {}               # no steps
    assert _parse_workflow("junk") == {}


def test_parse_workflow_drops_empty_steps_and_defaults_order():
    out = _parse_workflow({"name": "W", "steps": [
        {"text": "first"}, {"text": ""}, {"order": "bad", "text": "third"}]})
    # empty-text step dropped; missing/invalid order falls back to enumeration index
    assert [s["order"] for s in out["steps"]] == [1, 3]
    assert [s["text"] for s in out["steps"]] == ["first", "third"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && ODM_EXTRACT_KNOWLEDGE=true python -m pytest tests/test_workflow_models.py tests/test_extract_workflow.py -v`
Expected: FAIL — `WorkflowStep` / `_parse_workflow` not defined, `workflow`/`chunk_index` attributes missing.

- [ ] **Step 3: Add model fields**

In `models.py` `KnowledgeUnit`, after `typed_relations`:

```python
    # Ordered procedure extracted from Workflow/Runbook chunks (see graph.workflow).
    # {"name", "prerequisites": [str], "steps": [{"order", "text", "precondition"}]}.
    workflow: dict = field(default_factory=dict)
```

In `models.py` `Chunk`, after `end_line: Optional[int] = None` (and before `knowledge`):

```python
    # Position of this chunk within its source document (set by the pipeline).
    # Used to order workflow steps across chunks. NOT part of content_hash/id.
    chunk_index: Optional[int] = None
```

> Do NOT change `content_hash` — it hashes `source`, `start_line`, `end_line`, `text` only, so `chunk_index` is excluded by construction. Leave `is_empty()` unchanged.

In `graph/models.py`, after `Edge`:

```python
@dataclass
class WorkflowStep:
    step_order: int
    text: str
    precondition: str = ""
```

- [ ] **Step 4: Add `_parse_workflow` and wire into `_parse`**

In `extract/knowledge.py`, after `_parse_relations` (near the other `_parse_*` helpers):

```python
def _parse_workflow(value) -> dict:
    """Normalize the optional ``workflow`` object. Requires a name and at least
    one non-empty step, else returns {} (the snippet is not a real procedure)."""
    if not isinstance(value, dict):
        return {}
    name = str(value.get("name", "")).strip()
    raw_steps = value.get("steps", [])
    steps = []
    if isinstance(raw_steps, list):
        for i, s in enumerate(raw_steps, start=1):
            if not isinstance(s, dict):
                continue
            text = str(s.get("text", "")).strip()
            if not text:
                continue
            try:
                order = int(s.get("order", i))
            except (TypeError, ValueError):
                order = i
            steps.append({"order": order, "text": text,
                          "precondition": str(s.get("precondition", "")).strip()})
    if not name or not steps:
        return {}
    return {"name": name, "prerequisites": _str_list(value.get("prerequisites", [])),
            "steps": steps}
```

In `_SYSTEM`, change the final `typed_relations` line's trailing `.\n"` so it ends with `,\n"`, then insert the workflow key before `"Do not include any prose outside the JSON object."`:

```python
    '  "workflow": if this snippet is a runbook, workflow, or step-by-step '
    'procedure, an object {"name": a short title, "prerequisites": [conditions '
    'that must hold before starting], "steps": [{"order": 1-based integer, '
    '"text": what to do, "precondition": an optional condition for this step}]}; '
    'otherwise an empty object {}.\n'
    "Do not include any prose outside the JSON object."
```

In `_parse`'s `KnowledgeUnit(...)` construction, after `typed_relations=...`:

```python
        typed_relations=_parse_relations(data.get("typed_relations", [])),
        workflow=_parse_workflow(data.get("workflow", {})),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `source .venv/bin/activate && ODM_EXTRACT_KNOWLEDGE=true python -m pytest tests/test_workflow_models.py tests/test_extract_workflow.py -q && ODM_EXTRACT_KNOWLEDGE=true python -m pytest -q`
Expected: new tests PASS; full suite still green (166 + new, 2 skipped).

- [ ] **Step 6: Commit**

```bash
git add src/opendomainmcp/models.py src/opendomainmcp/graph/models.py src/opendomainmcp/extract/knowledge.py tests/test_workflow_models.py tests/test_extract_workflow.py
git commit -m "feat(workflow): KnowledgeUnit.workflow, Chunk.chunk_index, WorkflowStep, extraction parse"
```

---

## Task 2: 純邏輯建構 `build_workflow`

**Files:**
- Create: `src/opendomainmcp/graph/workflow.py`
- Test: `tests/test_build_workflow.py`

**Interfaces:**
- Consumes: `KnowledgeUnit`（Task 1）、`WorkflowStep`（Task 1）。
- Produces: `graph.workflow.build_workflow(knowledge: KnowledgeUnit) -> tuple[list[WorkflowStep], list[str], str]` → `(steps_sorted_by_order, prerequisites, workflow_name)`；無 workflow 回 `([], [], "")`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_workflow.py
from opendomainmcp.models import KnowledgeUnit
from opendomainmcp.graph.workflow import build_workflow


def test_build_workflow_maps_and_sorts_steps():
    k = KnowledgeUnit(workflow={
        "name": "Deploy", "prerequisites": ["perm"],
        "steps": [{"order": 2, "text": "deploy", "precondition": "tests ok"},
                  {"order": 1, "text": "test", "precondition": ""}]})
    steps, prereqs, name = build_workflow(k)
    assert name == "Deploy"
    assert prereqs == ["perm"]
    assert [(s.step_order, s.text) for s in steps] == [(1, "test"), (2, "deploy")]
    assert steps[1].precondition == "tests ok"


def test_build_workflow_empty_when_no_workflow():
    assert build_workflow(KnowledgeUnit()) == ([], [], "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && ODM_EXTRACT_KNOWLEDGE=true python -m pytest tests/test_build_workflow.py -v`
Expected: FAIL — `ImportError` (module not created).

- [ ] **Step 3: Implement `graph/workflow.py`**

```python
# src/opendomainmcp/graph/workflow.py
"""Turn a chunk's extracted ``KnowledgeUnit.workflow`` into typed steps.

Pure logic: the cross-chunk merge and final ordering happen at query time in
the store (ordered by chunk_index, step_order). Here we only map and locally
sort one chunk's steps.
"""

from __future__ import annotations

from ..models import KnowledgeUnit
from .models import WorkflowStep


def build_workflow(knowledge: KnowledgeUnit) -> tuple[list[WorkflowStep], list[str], str]:
    wf = knowledge.workflow or {}
    name = str(wf.get("name", "")).strip()
    raw_steps = wf.get("steps", []) or []
    if not name or not raw_steps:
        return [], [], ""
    steps = [WorkflowStep(step_order=int(s["order"]), text=s["text"],
                          precondition=s.get("precondition", ""))
             for s in raw_steps]
    steps.sort(key=lambda s: s.step_order)
    prerequisites = list(wf.get("prerequisites", []) or [])
    return steps, prerequisites, name
```

- [ ] **Step 4: Run test to verify it passes**

Run: `source .venv/bin/activate && ODM_EXTRACT_KNOWLEDGE=true python -m pytest tests/test_build_workflow.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/graph/workflow.py tests/test_build_workflow.py
git commit -m "feat(workflow): build_workflow maps KnowledgeUnit.workflow to typed steps"
```

---

## Task 3: GraphStore 工作流表與查詢（Maria + Protocol + Null + Fake）

**Files:**
- Modify: `src/opendomainmcp/graph/store.py` (`_SCHEMA`, `GraphStoreProtocol`, `MariaGraphStore`, `NullGraphStore`)
- Modify: `tests/conftest.py` (`FakeGraphStore`)
- Test: `tests/test_workflow_store_fake.py`, `tests/test_workflow_store_mariadb.py`

**Interfaces:**
- Consumes: `WorkflowStep`（Task 1）、`normalize_name`（既有 `graph/normalize.py`）。
- Produces (added to `GraphStoreProtocol`, `MariaGraphStore`, `NullGraphStore`, `FakeGraphStore`):
  - `upsert_workflow(workflow_name: str, chunk_id: str, chunk_index: int, steps: list[WorkflowStep], prerequisites: list[str]) -> None`
  - `get_workflow(name: str) -> Optional[dict]` → `{"workflow_name": str, "prerequisites": [str], "steps": [{"order": int, "text": str, "precondition": str, "chunk_id": str}]}`（依 `(chunk_index, step_order)` 排序、prereq 去重）或 `None`
  - `list_workflows(q: Optional[str] = None, limit: int = 50) -> list[dict]` → `[{"name": str}]`
  - `delete_for_chunks` / `delete_collection` 一併清兩張 workflow 表

- [ ] **Step 1: Write the failing fake-store test**

```python
# tests/test_workflow_store_fake.py
from opendomainmcp.graph.models import WorkflowStep


def _steps(*pairs):
    return [WorkflowStep(step_order=o, text=t) for o, t in pairs]


def test_get_workflow_merges_chunks_in_order(fake_graph):
    # two chunks of the same workflow; chunk_index drives cross-chunk ordering
    fake_graph.upsert_workflow("Deploy", "c1", 0, _steps((1, "test"), (2, "tag")), ["perm"])
    fake_graph.upsert_workflow("deploy", "c2", 1, _steps((1, "ship"), (2, "watch")), ["perm", "ci"])
    wf = fake_graph.get_workflow("DEPLOY")  # lookup is case-insensitive (normalized key)
    assert [s["text"] for s in wf["steps"]] == ["test", "tag", "ship", "watch"]
    assert sorted(wf["prerequisites"]) == ["ci", "perm"]  # deduped across chunks


def test_get_workflow_missing_returns_none(fake_graph):
    assert fake_graph.get_workflow("nope") is None


def test_delete_for_chunks_prunes_workflow(fake_graph):
    fake_graph.upsert_workflow("Deploy", "c1", 0, _steps((1, "test")), ["perm"])
    fake_graph.delete_for_chunks(["c1"])
    assert fake_graph.get_workflow("Deploy") is None


def test_list_workflows(fake_graph):
    fake_graph.upsert_workflow("Deploy", "c1", 0, _steps((1, "x")), [])
    fake_graph.upsert_workflow("Rollback", "c2", 0, _steps((1, "y")), [])
    names = {w["name"] for w in fake_graph.list_workflows()}
    assert names == {"Deploy", "Rollback"}
    assert [w["name"] for w in fake_graph.list_workflows(q="roll")] == ["Rollback"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && ODM_EXTRACT_KNOWLEDGE=true python -m pytest tests/test_workflow_store_fake.py -v`
Expected: FAIL — `FakeGraphStore` has no `upsert_workflow`.

- [ ] **Step 3: Add the two tables to `_SCHEMA` in `store.py`**

Append two entries to the `_SCHEMA` tuple:

```python
    """
    CREATE TABLE IF NOT EXISTS workflow_steps (
        collection    VARCHAR(255) NOT NULL,
        workflow_key  VARCHAR(255) NOT NULL,
        workflow_name VARCHAR(512) NOT NULL,
        chunk_id      VARCHAR(128) NOT NULL,
        chunk_index   INT          NOT NULL DEFAULT 0,
        step_order    INT          NOT NULL,
        text          TEXT         NOT NULL,
        precondition  TEXT,
        PRIMARY KEY (collection, workflow_key, chunk_id, step_order)
    ) CHARACTER SET utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_prereqs (
        collection   VARCHAR(255) NOT NULL,
        workflow_key VARCHAR(255) NOT NULL,
        chunk_id     VARCHAR(128) NOT NULL,
        prerequisite VARCHAR(512) NOT NULL,
        PRIMARY KEY (collection, workflow_key, chunk_id, prerequisite(191))
    ) CHARACTER SET utf8mb4
    """,
```

- [ ] **Step 4: Add methods to `GraphStoreProtocol` and `NullGraphStore`**

In `GraphStoreProtocol`:

```python
    def upsert_workflow(self, workflow_name: str, chunk_id: str, chunk_index: int,
                        steps: list, prerequisites: list[str]) -> None: ...
    def get_workflow(self, name: str) -> Optional[dict]: ...
    def list_workflows(self, q: Optional[str] = None, limit: int = 50) -> list[dict]: ...
```

In `NullGraphStore`:

```python
    def upsert_workflow(self, workflow_name, chunk_id, chunk_index, steps, prerequisites): pass
    def get_workflow(self, name): return None
    def list_workflows(self, q=None, limit=50): return []
```

- [ ] **Step 5: Add methods to `MariaGraphStore`**

```python
    def upsert_workflow(self, workflow_name, chunk_id, chunk_index, steps, prerequisites):
        if not workflow_name or (not steps and not prerequisites):
            return
        from .normalize import normalize_name
        key = normalize_name(workflow_name)
        with self._connect() as conn, conn.cursor() as cur:
            for s in steps:
                cur.execute(
                    "INSERT INTO workflow_steps (collection, workflow_key, workflow_name, "
                    "chunk_id, chunk_index, step_order, text, precondition) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE "
                    "workflow_name=VALUES(workflow_name), chunk_index=VALUES(chunk_index), "
                    "text=VALUES(text), precondition=VALUES(precondition)",
                    (self._collection, key, workflow_name, chunk_id, chunk_index,
                     s.step_order, s.text, s.precondition))
            for p in prerequisites:
                cur.execute(
                    "INSERT IGNORE INTO workflow_prereqs (collection, workflow_key, "
                    "chunk_id, prerequisite) VALUES (%s, %s, %s, %s)",
                    (self._collection, key, chunk_id, p))

    def get_workflow(self, name):
        from .normalize import normalize_name
        key = normalize_name(name)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT workflow_name, step_order, text, precondition, chunk_id "
                "FROM workflow_steps WHERE collection=%s AND workflow_key=%s "
                "ORDER BY chunk_index, step_order", (self._collection, key))
            rows = cur.fetchall()
            cur.execute(
                "SELECT DISTINCT prerequisite FROM workflow_prereqs "
                "WHERE collection=%s AND workflow_key=%s", (self._collection, key))
            prereqs = [r["prerequisite"] for r in cur.fetchall()]
        if not rows and not prereqs:
            return None
        display = rows[0]["workflow_name"] if rows else name
        steps = [{"order": r["step_order"], "text": r["text"],
                  "precondition": r["precondition"] or "", "chunk_id": r["chunk_id"]}
                 for r in rows]
        return {"workflow_name": display, "prerequisites": prereqs, "steps": steps}

    def list_workflows(self, q=None, limit=50):
        clauses, params = ["collection=%s"], [self._collection]
        if q:
            clauses.append("workflow_key LIKE %s")
            params.append(f"%{q.lower().strip()}%")
        params.append(max(1, min(500, limit)))
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT workflow_key, MAX(workflow_name) AS workflow_name FROM workflow_steps "
                f"WHERE {' AND '.join(clauses)} GROUP BY workflow_key "
                "ORDER BY workflow_name LIMIT %s", params)
            return [{"name": r["workflow_name"]} for r in cur.fetchall()]
```

In `MariaGraphStore.delete_for_chunks`, inside the existing `with` block, add (alongside the edges/entity_chunks deletes):

```python
            cur.execute(
                f"DELETE FROM workflow_steps WHERE collection=%s AND chunk_id IN ({placeholders})",
                [self._collection] + ids)
            cur.execute(
                f"DELETE FROM workflow_prereqs WHERE collection=%s AND chunk_id IN ({placeholders})",
                [self._collection] + ids)
```

In `MariaGraphStore.delete_collection`, add:

```python
            cur.execute("DELETE FROM workflow_steps WHERE collection=%s", (name,))
            cur.execute("DELETE FROM workflow_prereqs WHERE collection=%s", (name,))
```

- [ ] **Step 6: Add workflow methods to `FakeGraphStore` (conftest)**

In `FakeGraphStore._slot()`, add two keys to the lazily-created dict:

```python
            self._backing[self._collection] = {
                "entities": {},
                "entity_chunks": {},
                "edges": [],
                "workflow_steps": [],    # list of dicts
                "workflow_prereqs": [],  # list of dicts
            }
```

Add methods (mirroring MariaGraphStore semantics):

```python
    def upsert_workflow(self, workflow_name, chunk_id, chunk_index, steps, prerequisites):
        if not workflow_name or (not steps and not prerequisites):
            return
        from opendomainmcp.graph.normalize import normalize_name
        key = normalize_name(workflow_name)
        slot = self._slot()
        sidx = {(r["workflow_key"], r["chunk_id"], r["step_order"]): r
                for r in slot["workflow_steps"]}
        for s in steps:
            sidx[(key, chunk_id, s.step_order)] = {
                "workflow_key": key, "workflow_name": workflow_name, "chunk_id": chunk_id,
                "chunk_index": chunk_index, "step_order": s.step_order,
                "text": s.text, "precondition": s.precondition}
        slot["workflow_steps"] = list(sidx.values())
        pidx = {(r["workflow_key"], r["chunk_id"], r["prerequisite"]): r
                for r in slot["workflow_prereqs"]}
        for p in prerequisites:
            pidx[(key, chunk_id, p)] = {"workflow_key": key, "chunk_id": chunk_id,
                                        "prerequisite": p}
        slot["workflow_prereqs"] = list(pidx.values())

    def get_workflow(self, name):
        from opendomainmcp.graph.normalize import normalize_name
        key = normalize_name(name)
        slot = self._slot()
        rows = sorted((r for r in slot["workflow_steps"] if r["workflow_key"] == key),
                      key=lambda r: (r["chunk_index"], r["step_order"]))
        prereqs = []
        for r in slot["workflow_prereqs"]:
            if r["workflow_key"] == key and r["prerequisite"] not in prereqs:
                prereqs.append(r["prerequisite"])
        if not rows and not prereqs:
            return None
        display = rows[0]["workflow_name"] if rows else name
        steps = [{"order": r["step_order"], "text": r["text"],
                  "precondition": r["precondition"], "chunk_id": r["chunk_id"]}
                 for r in rows]
        return {"workflow_name": display, "prerequisites": prereqs, "steps": steps}

    def list_workflows(self, q=None, limit=50):
        slot = self._slot()
        names = {}
        for r in slot["workflow_steps"]:
            if q and q.lower().strip() not in r["workflow_key"]:
                continue
            names[r["workflow_key"]] = r["workflow_name"]
        out = [{"name": n} for _, n in sorted(names.items())]
        return out[:max(1, min(500, limit))]
```

In `FakeGraphStore.delete_for_chunks`, after the existing edge/entity prune, add:

```python
        slot["workflow_steps"] = [r for r in slot["workflow_steps"] if r["chunk_id"] not in ids]
        slot["workflow_prereqs"] = [r for r in slot["workflow_prereqs"] if r["chunk_id"] not in ids]
```

> `delete_collection` already pops the whole collection slot, so workflow data is covered.

- [ ] **Step 7: Run fake-store tests**

Run: `source .venv/bin/activate && ODM_EXTRACT_KNOWLEDGE=true python -m pytest tests/test_workflow_store_fake.py -v`
Expected: PASS (4 passed).

- [ ] **Step 8: Write the MariaDB integration test**

```python
# tests/test_workflow_store_mariadb.py
import os
import pytest
from opendomainmcp.graph.models import WorkflowStep

pytestmark = pytest.mark.integration


@pytest.fixture
def maria_store():
    if not os.getenv("GRAPH_DB_HOST"):
        pytest.skip("MariaDB integration env not configured (set GRAPH_DB_HOST)")
    from opendomainmcp.graph.store import MariaGraphStore
    store = MariaGraphStore(
        host=os.environ["GRAPH_DB_HOST"], port=int(os.getenv("GRAPH_DB_PORT", "3306")),
        user=os.environ["GRAPH_DB_USER"], password=os.getenv("GRAPH_DB_PASSWORD", ""),
        database=os.environ["GRAPH_DB_NAME"], collection="wf-it")
    store.ensure_schema()
    store.delete_for_chunks(["wf-c1", "wf-c2"])
    return store


def test_mariadb_workflow_roundtrip(maria_store):
    maria_store.upsert_workflow("Deploy", "wf-c1", 0,
                                [WorkflowStep(1, "test"), WorkflowStep(2, "tag")], ["perm"])
    maria_store.upsert_workflow("deploy", "wf-c2", 1,
                                [WorkflowStep(1, "ship")], ["perm", "ci"])
    wf = maria_store.get_workflow("DEPLOY")
    assert [s["text"] for s in wf["steps"]] == ["test", "tag", "ship"]
    assert sorted(wf["prerequisites"]) == ["ci", "perm"]
    assert {w["name"] for w in maria_store.list_workflows()} >= {"Deploy"}
    maria_store.delete_for_chunks(["wf-c1", "wf-c2"])
    assert maria_store.get_workflow("Deploy") is None
```

- [ ] **Step 9: Run tests**

Run: `source .venv/bin/activate && ODM_EXTRACT_KNOWLEDGE=true python -m pytest tests/test_workflow_store_fake.py tests/test_workflow_store_mariadb.py -v`
Expected: fake tests PASS; MariaDB test SKIPPED (no `GRAPH_DB_HOST`).

- [ ] **Step 10: Commit**

```bash
git add src/opendomainmcp/graph/store.py tests/conftest.py tests/test_workflow_store_fake.py tests/test_workflow_store_mariadb.py
git commit -m "feat(workflow): MariaDB workflow_steps/workflow_prereqs tables, upsert/get/list, sync"
```

---

## Task 4: Pipeline 接線（chunk_index + 工作流同步）

**Files:**
- Modify: `src/opendomainmcp/ingest/pipeline.py` (`_ingest_file`, new `_write_workflow`)
- Test: `tests/test_pipeline_workflow_sync.py`

**Interfaces:**
- Consumes: `build_workflow`（Task 2）、`upsert_workflow`/`get_workflow`（Task 3）、`fake_graph`/`pipeline` fixtures。
- Produces: pipeline 在切分後賦值 `chunk.chunk_index`，並在 `_write_graph` 之後呼叫 `_write_workflow(chunks)`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_workflow_sync.py
from opendomainmcp.config import Settings
from opendomainmcp.ingest.pipeline import Pipeline
from opendomainmcp.models import KnowledgeUnit


class _WorkflowExtractor:
    """Splits a 'runbook' file into a 2-chunk workflow keyed by step markers.
    Each chunk's text is a step line like 'S1 test' / 'S2 deploy'."""
    def extract(self, text, kind, language=None):
        order = int(text.split()[0][1:])           # 'S1 test' -> 1
        return KnowledgeUnit(
            summary=text, knowledge_type="Runbook", audience=["operations"],
            confidence=1.0,
            workflow={"name": "Deploy", "prerequisites": ["perm"],
                      "steps": [{"order": order, "text": text}]})


def test_ingest_populates_and_orders_workflow(tmp_path, store, fake_graph):
    # one file, small chunk_size so it splits into two chunks in document order
    f = tmp_path / "runbook.txt"
    f.write_text("S1 test\n\nS2 deploy")
    p = Pipeline(store, _WorkflowExtractor(),
                 Settings(chunk_size=8, chunk_overlap=0), graph=fake_graph)
    p.ingest_path(str(f))
    wf = fake_graph.get_workflow("Deploy")
    assert wf is not None
    assert [s["text"] for s in wf["steps"]] == ["S1 test", "S2 deploy"]  # document order
    assert wf["prerequisites"] == ["perm"]


def test_reingest_prunes_stale_workflow(tmp_path, store, fake_graph):
    f = tmp_path / "runbook.txt"
    f.write_text("S1 test")
    p = Pipeline(store, _WorkflowExtractor(),
                 Settings(chunk_size=200, chunk_overlap=0), graph=fake_graph)
    p.ingest_path(str(f))
    assert fake_graph.get_workflow("Deploy") is not None
    f.write_text("S1 different")            # same chunk id changes -> old pruned
    p.ingest_path(str(f))
    wf = fake_graph.get_workflow("Deploy")
    assert [s["text"] for s in wf["steps"]] == ["S1 different"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && ODM_EXTRACT_KNOWLEDGE=true python -m pytest tests/test_pipeline_workflow_sync.py -v`
Expected: FAIL — `get_workflow` returns None (pipeline does not write workflows yet) / `chunk_index` not set.

- [ ] **Step 3: Assign `chunk_index` and call `_write_workflow`**

In `pipeline.py` `_ingest_file`, right after the `if not chunks:` guard block (after the `return`), add:

```python
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i
```

In the same method, after the existing `self._write_graph(chunks)` line:

```python
        self._write_graph(chunks)
        self._write_workflow(chunks)
```

Add the helper next to `_write_graph`:

```python
    def _write_workflow(self, chunks: list[Chunk]) -> None:
        from ..graph.workflow import build_workflow

        for chunk in chunks:
            if not chunk.knowledge:
                continue
            steps, prerequisites, name = build_workflow(chunk.knowledge)
            if not name:
                continue
            self._graph.upsert_workflow(name, chunk.id, chunk.chunk_index or 0,
                                        steps, prerequisites)
```

> The stale-prune and `_sync_deletions` paths already call `self._graph.delete_for_chunks(...)`, which now also clears workflow rows (Task 3) — no extra wiring needed.

- [ ] **Step 4: Run tests**

Run: `source .venv/bin/activate && ODM_EXTRACT_KNOWLEDGE=true python -m pytest tests/test_pipeline_workflow_sync.py -v && ODM_EXTRACT_KNOWLEDGE=true python -m pytest -q`
Expected: new tests PASS; full suite green (2 skipped).

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/ingest/pipeline.py tests/test_pipeline_workflow_sync.py
git commit -m "feat(workflow): assign chunk_index and sync workflows during ingest"
```

---

## Task 5: 查詢層 — API 端點與 MCP 工具

**Files:**
- Modify: `src/opendomainmcp/api/app.py` (`/api/graph/workflow/{name}`, `/api/graph/workflows`)
- Modify: `src/opendomainmcp/server.py` (`graph_tool_handlers` + view registration)
- Test: `tests/test_workflow_api.py`, `tests/test_workflow_mcp.py`

**Interfaces:**
- Consumes: `ctx.graph.get_workflow`/`list_workflows`（Task 3）、`create_app(context=...)`（既有）、`graph_tool_handlers(ctx)`（既有，回傳 dict of callables）。
- Produces:
  - `GET /api/graph/workflow/{name}` → `get_workflow` dict 或 404 envelope `{"error": ...}`
  - `GET /api/graph/workflows?q=&limit=` → `{"items": [{"name": ...}]}`
  - MCP `get_workflow_steps(name)`、`list_workflows(q?, limit=50)`（Operations 視圖；`get_workflow_steps` 亦加進 Product 視圖）

- [ ] **Step 1: Write the failing API test**

```python
# tests/test_workflow_api.py
from fastapi.testclient import TestClient
from opendomainmcp.api.app import create_app
from opendomainmcp.config import Settings
from opendomainmcp.context import Context
from opendomainmcp.graph.models import WorkflowStep


def _client(store, fake_graph):
    fake_graph.upsert_workflow("Deploy", "c1", 0,
                               [WorkflowStep(1, "test"), WorkflowStep(2, "ship")], ["perm"])
    ctx = Context(settings=Settings(), store=store, pipeline=None, graph=fake_graph)
    return TestClient(create_app(context=ctx))


def test_get_workflow_endpoint(store, fake_graph):
    resp = _client(store, fake_graph).get("/api/graph/workflow/Deploy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow_name"] == "Deploy"
    assert [s["text"] for s in body["steps"]] == ["test", "ship"]
    assert body["prerequisites"] == ["perm"]


def test_get_workflow_404(store, fake_graph):
    resp = _client(store, fake_graph).get("/api/graph/workflow/nope")
    assert resp.status_code == 404
    assert "error" in resp.json()


def test_list_workflows_endpoint(store, fake_graph):
    resp = _client(store, fake_graph).get("/api/graph/workflows")
    assert resp.status_code == 200
    assert resp.json()["items"] == [{"name": "Deploy"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && ODM_EXTRACT_KNOWLEDGE=true python -m pytest tests/test_workflow_api.py -v`
Expected: FAIL — routes return 404 (not registered).

- [ ] **Step 3: Add API routes**

In `api/app.py`, next to the existing `/api/graph/*` routes (they use `ctx: Context = Depends(get_ctx)` and `JSONResponse`):

```python
    @app.get("/api/graph/workflow/{name}")
    def graph_workflow(name: str, ctx: Context = Depends(get_ctx)):
        result = ctx.graph.get_workflow(name)
        if result is None:
            return JSONResponse(status_code=404,
                                content={"error": f"workflow not found: {name}"})
        return result

    @app.get("/api/graph/workflows")
    def graph_workflows(q: str | None = None, limit: int = 50,
                        ctx: Context = Depends(get_ctx)):
        return {"items": ctx.graph.list_workflows(q=q, limit=limit)}
```

- [ ] **Step 4: Run API tests**

Run: `source .venv/bin/activate && ODM_EXTRACT_KNOWLEDGE=true python -m pytest tests/test_workflow_api.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Write the failing MCP test**

```python
# tests/test_workflow_mcp.py
from opendomainmcp.config import Settings
from opendomainmcp.context import Context
from opendomainmcp.graph.models import WorkflowStep
from opendomainmcp.server import graph_tool_handlers


def _ctx(store, fake_graph):
    fake_graph.upsert_workflow("Deploy", "c1", 0, [WorkflowStep(1, "test")], ["perm"])
    return Context(settings=Settings(), store=store, pipeline=None, graph=fake_graph)


def test_get_workflow_steps_tool(store, fake_graph):
    h = graph_tool_handlers(_ctx(store, fake_graph))
    out = h["get_workflow_steps"](name="Deploy")
    assert out["workflow_name"] == "Deploy"
    assert out["steps"][0]["text"] == "test"


def test_list_workflows_tool(store, fake_graph):
    h = graph_tool_handlers(_ctx(store, fake_graph))
    assert h["list_workflows"]() == [{"name": "Deploy"}]
```

- [ ] **Step 6: Run test to verify it fails**

Run: `source .venv/bin/activate && ODM_EXTRACT_KNOWLEDGE=true python -m pytest tests/test_workflow_mcp.py -v`
Expected: FAIL — `graph_tool_handlers` has no `get_workflow_steps`.

- [ ] **Step 7: Extend `graph_tool_handlers` and register in views**

In `server.py` `graph_tool_handlers(ctx)`, add two handlers to the returned dict:

```python
    def get_workflow_steps(name: str):
        result = ctx.graph.get_workflow(name)
        return result if result is not None else {"workflow_name": name,
                                                  "prerequisites": [], "steps": []}

    def list_workflows(q: str | None = None, limit: int = 50):
        return ctx.graph.list_workflows(q=q, limit=limit)

    return {"get_entity": get_entity, "list_related_entities": list_related_entities,
            "get_workflow_steps": get_workflow_steps, "list_workflows": list_workflows}
```

(Keep the existing `get_entity`/`list_related_entities` entries — extend the dict, don't replace it.)

In `build_view_server`, where graph tools are registered per view, register the workflow tools on the **operations** view (and `get_workflow_steps` on **product**), following the existing registration pattern used for `get_entity`/`list_related_entities` (the implementer must read the current registration block and mirror it — e.g. an `if view_name in ("operations",):` branch that `add_tool`s `list_workflows`, plus `get_workflow_steps` for `("operations", "product")`). Wire each tool body to `graph_tool_handlers(_context(collection))[...]`.

- [ ] **Step 8: Run tests**

Run: `source .venv/bin/activate && ODM_EXTRACT_KNOWLEDGE=true python -m pytest tests/test_workflow_mcp.py tests/test_workflow_api.py -v && ODM_EXTRACT_KNOWLEDGE=true python -m pytest -q`
Expected: all PASS; full suite green (2 skipped).

- [ ] **Step 9: Commit**

```bash
git add src/opendomainmcp/api/app.py src/opendomainmcp/server.py tests/test_workflow_api.py tests/test_workflow_mcp.py
git commit -m "feat(workflow): workflow query API endpoints and MCP tools"
```

---

## Self-Review

**Spec coverage:**
- §4 萃取（`workflow` 物件、`_parse_workflow`）→ Task 1 ✅
- §3 資料模型（`KnowledgeUnit.workflow`、`Chunk.chunk_index` 不進 hash、`WorkflowStep`）→ Task 1 ✅
- §5 `build_workflow` 純邏輯 → Task 2 ✅
- §6 儲存（兩表、`upsert_workflow`/`get_workflow`/`list_workflows`、跨 chunk 合併排序、prereq 去重、collection 隔離、delete 同步、Fake 鏡像）→ Task 3 ✅
- §8 接線（`chunk_index` 賦值、`_write_workflow`、prune 同步）→ Task 4 ✅
- §7 查詢層（API 404/list、MCP Operations+Product）→ Task 5 ✅
- §9 測試（純邏輯、Fake、pipeline 跨 chunk、API/MCP、MariaDB integration）→ Tasks 1–5 ✅

**Placeholder scan:** 無 TBD/TODO；所有 code step 皆含完整程式碼。Task 5 Step 7 的 view 註冊要求實作者比照現有 `get_entity` 註冊樣式（已說明具體分支與 add_tool 目標），非佔位。

**Type consistency:**
- `upsert_workflow(workflow_name, chunk_id, chunk_index, steps, prerequisites)` 簽章在 Protocol/Maria/Null/Fake/pipeline/測試一致。
- `get_workflow` 回傳 `{"workflow_name", "prerequisites":[str], "steps":[{"order","text","precondition","chunk_id"}]}` 在 Maria/Fake/API/MCP/測試一致。
- `list_workflows(q, limit) -> [{"name"}]` 一致。
- `WorkflowStep(step_order, text, precondition="")` 在 models/builder/store/測試一致。
- `build_workflow(knowledge) -> (list[WorkflowStep], list[str], str)` 在 Task 2/4 一致。
- `Chunk.chunk_index` 由 pipeline 賦值、被 `upsert_workflow` 使用，一致。

---

## Execution Handoff

見對話中的執行選項提示。
