# Review Workflow Enhancements — Design Spec

**Date:** 2026-07-06
**Sub-project:** #3 of the enhancement series (order: #1 → #4 → #2 → #5 → #3)
**Status:** Approved in brainstorming; awaiting implementation plan
**Depends on:** evidence spec (`evidence_status`), consensus spec (`conflicted` tier)

## Problem

The review gate already exists (`ODM_REVIEW_MODE`, `retrieve_approved_only`, Review
page with pending/approved/rejected tabs and per-item approve/reject). Missing for
expert review at corpus scale: an audit trail, batch operations, and risk-ordered
queues. Reviewer roles/RBAC were considered and deferred (single-team usage).

## Design

### Audit log

Append-only record in `<data_dir>/review_audit.db` (stdlib `sqlite3`; deliberately
not MariaDB — review must work when the graph store is unwired):

```
{ ts, item_id, action, actor, note, prev_status, new_status }
```

- approve/reject APIs accept an optional `note` (rejection reason). Accumulated
  rejection reasons become material for tuning extraction prompts later.
- `actor` comes from the API key name when auth is enabled, else `local`.
- New endpoint: `GET /api/items/{id}/history`.

### Batch review

- New endpoint: `POST /api/items/review-batch` — `{ids, action, note}`; single
  transaction; one audit entry per item.
- Review page: checkboxes, select-all-on-page, and filters (source file, trust
  level, knowledge_type, evidence_status) so an expert can filter-then-batch.

### Priority queue

Pending list defaults to risk-ordered, with a deterministic score computed at query
time from metadata (no new storage):

1. `conflicted` (consensus) — highest
2. `evidence_status='unverified'` (traceability)
3. low `confidence`
4. everything else

Optional setting `review_auto_approve_high_trust` (default **off**): rules with
`high` trust and fully verified evidence are auto-approved (with an audit entry
attributed to `auto`), so experts spend time only on doubtful items.

## Testing

- every action (single + batch, approve + reject, auto-approve) writes a queryable
  audit entry with correct actor/note/status transition;
- batch endpoint is transactional;
- priority ordering matches the tier definition;
- auto-approve fires only when the flag is on and only for high-trust + verified.

## Out of scope

- Reviewer roles / RBAC (revisit if multi-team review emerges).
- Assignment/queues per reviewer.
- Additional workflow states (e.g. "changes requested").
