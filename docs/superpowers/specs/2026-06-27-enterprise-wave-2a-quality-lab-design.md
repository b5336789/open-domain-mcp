# Enterprise Wave 2A Quality Lab Design

## Context

Wave 1 made the product understandable through Command Center, Source Intake, and the first readiness summary. The enterprise redesign blueprint names Wave 2 as "Quality Lab And Readiness Gates." This spec narrows the first Wave 2 slice to a shippable increment that creates measurable quality evidence without introducing publish governance yet.

## Goal

Make knowledge quality visible as evidence that can drive future MCP publish gates.

## Scope

In scope:

- Add a backend Quality Evidence service that converts readiness, source, review, job, graph, retrieval, and article signals into a stable API contract.
- Extend readiness output with article and retrieval health summaries.
- Add a `Quality Lab` workspace that shows evidence cards, gate status, score, and recommended next actions.
- Improve `Knowledge Review` by adding an article-curation panel next to the review queue so operators can review extracted knowledge and synthesized articles in one place.
- Add backend unit tests and frontend e2e tests for the new contracts and workspace.

Out of scope:

- Enforcing publish blockers in MCP Builder.
- Persisting publish decisions, override reasons, or endpoint versions.
- Building a full simulator scenario suite.
- Replacing the background task queue.

## Design Options

### Option A: Evidence API plus focused Quality Lab

Build a small `QualityEvidence` model and `/api/quality/evidence` endpoint. The frontend adds a Quality Lab page that reads this endpoint and shows evidence grouped by gate. Review gets a lightweight article-curation panel using the existing articles API.

Trade-off: this produces visible value quickly, but evidence is computed on demand rather than persisted.

### Option B: Full persisted evidence store

Create persistent evidence records now and write them from search, ask, synthesis, evals, simulator, and review actions.

Trade-off: this is closer to the final publish-governance model, but it touches many flows and creates migration/storage decisions before the UI proves the workflow.

### Option C: Frontend-only Quality Lab

Build Quality Lab by combining existing readiness, metrics, articles, and graph endpoints in the browser.

Trade-off: this is fast, but it duplicates quality rules in the UI and leaves no backend evidence contract for publish governance.

## Chosen Approach

Use Option A. It keeps the backend as the source of quality truth, keeps the frontend simple, and leaves persistence for Wave 3 when publish decisions need audit records.

## Backend Contract

Add `src/opendomainmcp/quality/evidence.py` with:

- `compute_quality_evidence(ctx, tasks=None, metrics=None) -> dict`
- `QualityEvidence` records shaped as dictionaries for now, matching existing API style.

The response:

```json
{
  "collection": "default",
  "status": "needs_review",
  "score": 72,
  "next_action": "Review pending knowledge objects.",
  "evidence": [
    {
      "id": "review",
      "gate": "Review",
      "status": "needs_review",
      "score": 80,
      "summary": "48 of 60 knowledge objects are approved.",
      "details": ["10 pending", "2 rejected"],
      "action": "Review pending knowledge objects."
    }
  ]
}
```

Statuses use the readiness vocabulary: `blocked`, `needs_review`, `validating`, `ready`, `published`.

## Readiness Extensions

Extend `compute_readiness` with:

- `article_health`: `articles`, `cross_validated`, `avg_relevance`
- `retrieval_health`: `events`, `grounding_hit_rate`, `avg_score`, `retrieval_precision`

These fields must degrade to zero-filled summaries when metrics or article collections are absent.

## Frontend

Add `web/src/pages/QualityLab.tsx` and route `/quality`.

The page shows:

- readiness score and status
- next action
- evidence cards for Coverage, Review, Articles, Retrieval, Graph, and Jobs
- links to Source Intake, Knowledge Review, Articles, Graph, Advisor, Simulator, and Metrics

Update navigation:

- Add `Quality Lab` after `Review`.
- Keep existing `Metrics` page for detailed metrics.

Update `Review`:

- Keep review queue behavior unchanged.
- Add an article curation side panel that lists synthesized articles, relevance, cross-validation state, and source count.
- Provide a `Synthesize articles` action using the existing task creation API.

## Error Handling

- Quality evidence must fail loud for programming errors in the service.
- Graph and article health must degrade to unavailable/zero states when optional sibling collections or graph calls are unavailable.
- Frontend must show an empty evidence state if `/api/quality/evidence` returns no evidence.
- Frontend API failures use the existing toast pattern.

## Testing

Backend:

- Unit tests for readiness article/retrieval health defaults.
- Unit tests for evidence statuses and summaries.
- API contract test for `/api/quality/evidence`.

Frontend:

- E2E test that Quality Lab renders evidence cards and next action with mocked API data.
- E2E test that Review renders the article-curation panel and queues synthesis.
- Smoke test nav includes Quality Lab.

## Acceptance Criteria

- `/api/workspace/readiness` includes `article_health` and `retrieval_health`.
- `/api/quality/evidence` returns a stable, typed quality-evidence payload.
- Quality Lab is reachable from navigation and shows the mocked evidence contract in e2e.
- Review continues to support approve/reject/manual add and now also shows article curation.
- Backend pytest, frontend build, and e2e pass.
