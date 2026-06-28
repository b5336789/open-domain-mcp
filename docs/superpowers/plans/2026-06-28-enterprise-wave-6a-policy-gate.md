# Enterprise Wave 6A Publish Policy Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a publish Policy gate to Quality Evidence so MCP publish decisions capture retrieval-policy and auth posture.

**Architecture:** Add a small backend policy evaluator that converts existing settings into a Quality Evidence card. Reuse the existing evidence array, publish decision snapshot, and generic frontend gate rendering; only refresh frontend evidence after policy saves.

**Tech Stack:** Python 3.11, FastAPI, Pydantic Settings, pytest, React, TypeScript, Vite, Playwright.

---

## File Map

- Create `src/opendomainmcp/quality/policy.py`: owns policy evidence card construction.
- Modify `src/opendomainmcp/quality/evidence.py`: insert Policy gate after Simulation and before Jobs.
- Modify `tests/test_quality_evidence.py`: cover Policy gate order, safe policy, unsafe approved-only setting, and auth posture details.
- Modify `tests/test_mcp_endpoints.py`: assert publish decision gate snapshot includes Policy.
- Modify `web/src/pages/McpBuilder.tsx`: refresh Quality Evidence after saving retrieval policy.
- Modify `web/tests/helpers/mockApi.ts`: add default Policy evidence card.
- Modify `web/tests/quality_lab.spec.ts`: expect the Policy gate.
- Modify `web/tests/mcp_builder.spec.ts`: verify policy save triggers evidence refresh.
- Modify `docs/DEVLOG.md`, `docs/TASKS.md`: record Wave 6A completion and verification.
- Regenerate generated docs HTML with `docs/build.py`.

## Task 1: Backend Policy Evidence

**Files:**
- Create: `src/opendomainmcp/quality/policy.py`
- Modify: `src/opendomainmcp/quality/evidence.py`
- Test: `tests/test_quality_evidence.py`

- [ ] **Step 1: Write failing quality evidence tests**

Add tests to `tests/test_quality_evidence.py`:

```python
def test_quality_evidence_includes_policy_gate_in_publish_order(
    store, pipeline, fake_graph, tmp_path
):
    ctx = _ctx(store, pipeline, fake_graph, tmp_path)

    payload = compute_quality_evidence(ctx, tasks=[])

    assert [card["id"] for card in payload["evidence"]] == [
        "coverage",
        "review",
        "articles",
        "retrieval",
        "graph",
        "simulation",
        "policy",
        "jobs",
    ]
    assert _card(payload, "policy")["gate"] == "Policy"


def test_policy_gate_is_ready_for_approved_only_supported_search_mode(
    store, pipeline, fake_graph, tmp_path
):
    ctx = Context(
        settings=Settings(
            data_dir=tmp_path,
            retrieve_approved_only=True,
            search_mode="hybrid",
            rerank_enabled=True,
        ),
        store=store,
        pipeline=pipeline,
        graph=fake_graph,
    )

    policy = _card(compute_quality_evidence(ctx, tasks=[]), "policy")

    assert policy == {
        "id": "policy",
        "gate": "Policy",
        "status": "ready",
        "score": 100,
        "summary": "Published MCP views use approved-only hybrid retrieval.",
        "details": [
            "approved-only on",
            "search mode hybrid",
            "rerank on",
            "auth disabled",
        ],
        "action": "Policy gate is clear.",
    }


def test_policy_gate_needs_review_when_approved_only_is_disabled(
    store, pipeline, fake_graph, tmp_path
):
    ctx = Context(
        settings=Settings(
            data_dir=tmp_path,
            retrieve_approved_only=False,
            search_mode="hybrid",
        ),
        store=store,
        pipeline=pipeline,
        graph=fake_graph,
    )

    policy = _card(compute_quality_evidence(ctx, tasks=[]), "policy")

    assert policy["status"] == "needs_review"
    assert policy["score"] == 60
    assert policy["summary"] == "Published MCP views may include unapproved knowledge."
    assert policy["action"] == "Enable approved-only retrieval before publishing."


def test_policy_gate_reports_auth_enabled_key_scope(
    store, pipeline, fake_graph, tmp_path
):
    ctx = Context(
        settings=Settings(
            data_dir=tmp_path,
            retrieve_approved_only=True,
            auth_enabled=True,
            api_keys="admin:admin:*,dev:developer:developer|architecture",
        ),
        store=store,
        pipeline=pipeline,
        graph=fake_graph,
    )

    details = _card(compute_quality_evidence(ctx, tasks=[]), "policy")["details"]

    assert "auth enabled" in details
    assert "2 API keys configured" in details
    assert "view scopes configured" in details
```

Run and confirm these fail:

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest tests/test_quality_evidence.py -q
```

- [ ] **Step 2: Implement policy evaluator**

Create `src/opendomainmcp/quality/policy.py`:

```python
from __future__ import annotations

from ..config import Settings

SUPPORTED_SEARCH_MODES = {"hybrid", "vector"}


def build_policy_evidence(settings: Settings) -> dict:
    approved_only = bool(settings.retrieve_approved_only)
    search_mode = str(settings.search_mode or "")
    rerank = bool(settings.rerank_enabled)

    details = [
        "approved-only on" if approved_only else "approved-only off",
        f"search mode {search_mode or 'unset'}",
        "rerank on" if rerank else "rerank off",
        *_auth_details(settings),
    ]

    if not approved_only:
        return {
            "id": "policy",
            "gate": "Policy",
            "status": "needs_review",
            "score": 60,
            "summary": "Published MCP views may include unapproved knowledge.",
            "details": details,
            "action": "Enable approved-only retrieval before publishing.",
        }

    if search_mode not in SUPPORTED_SEARCH_MODES:
        return {
            "id": "policy",
            "gate": "Policy",
            "status": "needs_review",
            "score": 70,
            "summary": f"Search mode {search_mode or 'unset'} is not publish-safe.",
            "details": details,
            "action": "Select hybrid or vector search mode.",
        }

    return {
        "id": "policy",
        "gate": "Policy",
        "status": "ready",
        "score": 100,
        "summary": f"Published MCP views use approved-only {search_mode} retrieval.",
        "details": details,
        "action": "Policy gate is clear.",
    }


def _auth_details(settings: Settings) -> list[str]:
    if not settings.auth_enabled:
        return ["auth disabled"]
    parsed = settings.parsed_api_keys()
    if not parsed:
        return ["auth enabled", "0 API keys configured", "no view scopes configured"]
    scoped = any("*" not in entry["views"] for entry in parsed.values())
    return [
        "auth enabled",
        f"{len(parsed)} API keys configured",
        "view scopes configured" if scoped else "wildcard access only",
    ]
```

Modify `src/opendomainmcp/quality/evidence.py`:

```python
from .policy import build_policy_evidence

...
        _simulation_card(ctx, readiness),
        build_policy_evidence(ctx.settings),
        _jobs_card(readiness),
```

- [ ] **Step 3: Verify backend evidence scope**

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest tests/test_quality_evidence.py -q
```

Commit after green:

```bash
git add src/opendomainmcp/quality/policy.py src/opendomainmcp/quality/evidence.py tests/test_quality_evidence.py
git commit -m "feat: add publish policy evidence gate"
```

## Task 2: Publish Decision Policy Snapshot

**Files:**
- Modify: `tests/test_mcp_endpoints.py`

- [ ] **Step 1: Write failing publish decision test**

Add to `tests/test_mcp_endpoints.py`:

```python
def test_publish_decision_captures_policy_gate(store, pipeline, fake_graph, tmp_path):
    tc, _, _ = _make_client(store, pipeline, fake_graph, tmp_path)

    published = tc.post(
        "/api/mcp/endpoints",
        json={"view": "product", "override_reason": "Internal pilot only."},
    ).json()

    gates = {gate["id"]: gate for gate in published["latest_decision"]["gates"]}
    assert gates["policy"]["gate"] == "Policy"
    assert gates["policy"]["status"] == "needs_review"
    assert gates["policy"]["summary"] == "Published MCP views may include unapproved knowledge."
```

Run and confirm it fails before Task 1 implementation or passes after Task 1:

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest tests/test_mcp_endpoints.py::test_publish_decision_captures_policy_gate -q
```

- [ ] **Step 2: Verify publish scope**

No new production code should be required if Task 1 is correct, because publish decisions already store the full evidence gate snapshot.

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest tests/test_mcp_endpoints.py tests/test_publish_decisions.py -q
```

Commit after green:

```bash
git add tests/test_mcp_endpoints.py
git commit -m "test: capture policy gate in publish decisions"
```

## Task 3: Frontend Policy Gate Refresh

**Files:**
- Modify: `web/src/pages/McpBuilder.tsx`
- Modify: `web/tests/helpers/mockApi.ts`
- Modify: `web/tests/quality_lab.spec.ts`
- Modify: `web/tests/mcp_builder.spec.ts`

- [ ] **Step 1: Write failing frontend tests**

Update `web/tests/helpers/mockApi.ts` default evidence array by inserting:

```ts
{
  id: "policy",
  gate: "Policy",
  status: "needs_review",
  score: 60,
  summary: "Published MCP views may include unapproved knowledge.",
  details: ["approved-only off", "search mode hybrid", "rerank off", "auth disabled"],
  action: "Enable approved-only retrieval before publishing.",
}
```

Update `web/tests/quality_lab.spec.ts` expected gate list to include `"Policy"` after `"Simulation"`.

Update `web/tests/mcp_builder.spec.ts` with a policy-save refresh test:

```ts
test("refreshes publish readiness after saving retrieval policy", async ({ page }) => {
  let evidenceCalls = 0;
  await installApiMocks(page, {
    "GET /api/views": DEFAULT_VIEWS,
    "GET /api/settings": DEFAULT_SETTINGS,
    "GET /api/mcp/endpoints": ENDPOINTS,
    "GET /api/quality/evidence": {
      status: 200,
      json: DEFAULT_QUALITY_EVIDENCE,
    },
    "PATCH /api/settings": { updated: ["retrieve_approved_only"] },
  });
  await page.route("**/api/quality/evidence", async (route) => {
    evidenceCalls += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DEFAULT_QUALITY_EVIDENCE),
    });
  });

  await page.goto("/#/mcp");
  await expect(page.getByRole("heading", { name: "MCP Publish" })).toBeVisible();
  await page.getByRole("button", { name: "Save policy" }).click();
  await expect.poll(() => evidenceCalls).toBeGreaterThan(1);
});
```

If the route override conflicts with the shared mock handler, implement the same assertion with `page.waitForResponse((response) => response.url().endsWith("/api/quality/evidence"))` after the save click.

Run and confirm the refresh test fails before implementation:

```bash
cd web
npm run test:e2e -- tests/quality_lab.spec.ts tests/mcp_builder.spec.ts
```

- [ ] **Step 2: Implement frontend refresh**

Modify `savePolicy()` in `web/src/pages/McpBuilder.tsx`:

```ts
  async function savePolicy() {
    setSaving(true);
    try {
      await api.patchSettings(policy);
      const nextQuality = await api.qualityEvidence();
      setQuality(nextQuality);
      toast.show("Retrieval policy saved", "green");
    } catch (e) {
      toast.show(String(e), "red");
    } finally {
      setSaving(false);
    }
  }
```

- [ ] **Step 3: Verify frontend scope**

```bash
cd web
npm run build
npm run test:e2e -- tests/quality_lab.spec.ts tests/mcp_builder.spec.ts
```

Commit after green:

```bash
git add web/src/pages/McpBuilder.tsx web/tests/helpers/mockApi.ts web/tests/quality_lab.spec.ts web/tests/mcp_builder.spec.ts
git commit -m "feat: refresh policy evidence after settings save"
```

## Task 4: Documentation And Final Verification

**Files:**
- Modify: `docs/DEVLOG.md`
- Modify: `docs/TASKS.md`
- Generated: `docs/*.html`

- [ ] **Step 1: Update docs**

Add Enterprise Wave 6A entries to `docs/DEVLOG.md` and `docs/TASKS.md` with behavior and final verification counts.

Run:

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python docs/build.py
```

- [ ] **Step 2: Run focused verification**

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest tests/test_quality_evidence.py tests/test_mcp_endpoints.py tests/test_publish_decisions.py -q
cd web
npm run test:e2e -- tests/quality_lab.spec.ts tests/mcp_builder.spec.ts
```

- [ ] **Step 3: Run full verification**

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest -q
cd web
npm run build
npm run test:e2e
```

- [ ] **Step 4: Request code review**

Use `superpowers:requesting-code-review` after full verification. Fix any Critical or Important findings before PR.

- [ ] **Step 5: Publish, merge, and redeploy locally**

Push `enterprise-wave-6a-policy-gate`, create a PR, merge it after verification/review, pull `main`, clean up the worktree and branch, rebuild frontend static assets, restart the local demo server at `http://127.0.0.1:8000`, and smoke:

```bash
curl -sS http://127.0.0.1:8000/api/health
curl -sS http://127.0.0.1:8000/api/quality/evidence
curl -sS http://127.0.0.1:8000/api/mcp/endpoints
```
