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
