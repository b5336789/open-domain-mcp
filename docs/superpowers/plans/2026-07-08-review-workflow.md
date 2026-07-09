# Review Workflow Enhancements (Enhancement #3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make expert review usable at corpus scale — an append-only audit trail for every approve/reject, batch review operations, and a risk-ordered pending queue that surfaces conflicted rules (#5) and unverified evidence (#2) first — plus an optional auto-approve for high-trust verified rules.

**Architecture:** A new `review/audit.py` owns an append-only SQLite log at `<data_dir>/review_audit.db` (stdlib `sqlite3`, first use of it in the repo), wired as a lazily-created module singleton mirroring the `_ingest_jobs` pattern in `api/app.py`. The existing approve/reject routes gain a `note` body and an `actor` (from the `auth_dependency` principal) and write one audit row each. A new `POST /api/items/review-batch` applies one action to many ids transactionally-per-item, one audit row each. `GET /api/items/{id}/history` returns the item's audit rows. The pending list route gains an opt-in risk ordering computed in Python (fetch-all-pending → deterministic score → slice) since Chroma `get()` has no ordering. `review_auto_approve_high_trust` flips high-trust fully-verified rules to approved at consensus upsert time with an `auto` audit actor. The SPA Review page gets per-item checkboxes, select-all-on-page, batch approve/reject with an optional note, filters (source/trust/knowledge_type/evidence_status), and a per-item history disclosure.

**Tech Stack:** Python ≥ 3.11 (stdlib `sqlite3`), FastAPI, React/TS SPA (Playwright e2e); pytest offline.

**Spec:** `docs/superpowers/specs/2026-07-06-review-workflow-enhancements-design.md`

## Global Constraints

- All tests offline; `.venv/bin/python -m pytest`; SPA verified via `cd web && npm run build` + Playwright where a spec already exists.
- Audit DB path (fixed): `Path(settings.data_dir) / "review_audit.db"`. Deliberately SQLite, not MariaDB — review must work when the graph store is unwired.
- Audit row schema (fixed): `id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, item_id TEXT NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', prev_status TEXT NOT NULL DEFAULT '', new_status TEXT NOT NULL DEFAULT ''`. Append-only: only INSERT and SELECT — never UPDATE/DELETE.
- `ts` is ISO-8601 UTC (`datetime.now(timezone.utc).isoformat()`); pass a clock callable into the logger for deterministic tests (default = that expression).
- `action` values (fixed): `approve` | `reject` | `auto-approve`. `actor`: the principal's API-key name when auth is on, else `local` (spec-fixed — NOT the `anonymous` role string).
- Append-only, never lost (Fail Loud): an audit write failure surfaces as a 500 on the mutating route, not a silent success. The status change and its audit row must both happen or the route reports failure.
- Priority order (fixed, ascending sort key so lowest sorts first): `conflicted` trust → 0; else `evidence_status == "unverified"` → 1; else `confidence < 0.5` → 2; else 3. Ties broken by existing insertion order (stable sort). Applied ONLY to `review_status == "pending"` listings when `order == "priority"`; default order unchanged (backward compatible).
- New settings: `review_auto_approve_high_trust: bool = False` (EDITABLE, appended after `codegraph_extract`). Documented in `.env.example`.
- Auto-approve criteria (fixed): `trust == "high"` AND `evidence_status == "verified"`. Applies only to `kind == "rule"` items at consensus upsert; writes an audit row with `action="auto-approve"`, `actor="auto"`.
- Everything additive/backward-compatible: no note ⇒ empty string; no order param ⇒ today's behavior; auto-approve default off.

## Parallel execution note

Waves: **[T1] → [T2, T3, T4 parallel — T2 routes, T3 priority route, T4 consensus hook: disjoint] → [T5 SPA]**. T2/T3 both touch `api/app.py` — to keep them parallel, T3 adds a SEPARATE route function and T2 owns the existing approve/reject/batch; they must not edit the same lines. To be safe, run T3 AFTER T2 (sequential) — see revised waves below. **Revised waves: [T1] → [T2] → [T3, T4 parallel] → [T5].**

---

### Task 1: `review/audit.py` — append-only SQLite audit log

**Files:**
- Create: `src/opendomainmcp/review/__init__.py`, `src/opendomainmcp/review/audit.py`
- Test: `tests/test_review_audit.py`

**Interfaces:**
- Produces:

```python
@dataclass
class AuditEntry:
    ts: str
    item_id: str
    action: str
    actor: str
    note: str = ""
    prev_status: str = ""
    new_status: str = ""

class AuditLog:
    def __init__(self, db_path, clock=None):
        # clock() -> ISO ts string; default datetime.now(timezone.utc).isoformat()
        # creates parent dir + table if missing (idempotent)
    def record(self, item_id, action, actor, note="", prev_status="",
               new_status="") -> AuditEntry     # INSERTs one row, returns it
    def history(self, item_id) -> list[dict]     # rows for one item, newest first
    def all(self, limit=200) -> list[dict]       # recent rows, newest first
```

- Uses stdlib `sqlite3` with `check_same_thread=False` and an internal `threading.Lock` around writes (FastAPI serves from a threadpool). Connection opened once per `AuditLog`. Schema created in `__init__` via `CREATE TABLE IF NOT EXISTS`. Append-only: the class exposes no update/delete.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_review_audit.py
"""Append-only SQLite review audit log (enhancement #3)."""

from opendomainmcp.review.audit import AuditEntry, AuditLog


def _log(tmp_path, ts="2026-07-08T00:00:00+00:00"):
    ticks = [ts]
    return AuditLog(tmp_path / "review_audit.db", clock=lambda: ticks[0])


def test_record_returns_entry_and_persists(tmp_path):
    log = _log(tmp_path)
    entry = log.record("chunk1", "approve", "alice", note="looks good",
                       prev_status="pending", new_status="approved")
    assert isinstance(entry, AuditEntry)
    assert entry.item_id == "chunk1" and entry.action == "approve"
    assert entry.actor == "alice" and entry.note == "looks good"
    rows = log.history("chunk1")
    assert len(rows) == 1
    assert rows[0]["new_status"] == "approved" and rows[0]["actor"] == "alice"


def test_history_is_per_item_and_newest_first(tmp_path):
    ticks = ["2026-07-08T00:00:01+00:00"]
    log = AuditLog(tmp_path / "a.db", clock=lambda: ticks[0])
    log.record("a", "approve", "u")
    ticks[0] = "2026-07-08T00:00:02+00:00"
    log.record("a", "reject", "u")
    log.record("b", "approve", "u")
    a_hist = log.history("a")
    assert [r["action"] for r in a_hist] == ["reject", "approve"]  # newest first
    assert len(log.history("b")) == 1


def test_reopening_db_keeps_rows(tmp_path):
    p = tmp_path / "persist.db"
    AuditLog(p, clock=lambda: "2026-07-08T00:00:00+00:00").record("x", "approve", "u")
    reopened = AuditLog(p, clock=lambda: "2026-07-08T00:00:00+00:00")
    assert len(reopened.history("x")) == 1


def test_all_returns_recent_newest_first(tmp_path):
    ticks = ["2026-07-08T00:00:01+00:00"]
    log = AuditLog(tmp_path / "a.db", clock=lambda: ticks[0])
    log.record("a", "approve", "u")
    ticks[0] = "2026-07-08T00:00:02+00:00"
    log.record("b", "reject", "u")
    rows = log.all()
    assert [r["item_id"] for r in rows] == ["b", "a"]


def test_defaults_and_auto_actor(tmp_path):
    log = _log(tmp_path)
    e = log.record("r1", "auto-approve", "auto", new_status="approved")
    assert e.note == "" and e.prev_status == ""
    assert log.history("r1")[0]["action"] == "auto-approve"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_review_audit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'opendomainmcp.review'`

- [ ] **Step 3: Implement**

```python
# src/opendomainmcp/review/__init__.py
"""Review workflow support: append-only audit log and batch helpers (#3)."""
```

```python
# src/opendomainmcp/review/audit.py
"""Append-only SQLite audit log for review decisions.

Every approve/reject/auto-approve is recorded with actor, timestamp, note,
and the status transition. SQLite (not MariaDB) so review auditing works even
when the graph store is unwired. Append-only by construction: the class only
INSERTs and SELECTs — there is no update or delete path."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    item_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    prev_status TEXT NOT NULL DEFAULT '',
    new_status TEXT NOT NULL DEFAULT ''
)
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AuditEntry:
    ts: str
    item_id: str
    action: str
    actor: str
    note: str = ""
    prev_status: str = ""
    new_status: str = ""


class AuditLog:
    def __init__(self, db_path, clock: Optional[Callable[[], str]] = None):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _utc_now
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute(_SCHEMA)

    def record(self, item_id: str, action: str, actor: str, note: str = "",
               prev_status: str = "", new_status: str = "") -> AuditEntry:
        entry = AuditEntry(ts=self._clock(), item_id=item_id, action=action,
                           actor=actor, note=note, prev_status=prev_status,
                           new_status=new_status)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO review_audit "
                "(ts, item_id, action, actor, note, prev_status, new_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (entry.ts, entry.item_id, entry.action, entry.actor,
                 entry.note, entry.prev_status, entry.new_status))
        return entry

    def history(self, item_id: str) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM review_audit WHERE item_id = ? ORDER BY id DESC",
            (item_id,))
        return [dict(r) for r in cur.fetchall()]

    def all(self, limit: int = 200) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM review_audit ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cur.fetchall()]
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_review_audit.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/review tests/test_review_audit.py
git commit -m "feat: append-only SQLite review audit log"
```

---

### Task 2: Audit-writing approve/reject + batch + history routes

**Files:**
- Modify: `src/opendomainmcp/api/app.py` (approve/reject routes; new batch + history routes; audit-log singleton accessor; request models)
- Test: `tests/test_api.py` (append)

**Interfaces:**
- Consumes: `AuditLog` (T1), `auth_dependency` principal (existing).
- Produces:
  - A module-level `_audit_logs: dict[str, AuditLog]` cache keyed by `str(data_dir)` + a `_get_audit_log(ctx) -> AuditLog` accessor (lazily constructs, mirroring `_ingest_jobs`), guarded by a `threading.Lock`.
  - Helper `_actor(principal) -> str`: the principal's key name when auth is on (`principal.get("key")`), else `"local"`. (Principal dict has `{"role","views","key"}`; when auth off `key` is None → `"local"`.)
  - `approve_item`/`reject_item` gain an optional JSON body `{"note": str}` (a new `ReviewAction` pydantic model with `note: str = ""`, body optional via `= ReviewAction()`), inject `principal: dict = Depends(auth_dependency)`, read the item's current `review_status` as `prev_status`, update metadata, then `audit.record(...)`. If the metadata update fails → 404 (unchanged). If the audit write raises → 500 (let it propagate; do not swallow).
  - `POST /api/items/review-batch` — body `{"ids": list[str], "action": "approve"|"reject", "note": str = ""}`; applies to each id (skipping ids that 404, collecting them), one audit row per applied id; returns `{"updated": [ids], "missing": [ids], "action": action}`.
  - `GET /api/items/{item_id}/history` → `audit.history(item_id)` (list, newest first).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py` (follow the existing `client` fixture returning `(TestClient, ctx, tmp_path)`):

```python
def _seed_pending(ctx, n=3):
    from opendomainmcp.models import Chunk, KnowledgeUnit
    chunks = [Chunk(text=f"pending {i}", source=f"s{i}.md", kind="text",
                    knowledge=KnowledgeUnit(summary=f"p{i}", knowledge_type="Feature",
                                            review_status="pending"))
              for i in range(n)]
    ctx.store.upsert(chunks)
    return [c.id for c in chunks]


def test_approve_writes_audit_with_note_and_actor(client):
    tc, ctx, _ = client
    ids = _seed_pending(ctx, 1)
    r = tc.post(f"/api/items/{ids[0]}/approve", json={"note": "verified by hand"})
    assert r.json()["metadata"]["review_status"] == "approved"
    hist = tc.get(f"/api/items/{ids[0]}/history").json()
    assert len(hist) == 1
    assert hist[0]["action"] == "approve" and hist[0]["note"] == "verified by hand"
    assert hist[0]["prev_status"] == "pending" and hist[0]["new_status"] == "approved"
    assert hist[0]["actor"] == "local"   # auth off in tests


def test_reject_without_note_defaults_empty(client):
    tc, ctx, _ = client
    ids = _seed_pending(ctx, 1)
    tc.post(f"/api/items/{ids[0]}/reject")
    hist = tc.get(f"/api/items/{ids[0]}/history").json()
    assert hist[0]["action"] == "reject" and hist[0]["note"] == ""


def test_review_batch_applies_and_reports_missing(client):
    tc, ctx, _ = client
    ids = _seed_pending(ctx, 3)
    r = tc.post("/api/items/review-batch",
                json={"ids": ids + ["nope"], "action": "approve", "note": "bulk"})
    body = r.json()
    assert set(body["updated"]) == set(ids) and body["missing"] == ["nope"]
    pending = tc.get("/api/items", params={"review_status": "pending"}).json()
    assert pending == []
    # one audit row per applied id, all carrying the batch note
    for i in ids:
        h = tc.get(f"/api/items/{i}/history").json()
        assert h[0]["note"] == "bulk" and h[0]["action"] == "approve"


def test_history_newest_first(client):
    tc, ctx, _ = client
    ids = _seed_pending(ctx, 1)
    tc.post(f"/api/items/{ids[0]}/approve")
    tc.post(f"/api/items/{ids[0]}/reject")
    hist = tc.get(f"/api/items/{ids[0]}/history").json()
    assert [h["action"] for h in hist] == ["reject", "approve"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_api.py -k "audit or batch or history" -v`
Expected: FAIL (routes/models absent)

- [ ] **Step 3: Implement** per Interfaces. Read `app.py`'s existing approve/reject block and the `_ingest_jobs` singleton first; place the `_audit_logs` cache + lock beside `_ingest_jobs`; add the `ReviewAction`/`ReviewBatch` pydantic models beside the existing `ItemPatch`/`ItemCreate`. Keep the 404 semantics. For batch, read each item's prev_status before updating.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: all pass (existing review test unaffected — it does not read history)

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/api/app.py tests/test_api.py
git commit -m "feat: audit-logged approve/reject, batch review, and history routes"
```

---

### Task 3: Risk-ordered pending queue

**Files:**
- Modify: `src/opendomainmcp/api/app.py` (`list_items` gains `order` param + priority path)
- Create: `src/opendomainmcp/review/priority.py`
- Test: `tests/test_review_priority.py`, `tests/test_api.py` (append)

**Interfaces:**
- Consumes: item metadata dicts.
- Produces:
  - `review/priority.py`: `priority_score(meta: dict) -> int` per the fixed order (conflicted 0 / unverified 1 / low-confidence 2 / else 3); `order_by_priority(items: list[dict]) -> list[dict]` stable-sorts by score.
  - `list_items` gains `order: str | None = None`. When `order == "priority"` AND `review_status == "pending"`: fetch the full pending set (paginate `get_items` internally with a large page until exhausted — bounded; pending sets are small), sort by `order_by_priority`, then apply `offset`/`limit` slice in Python. Otherwise unchanged (Chroma-native offset/limit).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_review_priority.py
"""Risk-ordered review priority scoring (enhancement #3)."""

from opendomainmcp.review.priority import order_by_priority, priority_score


def test_scores():
    assert priority_score({"trust": "conflicted"}) == 0
    assert priority_score({"evidence_status": "unverified"}) == 1
    assert priority_score({"confidence": 0.3}) == 2
    assert priority_score({"confidence": 0.9}) == 3
    assert priority_score({}) == 3
    # conflicted beats unverified beats low-confidence
    assert priority_score({"trust": "conflicted",
                           "evidence_status": "unverified"}) == 0


def test_order_is_stable_within_a_tier():
    items = [{"id": "a", "metadata": {"confidence": 0.9}},
             {"id": "b", "metadata": {"trust": "conflicted"}},
             {"id": "c", "metadata": {"evidence_status": "unverified"}},
             {"id": "d", "metadata": {"confidence": 0.9}}]
    ordered = [i["id"] for i in order_by_priority(items)]
    assert ordered == ["b", "c", "a", "d"]   # b(0) c(1) then a,d(3) stable
```

Append to `tests/test_api.py`:

```python
def test_pending_priority_order_route(client):
    from opendomainmcp.models import Chunk, KnowledgeUnit, RuleItem
    tc, ctx, _ = client
    ctx.store.upsert([
        Chunk(text="plain", source="p.md", kind="text",
              knowledge=KnowledgeUnit(summary="ok", confidence=0.9,
                                      review_status="pending")),
        Chunk(text="weak", source="w.md", kind="text",
              knowledge=KnowledgeUnit(summary="weak", confidence=0.2,
                                      evidence_status="unverified",
                                      review_status="pending")),
    ])
    ctx.store.upsert([RuleItem(statement="conflicting rule", trust="conflicted",
                               review_status="pending")])
    ordered = tc.get("/api/items",
                     params={"review_status": "pending", "order": "priority"}).json()
    kinds = [i["metadata"].get("trust") or i["metadata"].get("evidence_status")
             or "plain" for i in ordered]
    assert kinds[0] == "conflicted" and kinds[1] == "unverified"

    # default order unaffected
    default = tc.get("/api/items", params={"review_status": "pending"}).json()
    assert len(default) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_review_priority.py tests/test_api.py -k "priority" -v`
Expected: FAIL

- [ ] **Step 3: Implement** per Interfaces.

```python
# src/opendomainmcp/review/priority.py
"""Risk-ordered review queue scoring.

Lower score = higher risk = sorts first: conflicted rules (#5), then
unverified evidence (#2), then low-confidence extractions. Deterministic and
computed at query time from metadata already on the item — no new storage."""

from __future__ import annotations

_LOW_CONFIDENCE = 0.5


def priority_score(meta: dict) -> int:
    if meta.get("trust") == "conflicted":
        return 0
    if meta.get("evidence_status") == "unverified":
        return 1
    try:
        if float(meta.get("confidence", 1.0)) < _LOW_CONFIDENCE:
            return 2
    except (TypeError, ValueError):
        pass
    return 3


def order_by_priority(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda it: priority_score(it.get("metadata", {})))
```

For `list_items`, read the current function and add the branch; internal full fetch via a bounded loop over `get_items(limit=500, offset=...)` until a short page.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_review_priority.py tests/test_api.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/review/priority.py src/opendomainmcp/api/app.py tests/test_review_priority.py tests/test_api.py
git commit -m "feat: risk-ordered pending review queue"
```

---

### Task 4: Auto-approve high-trust verified rules

**Files:**
- Modify: `src/opendomainmcp/config.py` (setting + EDITABLE), `.env.example`
- Modify: `src/opendomainmcp/consensus/run.py` (auto-approve at upsert)
- Test: `tests/test_consensus_run.py` (append)

**Interfaces:**
- Consumes: `AuditLog` (T1); `RuleItem`.
- Produces:
  - `config.py`: `review_auto_approve_high_trust: bool = False` (after `codegraph_extract`; add to EDITABLE_FIELDS). `.env.example` documents it.
  - `run.py`: after the Fix-1 human-status restore block and before `store.upsert(rules)`, when `settings.review_auto_approve_high_trust`: for each rule with `trust == "high"` and `evidence_status == "verified"` whose current `review_status` is not a human decision (`approved`/`rejected` already set by Fix 1 must be left alone), set `review_status = "approved"` and record an audit row (`action="auto-approve"`, `actor="auto"`, `new_status="approved"`). `run_consensus` gains an optional `audit: Optional[AuditLog] = None` param (default constructs `AuditLog(Path(settings.data_dir) / "review_audit.db")`); result dict gains `"auto_approved": n`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_consensus_run.py`:

```python
def test_auto_approve_high_trust_verified_rules(store, fake_graph, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.consensus.adjudicate import RuleAdjudicator
    from opendomainmcp.consensus.run import run_consensus
    from opendomainmcp.review.audit import AuditLog

    _seed(store)  # two cross-layer chunks, verified evidence -> high trust
    settings = Settings(data_dir=tmp_path, review_auto_approve_high_trust=True)
    audit = AuditLog(tmp_path / "review_audit.db",
                     clock=lambda: "2026-07-08T00:00:00+00:00")
    result = run_consensus(store, settings, graph=fake_graph, audit=audit,
                           adjudicator=_same_adjudicator(tmp_path))
    assert result["auto_approved"] == 1
    rules = store.get_items(limit=10, where={"kind": "rule"})
    assert rules[0]["metadata"]["review_status"] == "approved"
    hist = audit.history(rules[0]["id"])
    assert hist and hist[0]["action"] == "auto-approve" and hist[0]["actor"] == "auto"


def test_auto_approve_off_by_default(store, fake_graph, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.consensus.run import run_consensus

    _seed(store)
    result = run_consensus(store, Settings(data_dir=tmp_path), graph=fake_graph,
                           adjudicator=_same_adjudicator(tmp_path))
    assert result.get("auto_approved", 0) == 0
    rules = store.get_items(limit=10, where={"kind": "rule"})
    assert rules[0]["metadata"]["review_status"] == "approved" or \
        rules[0]["metadata"]["review_status"] == "pending"  # per review_mode, not auto
```

(Note: `_seed` produces high-trust verified rules — confirm the seed's evidence is `verified`; if `_seed`'s evidence lacks `verified=True`, the auto-approve test needs a seed variant that yields `evidence_status=="verified"`. Read `_seed` first and adapt.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_consensus_run.py -k auto_approve -v`
Expected: FAIL (setting + param absent)

- [ ] **Step 3: Implement** per Interfaces. Ensure auto-approve does not override a human `rejected` restored by Fix 1 (check the current status is not already a human decision).

- [ ] **Step 4: Run tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_consensus_run.py tests/test_config.py -v` then `.venv/bin/python -m pytest`
Expected: all pass, no regressions

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/config.py src/opendomainmcp/consensus/run.py .env.example tests/test_consensus_run.py
git commit -m "feat: optional auto-approve of high-trust verified rules with audit"
```

---

### Task 5: SPA — batch review, filters, history

**Files:**
- Modify: `web/src/pages/Review.tsx`, `web/src/api.ts` (client methods + types)
- Test: `cd web && npm run build`; extend `web/tests/review.spec.ts` (exists).

**Interfaces:**
- Consumes: T2/T3 routes.
- Produces:
  - `api.ts`: `reviewBatch(ids, action, note)`, `itemHistory(id)`, and `items(...)` gains an `order` filter and the extra filters (source_contains? — the route supports `kind`/`review_status`/`knowledge_type`; add `order`); `approveItem`/`rejectItem` accept an optional `note`. Extend the `Item` type if needed (additive).
  - Review page: per-item checkbox; a header row with select-all-on-page, a batch Approve/Reject pair (enabled when ≥1 selected) with an optional note input; a "Sort by risk" toggle that sets `order=priority` on the pending tab; a per-item "History" disclosure calling `itemHistory`. Match existing component conventions (Button/Badge/Checkbox — if no Checkbox component exists, a plain `<input type="checkbox">` styled to match). Keep the existing single-item approve/reject.

- [ ] **Step 1: Read `Review.tsx` and `api.ts` fully**; identify the item-card render and the tab/filter header.
- [ ] **Step 2: Implement** the client methods, then the UI (checkboxes + batch bar + risk toggle + history disclosure).
- [ ] **Step 3: Build**

Run: `cd web && npm run build`
Expected: clean tsc + vite build.

- [ ] **Step 4: Extend the Playwright spec** — add a `test.describe("batch review")` covering: select-all, batch approve removes the rows, and the risk-sort toggle. Mock the new endpoints in the existing `installApiMocks` fixture.

- [ ] **Step 5: Commit**

```bash
git add web/src web/tests src/opendomainmcp/api/static
git commit -m "feat: batch review, risk sort, and history in the Review page"
```

(Commit built static assets only if tracked — check `.gitignore` first.)

---

## Self-review notes

- **Spec coverage:** Audit log with actor/ts/note/transition, append-only, SQLite-not-MariaDB ✔ T1; approve/reject notes + actor + `GET /history` ✔ T2; batch endpoint + one audit row per item ✔ T2; SPA checkboxes/select-all/filters ✔ T5; risk-ordered queue (conflicted → unverified → low-confidence) ✔ T3 (this is the "unverified sorts first" item deferred from #2 and the conflicted-first from #5); optional auto-approve high-trust+verified with `auto` actor ✔ T4. Out-of-scope per spec (reviewer roles/RBAC, assignment queues, extra workflow states) not built.
- **Placeholder scan:** T4's `_seed` evidence-status caveat and T5's component-convention notes name exactly what to read; everything else is complete code.
- **Type consistency:** `AuditLog.record/history` (T1) consumed by T2 routes and T4 consensus; `priority_score`/`order_by_priority` (T3) used only in `list_items`; `review_auto_approve_high_trust` (T4) + `_actor`/`_get_audit_log` (T2) are the shared seams.
- **Final-review deferral:** source-file filter deferred (needs source_contains support on /api/items).
- **Known risks:** two SQLite writers (the API audit log and the consensus auto-approve audit log) open the same `review_audit.db` — both use `check_same_thread=False` + a per-instance lock, and SQLite's own file locking serializes cross-process/instance writes; acceptable for this low-write-rate log, note for final review. `list_items` priority path fetches all pending — bounded by pending-set size; documented.
