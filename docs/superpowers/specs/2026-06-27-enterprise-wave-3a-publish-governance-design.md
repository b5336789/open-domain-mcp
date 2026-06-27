# Enterprise Wave 3A Publish Governance Design

## Context

Wave 1 made the console workflow-oriented through Command Center and Source Intake. Wave 2A made quality evidence visible through `/api/quality/evidence` and the Quality Lab workspace. The remaining enterprise gap is that MCP endpoint publication is still a transient toggle: it marks an endpoint as published in memory, but it does not record who or why a view was published, what readiness evidence existed at the time, or whether an override was used.

Wave 3A is the first publish-governance slice. It should make MCP publication auditable without replacing the existing FastMCP HTTP mounts or overbuilding the final approval workflow.

## Goal

Turn MCP publish/unpublish actions into durable publish decisions with readiness gate snapshots, optional override reasons, and a Publish workspace that explains the current endpoint state.

## Scope

In scope:

- Add a backend publish governance service with file-backed decision persistence under the active data directory.
- Record publish and unpublish decisions for each `(collection, view)`.
- Attach current Quality Evidence gate snapshots to every decision.
- Require an override reason when publishing while quality evidence is not `ready` or `published`.
- Extend MCP endpoint API responses with publish decision metadata.
- Upgrade the frontend MCP Builder into an MCP Publish workspace while keeping the existing route `/mcp`.
- Add backend unit/API tests and Playwright coverage for blocked publish, override publish, and publish history display.
- Update docs and generated docs HTML.

Out of scope:

- Replacing FastMCP SSE mounting.
- Adding a human approval workflow or external identity provider.
- Enforcing RBAC beyond the existing API key/view-scope layer.
- Endpoint version routing or multiple live versions per view.
- Queue replacement or multi-process job coordination.
- Storing large evidence payloads outside the existing data directory.

## Design Options

### Option A: File-backed publish decisions integrated into existing MCP endpoint routes

Keep the existing `/api/mcp/endpoints` contract, but add a small publish service that persists decision records and enriches endpoint rows with latest decision metadata. Publish requests include `override_reason`; the service computes Quality Evidence and blocks low-quality publish attempts unless a reason is provided.

Trade-off: this is pragmatic and aligns with current file-backed tasks. It is not a distributed approval system, but it creates an audit record now.

### Option B: New database-backed publish domain

Create a MariaDB-backed publish table and migration-like schema management next to graph storage.

Trade-off: stronger enterprise posture, but it couples publish governance to MariaDB availability and delays local dashboard/demo workflows that Wave 2A just hardened.

### Option C: Frontend-only publish checklist

Render readiness gates next to the existing Publish button and prompt for a reason in the browser only.

Trade-off: fast UI value, but decisions remain unaudited and can be bypassed by direct API calls.

## Chosen Approach

Use Option A. It keeps the backend authoritative, preserves local and testability benefits, and creates a path to a database-backed implementation later if deployment scale requires it.

## Backend Design

Create `src/opendomainmcp/publish/decisions.py`.

Core API:

- publish decision dictionaries with `id`, `collection`, `view`, `action`, `status`, `readiness_status`, `readiness_score`, `gates`, `override_reason`, `endpoint_url`, and `created_at`.
- `PublishDecisionStore(data_dir)`: reads/writes `publish_decisions.json` atomically under `settings.data_dir`.
- `require_publish_override(evidence, override_reason)`: validates low-quality publish attempts.
- `build_decision(...)`: captures a compact evidence snapshot for audit history.

Decision actions:

- `publish`
- `unpublish`

Endpoint status:

- `published`
- `unpublished`

Publish rules:

- If Quality Evidence status is `ready` or `published`, publishing succeeds without override.
- If status is `blocked`, `needs_review`, or `validating`, publishing fails with HTTP 409 unless `override_reason` is a non-empty string.
- Every successful publish records a decision with the current evidence card summaries.
- Unpublish always succeeds and records an `unpublish` decision.

`/api/mcp/endpoints` stays the frontend entry point:

- `GET /api/mcp/endpoints` returns existing endpoint fields plus `status`, `latest_decision`, and `history`.
- `POST /api/mcp/endpoints` accepts `{ "view": "...", "override_reason": "..." }`.
- `DELETE /api/mcp/endpoints/{view}` records an unpublish decision and returns the updated row.

## Frontend Design

Keep the route `/mcp`, but change the workspace title from `MCP Builder` to `MCP Publish`.

The page should show:

- Retrieval policy controls, unchanged.
- Quality gate summary using `/api/quality/evidence`.
- Endpoint rows with status, URL, latest decision, and publish history.
- A publish modal when a view is not ready, requiring an override reason.
- Existing copy endpoint URL and local stdio snippets.

The UI should not block ready publishes with unnecessary forms. It should only require an override reason when the backend would require one.

## Error Handling

- Unknown views return 404.
- Low-quality publish without an override returns 409 and a clear message.
- Corrupt publish decision files degrade to a failed API response rather than silently losing audit data.
- Frontend publish errors use the existing toast pattern.
- Empty history renders as "No publish decisions yet."

## Testing

Backend:

- Unit test that publish-ready evidence records a publish decision.
- Unit test that non-ready evidence without override is rejected.
- Unit test that non-ready evidence with override records the override reason.
- Unit test that unpublish records a decision.
- API test for enriched `/api/mcp/endpoints` response.

Frontend:

- E2E test that `/mcp` renders as `MCP Publish`.
- E2E test that a non-ready publish opens an override flow and posts the reason.
- E2E test that endpoint history renders latest decision metadata.

## Acceptance Criteria

- MCP publish decisions are durable in `settings.data_dir / "publish_decisions.json"`.
- Publishing with non-ready Quality Evidence requires an override reason.
- Endpoint list includes latest publish decision and history.
- MCP Publish workspace exposes readiness gates and decision history.
- Existing MCP endpoint publish/unpublish behavior remains available.
- Backend tests, frontend build, Playwright e2e, and local smoke pass.
