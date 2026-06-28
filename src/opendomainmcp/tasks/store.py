from __future__ import annotations

import json
import os
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Optional

from .models import (
    JOB_CANCELLED,
    JOB_QUEUED,
    JOB_RUNNING,
    RETRYABLE_TERMINAL_STATUSES,
    TERMINAL_STATUSES,
    Task,
    derive_child_status,
    validate_task_status,
)

HISTORY_CAP = 100_000
THROTTLE_SECONDS = 2.0
THROTTLE_COUNT = 100


class TaskStore:
    def __init__(self, data_dir):
        self._dir = Path(data_dir)
        self._index = self._dir / "tasks.json"
        self._children_dir = self._dir / ".tasks"
        self._lock = threading.RLock()
        self._tasks: dict[str, Task] = {}
        self._last_write: dict[str, tuple[float, int]] = {}  # id -> (ts, done)
        self._load()

    # -- persistence ----------------------------------------------------
    def _load(self) -> None:
        if self._index.exists():
            data = json.loads(self._index.read_text(encoding="utf-8"))
            for d in data.get("tasks", []):
                t = Task.from_dict(d)
                self._tasks[t.id] = t

    def _persist(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._evict()
        payload = {"tasks": [t.to_dict() for t in self._tasks.values()]}
        tmp = self._index.with_name(self._index.name + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self._index)

    def _evict(self) -> None:
        if len(self._tasks) <= HISTORY_CAP:
            return
        finished = sorted(
            (t for t in self._tasks.values() if t.is_terminal()),
            key=lambda t: t.created_at,
        )
        for t in finished[: len(self._tasks) - HISTORY_CAP]:
            self._tasks.pop(t.id, None)

    # -- CRUD -----------------------------------------------------------
    def create(self, type: str, title: str, collection: str, params: dict) -> Task:
        with self._lock:
            t = Task(id=uuid.uuid4().hex, type=type, title=title,
                     collection=collection, params=params, created_at=time.time())
            self._tasks[t.id] = t
            self._persist()
            return t

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list(self) -> list[Task]:
        items = list(self._tasks.values())
        items.sort(key=lambda t: (t.is_terminal(), -t.created_at))
        return items

    def next_queued(self) -> Optional[Task]:
        queued = [t for t in self._tasks.values() if t.status == JOB_QUEUED]
        queued.sort(key=lambda t: t.created_at)
        return queued[0] if queued else None

    def update(self, task_id: str, throttle: bool = False, **fields) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return
            if "status" in fields:
                validate_task_status(fields["status"])
            for k, v in fields.items():
                setattr(t, k, v)
            if throttle and not self._should_flush(t):
                return
            self._last_write[task_id] = (time.time(), t.done)
            self._persist()

    def transition(self, task_id: str, status: str, **fields) -> Optional[Task]:
        validate_task_status(status)
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return None
            previous = t.status
            t.status = status
            t.last_transition = f"{previous}_to_{status}"
            for k, v in fields.items():
                setattr(t, k, v)
            if status in TERMINAL_STATUSES and t.finished_at is None:
                t.finished_at = time.time()
            self._last_write[task_id] = (time.time(), t.done)
            self._persist()
            return t

    def start(
        self,
        task_id: str,
        cancelled_fields: Optional[dict] = None,
        **fields,
    ) -> Optional[Task]:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return None
            if t.cancel_requested:
                previous = t.status
                t.status = JOB_CANCELLED
                t.last_transition = f"{previous}_to_{JOB_CANCELLED}"
                for k, v in (cancelled_fields or {}).items():
                    setattr(t, k, v)
                if t.finished_at is None:
                    t.finished_at = time.time()
                self._last_write[task_id] = (time.time(), t.done)
                self._persist()
                return t
            previous = t.status
            t.status = JOB_RUNNING
            t.last_transition = f"{previous}_to_{JOB_RUNNING}"
            t.attempts += 1
            for k, v in fields.items():
                setattr(t, k, v)
            self._last_write[task_id] = (time.time(), t.done)
            self._persist()
            return t

    def mark_recovered(self, task_id: str) -> bool:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None or t.status != JOB_RUNNING:
                return False
            self._apply_recovered(t, time.time())
            self._last_write[task_id] = (time.time(), t.done)
            self._persist()
            return True

    def recover_running(self) -> int:
        with self._lock:
            now = time.time()
            count = 0
            for t in self._tasks.values():
                if t.status != JOB_RUNNING:
                    continue
                self._apply_recovered(t, now)
                self._last_write[t.id] = (now, t.done)
                count += 1
            if count:
                self._persist()
            return count

    def _apply_recovered(self, t: Task, recovered_at: float) -> None:
        t.status = JOB_QUEUED
        t.cancel_requested = False
        t.started_at = None
        t.finished_at = None
        t.recovery_count += 1
        t.recovered_at = recovered_at
        t.last_transition = "recovered_running_to_queued"

    def retry(self, task_id: str) -> Task:
        with self._lock:
            original = self._tasks.get(task_id)
            if original is None:
                raise KeyError(task_id)
            if original.status not in RETRYABLE_TERMINAL_STATUSES:
                raise ValueError(f"task {task_id} is not retryable from status {original.status}")
            now = time.time()
            retry = Task(
                id=uuid.uuid4().hex,
                type=original.type,
                title=original.title,
                collection=original.collection,
                params=deepcopy(original.params),
                created_at=now,
                result={
                    "retry_of": original.id,
                    "retry_status": original.status,
                    "retry_created_at": now,
                },
                last_transition=f"retry_of_{original.id}",
            )
            self._tasks[retry.id] = retry
            self._persist()
            return retry

    def _should_flush(self, t: Task) -> bool:
        ts, done = self._last_write.get(t.id, (0.0, 0))
        return (time.time() - ts) >= THROTTLE_SECONDS or (t.done - done) >= THROTTLE_COUNT

    def request_cancel(self, task_id: str) -> bool:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None or t.is_terminal():
                return False
            t.cancel_requested = True
            self._persist()
            return True

    def clear_finished(self) -> int:
        with self._lock:
            finished = [t.id for t in self._tasks.values() if t.is_terminal()]
            for tid in finished:
                self._tasks.pop(tid, None)
                names = self._children_dir / f"{tid}.names.json"
                if names.exists():
                    names.unlink()
            self._persist()
            return len(finished)

    # -- children -------------------------------------------------------
    def set_children_names(self, task_id: str, names: list[str]) -> None:
        with self._lock:
            self._children_dir.mkdir(parents=True, exist_ok=True)
            path = self._children_dir / f"{task_id}.names.json"
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(names), encoding="utf-8")
            os.replace(tmp, path)
            t = self._tasks.get(task_id)
            if t is not None:
                t.total = len(names)
                self._persist()

    def read_children(self, task_id: str, offset: int = 0, limit: int = 100) -> dict:
        t = self._tasks.get(task_id)
        path = self._children_dir / f"{task_id}.names.json"
        if t is None or not path.exists():
            return {"children": [], "total": t.total if t else 0}
        names = json.loads(path.read_text(encoding="utf-8"))
        fail = {f["name"]: f["status"] for f in t.failures}
        running = t.status == "running"
        out = []
        for i in range(offset, min(offset + limit, len(names))):
            name = names[i]
            out.append({"name": name,
                        "status": derive_child_status(i, t.done, running, fail.get(name))})
        return {"children": out, "total": len(names)}
