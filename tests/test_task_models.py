from opendomainmcp.tasks.models import Task, derive_child_status


def test_task_round_trip_and_terminal():
    t = Task(id="a1", type="ingest", title="Ingest /x", collection="c",
             total=3, done=1, params={"path": "/x"})
    d = t.to_dict()
    assert d["type"] == "ingest" and d["total"] == 3
    t2 = Task.from_dict(d)
    assert t2.id == "a1" and t2.params == {"path": "/x"}
    assert not t.is_terminal()
    t.status = "done"
    assert t.is_terminal()


def test_derive_child_status_prefix_and_failure():
    # done=2, running -> indices 0,1 done; index 2 running; 3+ pending
    assert derive_child_status(0, 2, True, None) == "done"
    assert derive_child_status(2, 2, True, None) == "running"
    assert derive_child_status(3, 2, True, None) == "pending"
    assert derive_child_status(2, 2, False, None) == "pending"  # task not running
    assert derive_child_status(1, 2, True, "error") == "error"  # failure overrides
