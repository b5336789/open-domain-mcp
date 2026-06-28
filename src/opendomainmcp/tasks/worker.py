from __future__ import annotations

import threading
import time

from .models import JOB_CANCELLED, JOB_DONE, JOB_ERROR, JOB_RUNNING


def _cancelled_result() -> dict:
    return {
        "status": JOB_CANCELLED,
        "message": "Task cancelled by request.",
    }


class TaskWorker:
    def __init__(self, store, run_one):
        self._store = store
        self._run_one = run_one
        self._wake = threading.Event()
        self._stop = False
        self._thread: threading.Thread | None = None

    def recover(self) -> None:
        self._store.recover_running()

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
        started = self._store.start(
            task.id,
            started_at=task.started_at or time.time(),
            cancel_requested=False,
            error=None,
            error_type=None,
            error_message=None,
            cancelled_fields={"result": _cancelled_result()},
        )
        if started is None or started.status == JOB_CANCELLED:
            return

        def is_cancelled():
            t = self._store.get(task.id)
            return bool(t and t.cancel_requested)

        try:
            self._run_one(task, is_cancelled)
            if is_cancelled():
                self._store.transition(
                    task.id,
                    JOB_CANCELLED,
                    result=_cancelled_result(),
                )
            else:
                self._store.transition(task.id, JOB_DONE)
        except Exception as exc:  # noqa: BLE001 - Fail Loud onto the task record
            self._store.transition(
                task.id,
                JOB_ERROR,
                error=repr(exc),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
