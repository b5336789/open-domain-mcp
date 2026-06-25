from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from .models import Task, derive_child_status

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
        queued = [t for t in self._tasks.values() if t.status == "queued"]
        queued.sort(key=lambda t: t.created_at)
        return queued[0] if queued else None

    def update(self, task_id: str, throttle: bool = False, **fields) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return
            for k, v in fields.items():
                setattr(t, k, v)
            if throttle and not self._should_flush(t):
                return
            self._last_write[task_id] = (time.time(), t.done)
            self._persist()

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
