from opendomainmcp.tasks.store import TaskStore


def test_create_list_ordering_and_persistence(tmp_path):
    s = TaskStore(tmp_path)
    a = s.create("ingest", "A", "c", {"path": "/a"})
    b = s.create("synthesize", "B", "c", {})
    s.update(a.id, status="done")
    # reload from disk (new instance) -> persisted
    s2 = TaskStore(tmp_path)
    ids = [t.id for t in s2.list()]
    assert ids[0] == b.id          # queued before terminal
    assert a.id in ids
    assert s2.get(a.id).status == "done"


def test_next_queued_is_oldest(tmp_path):
    s = TaskStore(tmp_path)
    a = s.create("ingest", "A", "c", {})
    s.create("ingest", "B", "c", {})
    assert s.next_queued().id == a.id


def test_children_names_and_derived_status(tmp_path):
    s = TaskStore(tmp_path)
    t = s.create("ingest", "A", "c", {})
    s.set_children_names(t.id, ["f0", "f1", "f2", "f3"])
    s.update(t.id, status="running", done=2,
             failures=[{"name": "f1", "status": "skipped"}])
    page = s.read_children(t.id, offset=0, limit=10)
    assert page["total"] == 4
    by_name = {c["name"]: c["status"] for c in page["children"]}
    assert by_name == {"f0": "done", "f1": "skipped", "f2": "running", "f3": "pending"}


def test_read_children_pagination(tmp_path):
    s = TaskStore(tmp_path)
    t = s.create("ingest", "A", "c", {})
    s.set_children_names(t.id, [f"f{i}" for i in range(250)])
    page = s.read_children(t.id, offset=100, limit=50)
    assert page["total"] == 250
    assert [c["name"] for c in page["children"]][:2] == ["f100", "f101"]
    assert len(page["children"]) == 50


def test_clear_finished_keeps_active(tmp_path):
    s = TaskStore(tmp_path)
    a = s.create("ingest", "A", "c", {})
    b = s.create("ingest", "B", "c", {})
    s.update(a.id, status="done")
    assert s.clear_finished() == 1
    remaining = [t.id for t in s.list()]
    assert remaining == [b.id]
