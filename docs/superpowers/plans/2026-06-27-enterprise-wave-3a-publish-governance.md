# Enterprise Wave 3A Publish Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add durable MCP publish decision records, readiness-gated override publishing, and an MCP Publish workspace.

**Architecture:** Publish governance lives in a new backend domain module under `src/opendomainmcp/publish/`. Existing MCP endpoint routes call the publish service, which computes Quality Evidence, persists decisions in the active data directory, and returns enriched endpoint rows. The frontend keeps route `/mcp`, renames the workspace to MCP Publish, and adds readiness gates, override publishing, and decision history.

**Tech Stack:** Python 3.11, FastAPI, pytest, JSON file persistence, TypeScript, React, Vite, Playwright.

---

## File Structure

- Create `src/opendomainmcp/publish/__init__.py`: exports publish governance helpers.
- Create `src/opendomainmcp/publish/decisions.py`: file-backed store and publish validation logic.
- Modify `src/opendomainmcp/api/mcp_endpoints.py`: enrich endpoint rows and record publish/unpublish decisions.
- Create `tests/test_publish_decisions.py`: unit tests for store and service behavior.
- Modify `tests/test_mcp_endpoints.py`: API contract tests for publish override and decision metadata.
- Modify `web/src/api.ts`: extend MCP endpoint types and publish request helper.
- Modify `web/src/pages/McpBuilder.tsx`: rename to MCP Publish and add readiness/override/history UI.
- Modify `web/tests/helpers/mockApi.ts`: add enriched MCP endpoints and quality evidence defaults.
- Modify `web/tests/mcp_builder.spec.ts`: update publish governance e2e.
- Modify `web/src/App.tsx`: update nav label to MCP Publish.
- Modify `docs/DEVLOG.md` and `docs/TASKS.md`: record Wave 3A.
- Regenerate generated docs HTML with `docs/build.py`.

## Task 1: Add Publish Decision Domain

**Files:**

- Create: `src/opendomainmcp/publish/__init__.py`
- Create: `src/opendomainmcp/publish/decisions.py`
- Create: `tests/test_publish_decisions.py`

- [ ] **Step 1: Write failing store and service tests**

Create `tests/test_publish_decisions.py` with tests:

```python
from pathlib import Path

import pytest

from opendomainmcp.publish.decisions import (
    PublishDecisionStore,
    PublishGateError,
    build_decision,
    require_publish_override,
)


def _evidence(status="ready", score=91):
    return {
        "collection": "default",
        "status": status,
        "score": score,
        "next_action": "Publish evidence is ready.",
        "evidence": [
            {
                "id": "review",
                "gate": "Review",
                "status": status,
                "score": score,
                "summary": "Review gate summary.",
                "details": ["detail"],
                "action": "action",
            }
        ],
    }


def test_decision_store_persists_latest_by_collection_and_view(tmp_path):
    store = PublishDecisionStore(tmp_path)
    decision = build_decision(
        collection="default",
        view="product",
        action="publish",
        endpoint_url="http://testserver/mcp/product",
        evidence=_evidence(),
    )

    store.append(decision)
    reloaded = PublishDecisionStore(tmp_path)

    assert reloaded.latest("default", "product")["id"] == decision["id"]
    assert reloaded.history("default", "product")[0]["action"] == "publish"


def test_ready_publish_does_not_require_override():
    require_publish_override(_evidence("ready"), "")


def test_non_ready_publish_requires_override_reason():
    with pytest.raises(PublishGateError, match="override reason"):
        require_publish_override(_evidence("needs_review"), "")


def test_non_ready_publish_accepts_override_reason():
    require_publish_override(_evidence("needs_review"), "Business owner accepted the risk.")


def test_build_decision_captures_gate_snapshot():
    decision = build_decision(
        collection="default",
        view="operations",
        action="publish",
        endpoint_url="http://testserver/mcp/operations",
        evidence=_evidence("validating", 58),
        override_reason="Temporary internal validation.",
    )

    assert decision["status"] == "published"
    assert decision["readiness_status"] == "validating"
    assert decision["readiness_score"] == 58
    assert decision["override_reason"] == "Temporary internal validation."
    assert decision["gates"][0]["gate"] == "Review"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest tests/test_publish_decisions.py -q
```

Expected: fail because `opendomainmcp.publish.decisions` does not exist.

- [ ] **Step 3: Implement minimal domain module**

Create `src/opendomainmcp/publish/decisions.py`:

```python
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

READY_TO_PUBLISH = {"ready", "published"}


class PublishGateError(ValueError):
    pass


def require_publish_override(evidence: dict, override_reason: str | None) -> None:
    status = str(evidence.get("status") or "blocked")
    if status in READY_TO_PUBLISH:
        return
    if (override_reason or "").strip():
        return
    raise PublishGateError(
        f"Quality evidence is {status}; an override reason is required to publish."
    )


def build_decision(
    *,
    collection: str,
    view: str,
    action: str,
    endpoint_url: str,
    evidence: dict,
    override_reason: str = "",
) -> dict:
    readiness_status = str(evidence.get("status") or "blocked")
    status = "published" if action == "publish" else "unpublished"
    gates = [
        {
            "id": card.get("id", ""),
            "gate": card.get("gate", ""),
            "status": card.get("status", ""),
            "score": int(card.get("score") or 0),
            "summary": card.get("summary", ""),
        }
        for card in evidence.get("evidence", [])
    ]
    return {
        "id": uuid.uuid4().hex,
        "collection": collection,
        "view": view,
        "action": action,
        "status": status,
        "readiness_status": readiness_status,
        "readiness_score": int(evidence.get("score") or 0),
        "gates": gates,
        "override_reason": (override_reason or "").strip(),
        "endpoint_url": endpoint_url,
        "created_at": time.time(),
    }


class PublishDecisionStore:
    def __init__(self, data_dir):
        self._path = Path(data_dir) / "publish_decisions.json"
        self._decisions = self._load()

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return list(data.get("decisions", []))

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps({"decisions": self._decisions}), encoding="utf-8")
        os.replace(tmp, self._path)

    def append(self, decision: dict) -> dict:
        self._decisions.append(decision)
        self._persist()
        return decision

    def history(self, collection: str, view: str) -> list[dict]:
        items = [
            d
            for d in self._decisions
            if d.get("collection") == collection and d.get("view") == view
        ]
        return sorted(items, key=lambda d: d.get("created_at", 0), reverse=True)

    def latest(self, collection: str, view: str) -> dict | None:
        items = self.history(collection, view)
        return items[0] if items else None
```

Create `src/opendomainmcp/publish/__init__.py`:

```python
from .decisions import (
    PublishDecisionStore,
    PublishGateError,
    build_decision,
    require_publish_override,
)

__all__ = [
    "PublishDecisionStore",
    "PublishGateError",
    "build_decision",
    "require_publish_override",
]
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest tests/test_publish_decisions.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/publish/__init__.py src/opendomainmcp/publish/decisions.py tests/test_publish_decisions.py
git commit -m "feat: add publish decision store"
```

## Task 2: Enforce Publish Decisions In MCP Endpoint API

**Files:**

- Modify: `src/opendomainmcp/api/mcp_endpoints.py`
- Modify: `tests/test_mcp_endpoints.py`

- [ ] **Step 1: Write failing API tests**

Extend `tests/test_mcp_endpoints.py` with:

```python
from fastapi.testclient import TestClient

from opendomainmcp.api.app import create_app
from opendomainmcp.context import Context
from opendomainmcp.config import Settings


def _ctx(store, pipeline, fake_graph, tmp_path):
    settings = Settings(data_dir=tmp_path)
    return Context(settings=settings, store=store, pipeline=pipeline, graph=fake_graph)


def test_mcp_publish_requires_override_when_quality_not_ready(store, pipeline, fake_graph, tmp_path):
    client = TestClient(create_app(context=_ctx(store, pipeline, fake_graph, tmp_path)))

    resp = client.post("/api/mcp/endpoints", json={"view": "product"})

    assert resp.status_code == 409
    assert "override reason" in resp.text


def test_mcp_publish_records_override_decision(store, pipeline, fake_graph, tmp_path):
    client = TestClient(create_app(context=_ctx(store, pipeline, fake_graph, tmp_path)))

    resp = client.post(
        "/api/mcp/endpoints",
        json={"view": "product", "override_reason": "Internal pilot only."},
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["published"] is True
    assert payload["status"] == "published"
    assert payload["latest_decision"]["override_reason"] == "Internal pilot only."
    assert payload["history"][0]["action"] == "publish"


def test_mcp_unpublish_records_decision(store, pipeline, fake_graph, tmp_path):
    client = TestClient(create_app(context=_ctx(store, pipeline, fake_graph, tmp_path)))
    client.post(
        "/api/mcp/endpoints",
        json={"view": "product", "override_reason": "Internal pilot only."},
    )

    resp = client.delete("/api/mcp/endpoints/product")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["published"] is False
    assert payload["latest_decision"]["action"] == "unpublish"
    assert payload["history"][0]["status"] == "unpublished"
```

- [ ] **Step 2: Run focused API tests and verify failure**

Run:

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest tests/test_mcp_endpoints.py -q
```

Expected: fail because endpoint payloads do not include decision metadata and publish does not require overrides.

- [ ] **Step 3: Update MCP endpoint routes**

Modify `src/opendomainmcp/api/mcp_endpoints.py`:

- Add `override_reason: str = ""` to `PublishRequest`.
- Import `get_ctx`, `compute_quality_evidence`, and publish helpers.
- Build `PublishDecisionStore(ctx.settings.data_dir)`.
- In `_entry`, include:

```python
"status": "published" if view in published else "unpublished",
"latest_decision": latest,
"history": history,
```

- In `publish_endpoint`, compute evidence, call `require_publish_override`, record a `publish` decision, then return `_entry`.
- Convert `PublishGateError` to `HTTPException(status_code=409, detail=str(exc))`.
- In `unpublish_endpoint`, record an `unpublish` decision using current evidence and return `_entry`.

- [ ] **Step 4: Run focused backend tests**

Run:

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest tests/test_mcp_endpoints.py tests/test_publish_decisions.py tests/test_quality_evidence.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/api/mcp_endpoints.py tests/test_mcp_endpoints.py
git commit -m "feat: record mcp publish decisions"
```

## Task 3: Upgrade MCP Builder Into MCP Publish Workspace

**Files:**

- Modify: `web/src/api.ts`
- Modify: `web/src/pages/McpBuilder.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/tests/helpers/mockApi.ts`
- Modify: `web/tests/mcp_builder.spec.ts`

- [ ] **Step 1: Write failing Playwright test**

Update `web/tests/mcp_builder.spec.ts` to assert:

- heading is `MCP Publish`
- quality evidence next action is visible
- clicking Publish on a non-ready endpoint opens an override reason modal
- submitting reason posts to `/api/mcp/endpoints`
- row flips to published and shows latest decision

- [ ] **Step 2: Run e2e test and verify failure**

Run:

```bash
npm run test:e2e -- tests/mcp_builder.spec.ts
```

Expected: fail because UI still renders `MCP Builder` and has no override modal/history.

- [ ] **Step 3: Extend frontend types and mocks**

In `web/src/api.ts`, add:

```ts
export interface PublishDecision {
  id: string;
  collection: string;
  view: string;
  action: "publish" | "unpublish";
  status: "published" | "unpublished";
  readiness_status: ReadinessStatus;
  readiness_score: number;
  override_reason: string;
  endpoint_url: string;
  created_at: number;
  gates: { id: string; gate: string; status: ReadinessStatus; score: number; summary: string }[];
}
```

Extend `McpEndpoint` with `status`, `latest_decision`, and `history`. Change `publishMcp(view)` to `publishMcp(view, override_reason = "")`.

- [ ] **Step 4: Implement UI**

In `web/src/pages/McpBuilder.tsx`:

- Change title to `MCP Publish`.
- Load `api.qualityEvidence()` alongside views/endpoints/settings.
- Show a compact quality gate section before endpoint rows.
- When publishing and quality status is not `ready` or `published`, open a modal with textarea for override reason.
- POST the reason through `api.publishMcp(endpoint.view, reason)`.
- Show latest decision action/status/override reason in each endpoint row.

- [ ] **Step 5: Run frontend focused checks**

Run:

```bash
npm run test:e2e -- tests/mcp_builder.spec.ts
npm run build
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add web/src/api.ts web/src/pages/McpBuilder.tsx web/src/App.tsx web/tests/helpers/mockApi.ts web/tests/mcp_builder.spec.ts
git commit -m "feat(web): add mcp publish governance"
```

## Task 4: Docs And Full Verification

**Files:**

- Modify: `docs/DEVLOG.md`
- Modify: `docs/TASKS.md`
- Regenerate: `docs/devlog.html`, `docs/tasks.html`, and any generated docs touched by `docs/build.py`.

- [ ] **Step 1: Run backend full tests**

Run:

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest -q
```

Expected: all backend tests pass.

- [ ] **Step 2: Run frontend build and e2e**

Run:

```bash
npm run build
npm run test:e2e
```

Expected: Vite build succeeds and all Playwright specs pass.

- [ ] **Step 3: Update docs**

Add Enterprise Redesign Wave 3A to `docs/DEVLOG.md` and `docs/TASKS.md` with:

- publish decision store
- readiness-gated override publish
- MCP Publish workspace
- verification results

- [ ] **Step 4: Regenerate docs**

Run:

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python docs/build.py
```

Expected: generated HTML updates without errors.

- [ ] **Step 5: Commit docs**

```bash
git add docs/DEVLOG.md docs/TASKS.md docs/*.html
git commit -m "docs: record enterprise wave 3a"
```

## Final Verification

Run after all commits:

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest -q
cd web && npm run build && npm run test:e2e
```

Expected:

- Backend tests pass.
- Frontend build passes.
- Playwright e2e passes.
- `git status -sb` shows a clean branch except ignored dependency directories.
