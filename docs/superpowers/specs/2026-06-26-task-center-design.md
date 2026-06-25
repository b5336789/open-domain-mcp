# Task Center — Design Spec

Date: 2026-06-26
Status: Approved (design); pending implementation plan

## 1. Goal

A centralized **Task Center** for long-running background work (ingest,
synthesize, and a new standalone re-extract). A button in a new global top-right
bar opens a slide-over panel that lists in-progress tasks (plus recent history),
shows per-task progress, lets the user cancel tasks, and **persists across page
reloads** because task state lives server-side. Creating an ingest task
enumerates the source's files into per-file child entries up front, so the work
is durable the moment it is queued.

## 2. Decisions (locked)

- **Granularity:** hybrid — a parent task with child entries (files for ingest,
  topics for synthesize, source files for extract).
- **Task types:** `ingest`, `synthesize`, `extract` (standalone re-extract).
- **History:** keep finished tasks as recent history with a "Clear finished"
  action; cap at **100,000** tasks (drop oldest finished beyond the cap).
- **Execution:** a single **serial queue** — one task runs at a time.
- **Cancellation:** parent-level. The in-flight child finishes; remaining
  children/queue stop; status becomes `cancelled`. A still-`queued` task cancels
  by removing it from the queue.
- **Scope of store:** global (across collections). Each task records its
  `collection` and the panel shows all tasks with a collection badge.

## 3. Scalability constraints (huge codebases — up to ~100k files)

These shape the persistence design and are first-class requirements:

1. **Two-layer storage.** A lightweight **index** holds task *summaries* only
   (id, type, title, collection, status, `done`/`total` counts, timestamps,
   error). Even a 100k-file ingest is one summary row (`done: 12345 / total:
   100000`). `GET /api/tasks` returns summaries only, so polling is always cheap.
2. **Lazy, paginated children.** Child detail is stored separately and fetched
   on demand via `GET /api/tasks/{id}/children?offset=&limit=` only when the user
   expands a task. Children are never included in the polled list payload.
3. **Throttled writes.** Progress persistence (checkpoint + task counts) is
   throttled to **at most once per 2 seconds or every 100 children (whichever
   comes first)**, not once per file. This bounds disk IO to ~O(n) instead of the
   O(n²) caused by rewriting a growing file after every item. A crash loses at
   most the last batch, which the checkpoint resumes cheaply.
4. **Reuse the checkpoint for ingest children.** Ingest child state (which files
   are done) is derived from the existing `ingest/checkpoint.py`
   `completed_files`, not duplicated into the task store.

## 4. Data model

### 4.1 Task summary (in the index)

```
TaskSummary {
  id: str                     # uuid hex
  type: "ingest" | "synthesize" | "extract"
  title: str                  # e.g. "Ingest /repo", "Synthesize articles", "Re-extract billing.py"
  collection: str
  status: "queued" | "running" | "done" | "error" | "cancelled"
  created_at: float           # epoch seconds (server time.time())
  started_at: float | None
  finished_at: float | None
  total: int                  # number of children (files / topics / chunks-groups)
  done: int                   # children completed
  cancel_requested: bool
  error: str | None
  result: dict | None         # compact report summary (e.g. files_indexed, stored)
  params: dict                # {path, sync} | {limit, dry_run} | {source}
}
```

### 4.2 Child entry (in the per-task children store / checkpoint)

```
Child { name: str, status: "pending" | "running" | "done" | "error" | "skipped" }
```

For `ingest`, children = the source's files; their done/skipped/error status is
derived from the checkpoint + report. For `synthesize`, children = gated topics.
For `extract`, children = the source files (or chunks-by-source) being re-extracted.

## 5. Backend components

### 5.1 `tasks/store.py` — `TaskStore`

- Persists the index atomically to `<data_dir>/tasks.json` (temp + `os.replace`).
- Guards all mutations with a `threading.Lock`.
- API: `create(type, params, title, collection) -> TaskSummary`,
  `list() -> [TaskSummary]` (newest first; running/queued first), `get(id)`,
  `update(id, **fields)` (throttle-aware for progress), `next_queued()`,
  `request_cancel(id)`, `clear_finished()`, enforce the 100k cap on write.
- Children: per-task children file `<data_dir>/.tasks/<id>.children.json`
  (paginated reads). For ingest, children are read from the checkpoint instead.

### 5.2 `tasks/worker.py` — serial worker

- One daemon thread started from `create_app` (idempotent; guarded so tests that
  build multiple apps don't spawn duplicates, or the worker is per-app-instance
  and stopped on shutdown).
- Loop: take the oldest `queued` task → mark `running` → dispatch to the runner
  for its type → mark terminal status → repeat; idle-wait when the queue is empty.
- Cooperative cancellation: runners check `cancel_requested` between children.
- Startup recovery: any task left `running` (process crash) is re-queued; ingest
  resumes via its checkpoint.

### 5.3 Runners

- **ingest runner:** walk the path → write file children (pending) → run
  `pipeline.ingest_path(path, progress, sync, checkpoint=cp)`; the progress
  callback updates child statuses + throttled counts. Reuses the path-keyed
  checkpoint for resume.
- **synthesize runner:** run `synthesize_articles(..., on_event=...)`; topics
  become children; per-topic events update progress.
- **extract runner:** for the given `source`, page its existing chunks from the
  store, re-run the extractor per chunk, and update stored knowledge/metadata +
  graph. **Does not recompute dense vectors** (the new summary won't affect the
  embedding unless the source is re-ingested) — surfaced in the UI.

### 5.4 API (`api/app.py` or a new `api/task_routes.py`)

- `POST /api/tasks` `{type, params}` → enqueue, return the `TaskSummary`.
- `GET /api/tasks` → `{tasks: [TaskSummary]}` (summaries only).
- `GET /api/tasks/{id}/children?offset=&limit=` → `{children: [Child], total}`.
- `DELETE /api/tasks/{id}` → cancel (queued → removed/cancelled; running →
  `cancel_requested`).
- `POST /api/tasks/clear` → remove finished (done/cancelled/error).

### 5.5 Relationship to existing endpoints

The Task Center is the unified front door for background work. The previous PR's
`/api/ingest/async` + `/api/ingest/jobs/{id}` polling UI is **folded into** the
task system (the `checkpoint.py` module is retained and reused). The in-page live
**SSE** flows (`/api/ingest/stream`, `/api/synthesize/stream`) remain unchanged
for users who want to watch a run inline; the persistent, cancelable, centralized
path goes through the Task Center.

## 6. Frontend

- **Global top-right bar:** a new fixed bar (desktop) / addition to the mobile
  top bar containing a Task Center button with a badge showing the active
  (queued + running) count.
- **Slide-over panel** (right drawer): task cards with type icon, title,
  collection badge, progress bar (`done/total`), status, a cancel button, and an
  expand control that lazily loads paginated children. A "Clear finished" action
  at the top/bottom.
- **Polling hook:** fetch `/api/tasks` every ~1.5s while the panel is open or any
  task is active; stop when idle and closed.
- **Wiring:**
  - Ingest page "Run in background" → `POST /api/tasks {type:"ingest", params:{path, sync}}`.
  - Articles "Synthesize now" → `POST /api/tasks {type:"synthesize", params:{limit?, dry_run?}}`.
  - New "Re-extract" trigger on the Browse / source-management UI (minimal) →
    `POST /api/tasks {type:"extract", params:{source}}`, with a note that
    re-extract refreshes knowledge metadata but not dense vectors.

## 7. Error handling (Fail Loud)

- Runner exceptions set the task `status=error` and store `error` (repr); the
  worker continues with the next task.
- Per-child failures are recorded on the child (`status:"error"`) and in the
  task result, never silently dropped.
- Atomic writes prevent a half-written index/checkpoint on crash.
- Startup re-queues stale `running` tasks (crash recovery).

## 8. Testing (business-logic)

- `TaskStore`: create/list ordering, atomic round-trip, 100k cap eviction of
  oldest finished, `clear_finished`, throttled-update behavior (counts coalesce).
- Children: paginated reads; ingest children derived from checkpoint.
- Worker: serial execution order; cooperative cancel stops between children;
  startup recovery re-queues a stale `running` task.
- Runners: ingest runner enumerates children + resumes via checkpoint;
  synthesize runner maps topics → children; extract runner re-extracts existing
  chunks and updates stored knowledge without re-embedding.
- API: enqueue → poll summary → expand children (paginated) → cancel → clear;
  unknown id → 404.
- Frontend: typecheck + build; the task-center polling/cancel wiring.

## 9. Out of scope (YAGNI)

- Parallel task execution / configurable concurrency.
- Per-child cancellation (only parent-level).
- Cross-process/distributed queue (single-process web server only).
- Recomputing dense vectors during standalone re-extract.
