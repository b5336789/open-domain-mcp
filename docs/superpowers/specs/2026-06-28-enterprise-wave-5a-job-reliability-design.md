# Enterprise Wave 5A Job Reliability Foundation Design

Date: 2026-06-28

## Summary

Wave 5A hardens the existing Task Center into a reliable job domain without replacing the in-process worker or the file-backed store. The goal is to make background work explainable, recoverable, and safe enough to support Source Intake, synthesis, extraction, evals, and publish validation.

This slice intentionally avoids a queue backend migration. The current product risk is not that jobs run in-process; it is that job semantics are not explicit enough for operators to understand what happened, what can be retried, and whether readiness should block.

## Current State

The codebase already has:

- `src/opendomainmcp/tasks/models.py` with a `Task` dataclass.
- `src/opendomainmcp/tasks/store.py` with file-backed `tasks.json` and child-name storage.
- `src/opendomainmcp/tasks/worker.py` with serial execution and basic recovery from `running` to `queued`.
- `src/opendomainmcp/api/task_routes.py` with task create/list/children/cancel/clear routes.
- `web/src/components/TaskCenter.tsx` with a top-right task drawer.
- readiness and quality evidence gates that already count queued/running/error/cancelled jobs.

The gaps are:

- status values are string conventions rather than a validated job contract;
- failed and recovered jobs do not carry structured operator evidence;
- recovery requeues `running` jobs but does not record why or how many times;
- task list rows do not expose retryability or recovery/failure summary consistently;
- Task Center visibility is still too thin for enterprise operators investigating blocked readiness.

## Goals

1. Define a stable JobRun contract around the existing `Task` model.
2. Preserve backwards compatibility with existing `tasks.json` files.
3. Record structured failure and recovery evidence on task rows.
4. Expose retryability and recovery metadata through `/api/tasks`.
5. Improve Task Center UI so blocked/failed/recovered jobs are actionable at a glance.
6. Keep readiness and Quality Evidence behavior compatible while making their job inputs more trustworthy.

## Non-Goals

- Do not introduce Celery, Redis, RQ, Dramatiq, or another queue dependency.
- Do not rewrite async ingest checkpoint endpoints.
- Do not add a full job detail page.
- Do not implement recurring schedules.
- Do not add multi-worker locking. This is a contract/foundation slice, not a distributed execution slice.

## Proposed Approach

### Option A: Contract-First Hardening (Recommended)

Add job constants, status validation, retryability, recovery metadata, and structured result/error evidence to the existing task domain.

Trade-offs:

- Pros: small blast radius, preserves current APIs, improves operator trust immediately, unlocks future queue migration.
- Cons: still single-process execution; does not solve distributed scale.

### Option B: Queue Backend Migration

Replace the worker with a real queue backend and adapt routes/UI around it.

Trade-offs:

- Pros: better long-term scale and multi-process safety.
- Cons: high migration risk, new operational dependency, premature before the job contract is stable.

### Option C: UI-Only Task Center Improvements

Improve Task Center presentation but leave backend semantics unchanged.

Trade-offs:

- Pros: fastest visible change.
- Cons: does not make readiness or recovery more trustworthy.

Wave 5A will use Option A.

## Backend Design

### Job Constants And Validation

`src/opendomainmcp/tasks/models.py` will define:

- `JOB_QUEUED = "queued"`
- `JOB_RUNNING = "running"`
- `JOB_DONE = "done"`
- `JOB_ERROR = "error"`
- `JOB_CANCELLED = "cancelled"`
- `JOB_STATUSES`
- `ACTIVE_STATUSES`
- `TERMINAL_STATUSES`
- `RETRYABLE_TERMINAL_STATUSES`

`Task.from_dict()` will remain lenient for backwards compatibility, but store mutations will validate status updates before persisting. A corrupt or unknown status in persisted JSON should fail loudly on store load because readiness decisions must not silently ignore malformed jobs.

### Task Fields

Extend `Task` with backwards-compatible fields:

- `attempts: int = 0`
- `recovered_at: Optional[float] = None`
- `recovery_count: int = 0`
- `last_transition: Optional[str] = None`
- `error_type: Optional[str] = None`
- `error_message: Optional[str] = None`

Existing `error` remains for backwards compatibility and quick display. New structured fields let the UI and readiness explain failures without parsing `repr(exc)`.

### Store Behavior

`TaskStore` will own state transitions instead of raw callers setting arbitrary status strings.

Add:

- `transition(task_id, status, **fields)`: validates status, timestamps terminal states, records `last_transition`, and persists.
- `mark_recovered(task_id)`: turns stale `running` into `queued`, increments `recovery_count`, clears `cancel_requested`, sets `recovered_at`, and sets `last_transition = "recovered_running_to_queued"`.
- `retry(task_id)`: creates a new queued task with the same type/title/collection/params and result metadata pointing back to the original failed/cancelled task.

Keep `update()` for existing code paths, but route status changes through validation.

### Worker Behavior

`TaskWorker.recover()` will call `store.mark_recovered()` rather than a raw `update()`.

`TaskWorker._run()` will:

- increment attempts when a task starts;
- set `started_at` only when missing or retrying;
- on success, persist `status=done`, `finished_at`, and leave runner-set result intact;
- on cancellation, persist `status=cancelled` and a human-readable result summary;
- on exception, persist `status=error`, `error`, `error_type`, `error_message`, and `finished_at`.

### API Behavior

`src/opendomainmcp/api/task_routes.py` will add:

- `POST /api/tasks/{task_id}/retry`

Retry is allowed only for `error` or `cancelled` tasks. It returns the newly queued task and wakes the worker.

Existing routes remain:

- `POST /api/tasks`
- `GET /api/tasks`
- `GET /api/tasks/{task_id}/children`
- `DELETE /api/tasks/{task_id}`
- `POST /api/tasks/clear`

`GET /api/tasks` will include the new fields automatically through `Task.to_dict()`.

## Frontend Design

Task Center stays as a compact drawer, not a full workspace. It will display:

- status badge;
- progress `done / total`;
- error type/message for failed jobs;
- recovered indicator when `recovery_count > 0`;
- retry button for `error` and `cancelled` jobs;
- cancel button for active jobs;
- clear finished action unchanged.

The retry action will call `api.retryTask(taskId)` and refresh the task list. It should not require the user to navigate away from the current workspace.

## Data Flow

```text
POST /api/tasks
  -> TaskStore.create(status=queued)
  -> TaskWorker.wake()
  -> TaskWorker.transition(running, attempts+1)
  -> runner updates progress/result
  -> TaskWorker.transition(done | error | cancelled)
  -> Readiness/Quality Evidence read job_health from task rows
  -> Task Center shows actionable state

Process restart
  -> TaskWorker.start()
  -> recover()
  -> mark_recovered(running -> queued, recovery_count+1)
  -> rerun stale job with visible recovery evidence

POST /api/tasks/{id}/retry
  -> TaskStore.retry(error|cancelled)
  -> new queued task linked to original
  -> worker wakes
```

## Error Handling

- Unknown task type remains `400`.
- Unknown task id remains `404`.
- Retry on non-terminal or successful jobs returns `409`.
- Store load fails loudly on corrupt JSON or unknown status.
- Task runner exceptions are persisted as structured error evidence; they do not crash the worker loop.
- Metrics/readiness consumers should tolerate the new fields without requiring them.

## Testing Strategy

Backend tests:

- status validation rejects unknown persisted statuses;
- transition records timestamps and last transition;
- recovery records `recovery_count` and `recovered_at`;
- worker records `attempts`, structured errors, and cancellation result;
- retry clones params/collection/type/title and links to original task;
- API retry returns `409` for non-retryable jobs and starts retryable jobs.

Frontend tests:

- Task Center renders failure message and recovered badge;
- retry button posts to `/api/tasks/{id}/retry`;
- active jobs still support cancel;
- existing Source Intake and smoke tests keep passing.

## Documentation

Update:

- `docs/DEVLOG.md`
- `docs/TASKS.md`
- generated docs HTML

Add Wave 5A task entries and final verification counts.

## Rollout Notes

This change is backwards compatible with older task rows because new fields default in `Task.from_dict()`. It intentionally does not migrate old `error` strings into structured `error_type`/`error_message`; only new worker failures will have structured fields.

## Approval

This design follows the already approved enterprise redesign blueprint and the user's instruction to continue with the recommended next slice.
