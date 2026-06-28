import time

from opendomainmcp.tasks.store import TaskStore
from opendomainmcp.tasks.worker import TaskWorker


def _wait_for(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


def test_worker_runs_tasks_serially_in_order(tmp_path):
    s = TaskStore(tmp_path)
    order = []

    def run_one(task, is_cancelled):
        order.append(task.title)

    w = TaskWorker(s, run_one)
    a = s.create("ingest", "A", "c", {})
    b = s.create("ingest", "B", "c", {})
    w.start()
    w.wake()
    assert _wait_for(lambda: s.get(b.id).status == "done")
    assert order == ["A", "B"]
    assert s.get(a.id).status == "done"
    assert s.get(a.id).attempts == 1
    assert s.get(b.id).attempts == 1
    w.stop()


def test_worker_marks_error_on_exception(tmp_path):
    s = TaskStore(tmp_path)

    def run_one(task, is_cancelled):
        raise RuntimeError("boom")

    w = TaskWorker(s, run_one)
    t = s.create("ingest", "X", "c", {})
    w.start(); w.wake()
    assert _wait_for(lambda: s.get(t.id).status == "error")
    row = s.get(t.id)
    assert "boom" in row.error
    assert row.error_type == "RuntimeError"
    assert row.error_message == "boom"
    assert row.attempts == 1
    w.stop()


def test_worker_cancellation_marks_cancelled(tmp_path):
    s = TaskStore(tmp_path)

    def run_one(task, is_cancelled):
        for _ in range(100):
            if is_cancelled():
                return
            time.sleep(0.01)

    w = TaskWorker(s, run_one)
    t = s.create("ingest", "X", "c", {})
    w.start(); w.wake()
    assert _wait_for(lambda: s.get(t.id).status == "running")
    s.request_cancel(t.id)
    assert _wait_for(lambda: s.get(t.id).status == "cancelled")
    row = s.get(t.id)
    assert row.result == {"status": "cancelled", "message": "Task cancelled by request."}
    assert row.attempts == 1
    w.stop()


def test_worker_respects_cancel_requested_before_run(tmp_path):
    s = TaskStore(tmp_path)
    ran = False

    def run_one(task, is_cancelled):
        nonlocal ran
        ran = True

    w = TaskWorker(s, run_one)
    t = s.create("ingest", "X", "c", {})
    assert s.request_cancel(t.id) is True

    try:
        w.start(); w.wake()
        assert _wait_for(lambda: s.get(t.id).status == "cancelled")
        row = s.get(t.id)
        assert ran is False
        assert row.result == {"status": "cancelled", "message": "Task cancelled by request."}
        assert row.attempts == 0
    finally:
        w.stop()


def test_worker_start_does_not_clear_concurrent_queued_cancel(tmp_path):
    class RacingCancelStore(TaskStore):
        def __init__(self, data_dir):
            super().__init__(data_dir)
            self.inject_cancel_for = None

        def start(self, task_id, *args, **kwargs):
            if self.inject_cancel_for == task_id:
                self.inject_cancel_for = None
                self.request_cancel(task_id)
            return super().start(task_id, *args, **kwargs)

    s = RacingCancelStore(tmp_path)
    ran = False

    def run_one(task, is_cancelled):
        nonlocal ran
        ran = True

    w = TaskWorker(s, run_one)
    t = s.create("ingest", "X", "c", {})
    s.inject_cancel_for = t.id

    try:
        w.start(); w.wake()
        assert _wait_for(lambda: s.get(t.id).status == "cancelled")
        assert ran is False
        assert s.get(t.id).attempts == 0
    finally:
        w.stop()


def test_recover_requeues_stale_running(tmp_path):
    s = TaskStore(tmp_path)
    t = s.create("ingest", "X", "c", {})
    s.update(t.id, status="running")
    TaskWorker(s, lambda task, c: None).recover()
    row = s.get(t.id)
    assert row.status == "queued"
    assert row.recovery_count == 1
    assert row.recovered_at is not None
    assert row.last_transition == "recovered_running_to_queued"
