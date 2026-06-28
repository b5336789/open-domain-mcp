# Enterprise Wave 6A Publish Policy Gate Design

Date: 2026-06-28

## Summary

Wave 6A turns the existing MCP Publish retrieval-policy controls into first-class governance evidence. The product already lets operators edit `search_mode`, `rerank_enabled`, and `retrieve_approved_only`, and it already has API-key view scope primitives. The missing enterprise behavior is that publish readiness does not explain whether those policy settings are safe for a published MCP endpoint.

This slice adds a Policy gate to Quality Evidence and captures policy evidence in publish decisions. It does not add a full RBAC administration UI or change retrieval execution semantics.

## Current State

The codebase already has:

- `src/opendomainmcp/config.py` with editable runtime settings and env-only auth settings.
- `src/opendomainmcp/api/app.py` exposing `GET/PATCH /api/settings`.
- `src/opendomainmcp/api/auth.py` with API-key parsing and view-scope checks.
- `src/opendomainmcp/quality/evidence.py` producing Coverage, Review, Articles, Retrieval, Graph, Simulation, and Jobs gates.
- `src/opendomainmcp/publish/decisions.py` storing gate snapshots in publish decisions.
- `web/src/pages/McpBuilder.tsx` exposing retrieval-policy controls in MCP Publish.

The gap is that policy posture is visible as editable settings but absent from the same evidence model that governs publish decisions.

## Goals

1. Add a stable Policy gate to `/api/quality/evidence`.
2. Evaluate publish policy from existing settings and auth posture.
3. Make publish decisions include the Policy gate snapshot automatically.
4. Display the Policy gate in Quality Lab and MCP Publish through existing evidence cards.
5. Keep the implementation backwards compatible with existing settings and decision records.

## Non-Goals

- Do not add an RBAC management UI.
- Do not require auth to be enabled for local development.
- Do not block publish solely because auth is disabled.
- Do not change retrieval, ranking, or MCP runtime behavior.
- Do not introduce a new database table or migration.

## Proposed Approach

### Option A: Evidence-Only Policy Gate (Recommended)

Add a `policy` evidence card computed from current settings. The gate is ready when approved-only retrieval is enabled and search mode is one of the supported publish-safe modes. It reports auth posture and rerank posture as details. Auth disabled is a warning-level detail, not a blocker, because local/internal deployments may intentionally run without API-key auth.

Trade-offs:

- Pros: small blast radius, immediately improves publish governance, reuses existing evidence and publish decision paths.
- Cons: does not enforce per-view role coverage before publish.

### Option B: Strict Publish Policy Enforcement

Make publish return `409` unless approved-only retrieval and auth are enabled.

Trade-offs:

- Pros: stronger production posture.
- Cons: too disruptive for current local-first workflows and existing tests; it would make env-only deployment policy a product blocker.

### Option C: RBAC Administration UI First

Build UI for API-key role/view management before adding evidence.

Trade-offs:

- Pros: gives operators direct control over auth policy.
- Cons: larger security surface, needs secret-handling design, and does not first establish the evidence contract.

Wave 6A will use Option A.

## Backend Design

### Policy Evidence Model

Add a small policy evaluator in `src/opendomainmcp/quality/policy.py`.

Inputs:

- `ctx.settings.search_mode`
- `ctx.settings.rerank_enabled`
- `ctx.settings.retrieve_approved_only`
- `ctx.settings.auth_enabled`
- `ctx.settings.api_keys`

Output:

```python
{
    "id": "policy",
    "gate": "Policy",
    "status": "ready" | "needs_review",
    "score": int,
    "summary": str,
    "details": list[str],
    "action": str,
}
```

Rules:

- `retrieve_approved_only=False` is `needs_review` with score `60`, because published MCP views may expose unreviewed knowledge.
- Unsupported `search_mode` is `needs_review` with score `70`, even though settings validation should normally prevent it.
- `retrieve_approved_only=True` and supported search mode are `ready`.
- `rerank_enabled=True` improves precision detail but is not required.
- `auth_enabled=False` appears as a detail and keeps the gate ready for local/internal deployments.
- `auth_enabled=True` reports the number of configured API keys and whether at least one wildcard or scoped key exists.

### Quality Evidence Integration

`compute_quality_evidence()` will include the Policy gate after Simulation and before Jobs:

```text
Coverage, Review, Articles, Retrieval, Graph, Simulation, Policy, Jobs
```

This ordering keeps policy close to publish-specific evidence and keeps Jobs as the operational tail gate.

### Publish Decision Snapshot

`build_decision()` already stores all evidence gates passed to publish. Once Quality Evidence includes Policy, publish decisions automatically capture the policy snapshot. Existing decision history remains readable because old records simply lack a Policy gate.

### API Compatibility

No new endpoint is required. Existing endpoints continue to work:

- `GET /api/quality/evidence`
- `GET /api/mcp/endpoints`
- `POST /api/mcp/endpoints`
- `PATCH /api/settings`

## Frontend Design

Quality Lab and MCP Publish already render evidence arrays generically, so the Policy gate will appear without new page layout work.

Wave 6A will make one small MCP Publish improvement: after saving retrieval policy, refresh Quality Evidence so the Policy gate updates immediately without a page reload.

The MCP Publish policy panel remains focused on:

- Approved knowledge only
- Cross-encoder re-ranking
- Search mode

No new visible auth-secret UI is added.

## Data Flow

```text
PATCH /api/settings
  -> save editable retrieval policy
  -> frontend refreshes /api/quality/evidence
  -> Policy gate reflects new settings

POST /api/mcp/endpoints
  -> compute_quality_evidence()
  -> includes Policy gate
  -> publish decision stores gates snapshot
```

## Error Handling

- Invalid setting values continue to be handled by existing settings validation.
- If policy evaluation receives an unexpected search mode, it returns `needs_review` rather than raising.
- Existing publish override behavior remains unchanged: non-ready evidence can still publish with an override reason.

## Testing Strategy

Backend tests:

- policy gate is ready when approved-only retrieval is enabled;
- policy gate is needs_review when approved-only retrieval is disabled;
- policy gate reports auth enabled with configured key count;
- publish decisions capture the Policy gate snapshot;
- quality evidence ordering includes Policy before Jobs.

Frontend tests:

- Quality Lab renders the Policy gate from mocked evidence;
- MCP Publish refreshes publish readiness after saving policy.

## Documentation

Update:

- `docs/DEVLOG.md`
- `docs/TASKS.md`
- generated docs HTML

Add Wave 6A task entries and final verification counts.

## Rollout Notes

This is backwards compatible with existing settings files and publish decision history. It changes readiness scoring because Quality Evidence will average one additional gate. That is acceptable because the score is already secondary to explainable blockers, warnings, and actions.

## Approval

This design follows the approved enterprise redesign blueprint and the user's instruction to continue with the recommended next slice.
