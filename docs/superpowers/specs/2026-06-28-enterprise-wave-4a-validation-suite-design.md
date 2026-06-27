# Enterprise Wave 4A Validation Suite Design

Date: 2026-06-28

## Context

Wave 1 made OpenDomainMCP workflow-oriented through Command Center and Source
Intake. Wave 2A added backend Quality Evidence and the Quality Lab workspace.
Wave 3A made MCP publish and unpublish actions auditable with readiness gate
snapshots and override reasons.

The remaining gap in the enterprise lifecycle is validation. The Agent
Simulator can run an ad hoc task against an MCP view, and aggregate metrics can
show broad retrieval health, but there is no persistent validation scenario
suite that operators can re-run before publishing. This means a publish decision
can include current quality evidence, but not a clear record that the intended
agent tasks still pass for a specific MCP view.

Wave 4A introduces the first validation-suite slice. It should turn one-off
simulator tasks into reusable validation scenarios and make the latest scenario
results visible in Quality Evidence and MCP Publish.

## Goal

Give each knowledge base a lightweight, auditable validation suite for MCP
views. Operators should be able to save representative tasks, run them against a
view, see pass/fail outcomes, and use the latest validation summary as a publish
readiness signal.

## Non-Goals

- No external queue replacement or scheduler.
- No automatic nightly validation.
- No LLM-as-judge scoring.
- No database migration or MariaDB dependency for validation records.
- No full scenario authoring workflow with ownership, tags, versioning, or
  approvals.
- No changes to the FastMCP SSE transport.

## Options Considered

### Option A: File-Backed Validation Scenarios And Runs

Add a small validation domain under `src/opendomainmcp/validation/` that persists
scenarios and run records in `settings.data_dir / "validation_runs.json"`. The
API exposes scenario listing, scenario execution, and summary endpoints. The
execution path reuses the existing simulator service behavior, then records a
compact result.

Trade-off: this is pragmatic and consistent with existing file-backed tasks,
metrics, and publish decisions. It is not distributed, but it creates an
enterprise workflow and audit shape now.

### Option B: Job-Backed Validation Runs

Make every validation scenario run a Task Center job. Persist richer progress
and cancellation state through the task store.

Trade-off: this aligns with the final job reliability direction, but it couples
the first validation slice to Task Center semantics before the scenario model is
proven.

### Option C: Frontend-Only Saved Simulator Inputs

Store saved simulator prompts in local browser storage and render pass/fail in
the Simulator page only.

Trade-off: fastest UI value, but not auditable, not shareable across operators,
and not usable by Quality Evidence or publish governance.

## Recommendation

Use Option A.

This is the smallest backend-authoritative slice that closes the validate ->
publish loop. It keeps validation records local-demo friendly, gives Quality
Evidence a real simulation gate, and leaves room to move execution into Task
Center later without changing the scenario/run contract.

## Backend Design

### Validation Domain

Add `src/opendomainmcp/validation/` with:

- `ValidationStore(data_dir)`: reads and writes validation records atomically.
- `build_scenario(collection, view, name, query)`: creates a reusable scenario.
- `build_run(collection, scenario, result)`: captures simulator output as a run.
- `summarize_validation(collection, view=None)`: computes pass/fail totals and
  latest run status.

The initial pass/fail rule is deliberately deterministic:

- A run passes when `grounding.hits > 0` and at least one tool result exists.
- A run fails when there is no grounding, no tool result, or the simulator raises
  an error.

This avoids introducing subjective scoring while still detecting the most
important enterprise risk: a published view that cannot ground the intended
agent task.

### Record Shape

The persisted file should be shaped as:

```json
{
  "scenarios": [
    {
      "id": "hex",
      "collection": "domain_knowledge",
      "view": "product",
      "name": "Rollback guidance",
      "query": "How do I roll back a failed deployment?",
      "created_at": 0.0
    }
  ],
  "runs": [
    {
      "id": "hex",
      "scenario_id": "hex",
      "collection": "domain_knowledge",
      "view": "product",
      "query": "How do I roll back a failed deployment?",
      "status": "passed",
      "grounding_hits": 3,
      "avg_score": 0.82,
      "tool_results": 3,
      "knowledge_types": ["Workflow"],
      "error": "",
      "created_at": 0.0
    }
  ]
}
```

Records are scoped by `collection` and `view`. Listing endpoints must never show
another collection's scenarios or runs.

### API

Add a focused router, likely `src/opendomainmcp/api/validation_routes.py`.

Endpoints:

- `GET /api/validation/scenarios?view=product`
  - Lists scenarios for the active collection, optionally filtered by view.
- `POST /api/validation/scenarios`
  - Body: `{ "view": "product", "name": "...", "query": "..." }`
  - Creates a scenario for the active collection.
- `POST /api/validation/scenarios/{id}/run`
  - Runs the scenario against its view using the existing simulator execution
    path and records a run.
- `POST /api/validation/run`
  - Body: `{ "view": "product", "query": "...", "name": "optional" }`
  - Convenience endpoint for "run and optionally save" from Simulator.
- `GET /api/validation/summary?view=product`
  - Returns latest status and pass/fail counts for the active collection/view.

Unknown view names return 404. Unknown scenario ids return 404. Blank name or
query returns 422.

### Simulator Reuse

The existing `/api/simulate` route should not duplicate validation logic.
Implementation should extract its core execution into a small helper that both
`/api/simulate` and validation routes can call. This keeps ad hoc simulation and
saved validation consistent.

## Quality Evidence Integration

Add a seventh evidence card:

- `id`: `simulation`
- `gate`: `Simulation`
- `status`:
  - `validating` when no scenario has been run.
  - `blocked` when the latest run for any scenario in scope failed.
  - `ready` when every latest scenario run passed.
- `score`:
  - `0` for no runs or any failed latest run.
  - pass rate percentage across latest scenario runs otherwise.
- `summary`: concise count of scenarios and latest pass/fail state.
- `action`: directs the operator to run validation scenarios or inspect failed
  scenarios.

This gate should use zero-filled summaries when no validation file exists. A
corrupt validation file should fail loudly in the API, matching metrics and
publish decisions rather than silently losing audit data.

## Frontend Design

### Agent Simulator

Keep the current first-screen workflow: task input, MCP view selector, and run
button. Add validation controls after a simulation result:

- Scenario name input.
- `Save Scenario` action.
- `Run Saved Scenario` action for listed scenarios.
- Latest run status badge per scenario.

The page remains a tool surface, not a marketing page. It should use existing
cards, badges, buttons, and form controls.

### Quality Lab

Quality Lab should render the new Simulation evidence card alongside the
existing gates. No special-case layout is needed; the evidence API contract
should drive the UI.

### MCP Publish

MCP Publish should show a compact validation summary for each endpoint row:

- latest validation status
- passed/failed scenario counts
- last run time when available

Publishing is not blocked by validation in Wave 4A beyond the existing Quality
Evidence gate. If validation is not ready, Wave 3A's override reason behavior
already applies through Quality Evidence status.

## Error Handling

- Validation storage read/write errors fail the validation API request.
- Simulator execution errors are recorded as failed validation runs when the
  request is specifically a validation run.
- Ad hoc `/api/simulate` keeps its current fail-loud behavior.
- Corrupt validation JSON raises a clear error naming the file and line or
  section.
- Metrics recording remains best-effort and must not affect validation result
  persistence.

## Testing Strategy

Backend:

- Unit tests for `ValidationStore` persistence, collection/view filtering,
  latest-run summary, corrupt-file behavior, and pass/fail rules.
- API tests for create/list/run/summary and unknown view/scenario errors.
- Quality Evidence tests for no scenarios, passing latest runs, and failed
  latest runs.

Frontend:

- Playwright test for Simulator saving a scenario and showing a latest run
  status.
- Existing Quality Lab test updated to expect the Simulation gate.
- MCP Publish test updated to render endpoint validation summary.

Full verification:

- `PYTHONPATH=src .venv/bin/python -m pytest -q`
- `npm run build`
- `npm run test:e2e`

## Acceptance Criteria

- Operators can save a simulator task as a validation scenario for a specific
  MCP view.
- Operators can run saved scenarios and see pass/fail status.
- Validation records are persisted under the active data directory and scoped by
  collection/view.
- Quality Evidence includes a Simulation gate based on latest scenario runs.
- MCP Publish exposes validation status next to endpoint publish state.
- Existing ad hoc simulator, publish decision, and quality evidence behavior
  remain backward compatible.

## Rollout Notes

Wave 4A is safe for local and production-like deployments because it is additive
and file-backed. Existing deployments start with no scenarios and therefore see
a `validating` Simulation gate until operators run at least one scenario. This is
intentional: validation should become a visible missing step, but Wave 3A
override reasons still allow controlled publication before full validation is in
place.
