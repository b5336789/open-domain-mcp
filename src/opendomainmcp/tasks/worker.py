from __future__ import annotations

import threading
import time


class TaskWorker:
    def __init__(self, store, run_one):
        self._store = store
        self._run_one = run_one
        self._wake = threading.Event()
        self._stop = False
        self._thread: threading.Thread | None = None

    def recover(self) -> None:
        for t in self._store.list():
            if t.status == "running":
                self._store.update(t.id, status="queued", cancel_requested=False)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.recover()
        self._stop = False
        self._thread = threading.Thread(target=self._loop, name="task-worker",
                                        daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop:
            task = self._store.next_queued()
            if task is None:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            self._run(task)

    def _run(self, task) -> None:
        self._store.update(task.id, status="running", started_at=time.time())

        def is_cancelled():
            t = self._store.get(task.id)
            return bool(t and t.cancel_requested)

        try:
            self._run_one(task, is_cancelled)
            status = "cancelled" if is_cancelled() else "done"
            self._store.update(task.id, status=status, finished_at=time.time())
        except Exception as exc:  # noqa: BLE001 - Fail Loud onto the task record
            self._store.update(task.id, status="error", error=repr(exc),
                               finished_at=time.time())
