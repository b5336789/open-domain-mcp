# Enterprise Wave 2A Quality Lab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first Wave 2 quality evidence slice: backend evidence contract, readiness health extensions, Quality Lab UI, and Review article curation.

**Architecture:** Backend quality logic remains under `src/opendomainmcp/quality/` and HTTP wiring lives in a focused `api/quality_routes.py`. Frontend consumes a typed `QualityEvidenceResponse` through `web/src/api.ts`, renders a new `QualityLab` page, and extends `Review` without changing the existing approve/reject flow.

**Tech Stack:** Python 3.11, FastAPI, pytest, TypeScript, React, Vite, Playwright.

---

## File Structure

- Create `src/opendomainmcp/quality/evidence.py`: computes evidence cards from readiness, metrics, and optional articles.
- Create `src/opendomainmcp/api/quality_routes.py`: exposes `/api/quality/evidence`.
- Modify `src/opendomainmcp/quality/readiness.py`: adds `article_health` and `retrieval_health`.
- Modify `src/opendomainmcp/quality/__init__.py`: exports evidence helpers.
- Modify `src/opendomainmcp/api/app.py`: includes quality routes.
- Modify `tests/test_workspace_readiness.py`: verifies extended readiness contract.
- Create `tests/test_quality_evidence.py`: verifies evidence service and API contract.
- Modify `web/src/api.ts`: adds evidence types and API client.
- Create `web/src/pages/QualityLab.tsx`: renders evidence workspace.
- Modify `web/src/main.tsx`: adds route.
- Modify `web/src/App.tsx`: adds nav link.
- Modify `web/src/pages/Review.tsx`: adds article curation panel.
- Modify `web/tests/helpers/mockApi.ts`: mocks quality evidence and article task responses.
- Modify `web/tests/smoke.spec.ts`: expects Quality Lab nav.
- Create `web/tests/quality_lab.spec.ts`: tests Quality Lab.
- Modify `web/tests/source_intake.spec.ts` only if shared mocks require adjustment.

## Task 1: Extend Readiness Health

**Files:**

- Modify: `src/opendomainmcp/quality/readiness.py`
- Modify: `tests/test_workspace_readiness.py`

- [ ] **Step 1: Write failing readiness contract tests**

Add expectations that `EXPECTED_KEYS` contains `article_health` and `retrieval_health`, and assert zero-filled defaults in `test_empty_collection_is_blocked`.

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest tests/test_workspace_readiness.py::test_empty_collection_is_blocked -q
```

Expected: fail because readiness does not yet include the new keys.

- [ ] **Step 3: Implement minimal readiness fields**

Add `_article_health(ctx)` and `_retrieval_health(ctx)` helpers. They return zero-filled dictionaries if optional data is absent.

- [ ] **Step 4: Run focused readiness tests**

Run:

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest tests/test_workspace_readiness.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/quality/readiness.py tests/test_workspace_readiness.py
git commit -m "feat: extend readiness quality health"
```

## Task 2: Add Quality Evidence Service And API

**Files:**

- Create: `src/opendomainmcp/quality/evidence.py`
- Create: `src/opendomainmcp/api/quality_routes.py`
- Modify: `src/opendomainmcp/quality/__init__.py`
- Modify: `src/opendomainmcp/api/app.py`
- Create: `tests/test_quality_evidence.py`

- [ ] **Step 1: Write failing evidence service tests**

Create tests that build fake contexts, call `compute_quality_evidence`, and assert Coverage, Review, Articles, Retrieval, Graph, and Jobs cards.

- [ ] **Step 2: Run service tests and verify they fail**

Run:

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest tests/test_quality_evidence.py -q
```

Expected: import failure because `opendomainmcp.quality.evidence` does not exist.

- [ ] **Step 3: Implement evidence service**

Use `compute_readiness(ctx, tasks)` as the base. Build cards from readiness fields only; do not create persistence in this wave.

- [ ] **Step 4: Add route and API contract test**

Add router prefix `/api/quality` with `/evidence`. Add a TestClient check that the endpoint returns `collection`, `status`, `score`, `next_action`, and `evidence`.

- [ ] **Step 5: Run focused backend tests**

Run:

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest tests/test_workspace_readiness.py tests/test_quality_evidence.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add src/opendomainmcp/quality/evidence.py src/opendomainmcp/api/quality_routes.py src/opendomainmcp/quality/__init__.py src/opendomainmcp/api/app.py tests/test_quality_evidence.py
git commit -m "feat: expose quality evidence"
```

## Task 3: Add Quality Lab Frontend

**Files:**

- Modify: `web/src/api.ts`
- Create: `web/src/pages/QualityLab.tsx`
- Modify: `web/src/main.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/tests/helpers/mockApi.ts`
- Modify: `web/tests/smoke.spec.ts`
- Create: `web/tests/quality_lab.spec.ts`

- [ ] **Step 1: Write failing e2e tests**

Add tests that visit `/#/quality`, assert the `Quality Lab` heading, readiness score, next action, and evidence cards.

- [ ] **Step 2: Run e2e test and verify it fails**

Run:

```bash
npm run test:e2e -- tests/quality_lab.spec.ts
```

Expected: fail because `/quality` route and mock endpoint do not exist.

- [ ] **Step 3: Add API types and mock response**

Add `QualityEvidence`, `QualityEvidenceResponse`, and `api.qualityEvidence()`.

- [ ] **Step 4: Build Quality Lab page and route**

Render the evidence cards with existing `PageHeader`, `Card`, `Badge`, `Button`, and `Skeleton` primitives. Add navigation and route wiring.

- [ ] **Step 5: Run frontend checks**

Run:

```bash
npm run test:e2e -- tests/quality_lab.spec.ts
npm run build
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add web/src/api.ts web/src/pages/QualityLab.tsx web/src/main.tsx web/src/App.tsx web/tests/helpers/mockApi.ts web/tests/smoke.spec.ts web/tests/quality_lab.spec.ts
git commit -m "feat(web): add quality lab workspace"
```

## Task 4: Add Review Article Curation Panel

**Files:**

- Modify: `web/src/pages/Review.tsx`
- Modify: `web/tests/helpers/mockApi.ts`
- Create or modify: `web/tests/review.spec.ts`

- [ ] **Step 1: Write failing e2e test**

Assert `/#/review` renders `Article Curation`, lists mocked articles, and queues synthesis through `POST /api/tasks`.

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
npm run test:e2e -- tests/review.spec.ts
```

Expected: fail because the article curation panel does not exist.

- [ ] **Step 3: Implement article curation panel**

Load articles with `api.articles()`, display title, relevance, cross-validation, source count, and add `Synthesize articles` using `api.createTask("synthesize", {})`.

- [ ] **Step 4: Run frontend focused tests**

Run:

```bash
npm run test:e2e -- tests/review.spec.ts tests/quality_lab.spec.ts
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/Review.tsx web/tests/helpers/mockApi.ts web/tests/review.spec.ts
git commit -m "feat(web): add article curation to review"
```

## Task 5: Full Verification And Docs

**Files:**

- Modify: `docs/DEVLOG.md`
- Modify: `docs/TASKS.md`
- Regenerate: `docs/devlog.html`, `docs/tasks.html` if the docs build script supports it.

- [ ] **Step 1: Run full backend tests**

Run:

```bash
PYTHONPATH=src /Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Run frontend build and e2e**

Run:

```bash
npm run build
npm run test:e2e
```

Expected: build succeeds and all Playwright tests pass.

- [ ] **Step 3: Update docs**

Add a concise Wave 2A entry in `docs/DEVLOG.md` and task status in `docs/TASKS.md`.

- [ ] **Step 4: Regenerate docs if needed**

Run:

```bash
/Users/b5336789/Documents/workspace/open-domain-mcp/.venv/bin/python docs/build.py
```

Expected: generated HTML updates without errors.

- [ ] **Step 5: Final status check**

Run:

```bash
git status -sb
```

Expected: only intended files are modified.

- [ ] **Step 6: Commit**

```bash
git add docs/DEVLOG.md docs/TASKS.md docs/devlog.html docs/tasks.html src/opendomainmcp/api/static
git commit -m "docs: record enterprise wave 2a"
```

## Self-Review

- Spec coverage: The plan covers readiness extensions, evidence API, Quality Lab UI, Review article curation, tests, and docs.
- Placeholder scan: No unresolved placeholder tokens are used as implementation steps.
- Type consistency: Frontend type names align with the backend response names: `QualityEvidence` and `QualityEvidenceResponse`.
