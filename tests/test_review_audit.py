"""Append-only SQLite review audit log (enhancement #3)."""

import threading

from opendomainmcp.review.audit import AuditEntry, AuditLog


def _log(tmp_path, ts="2026-07-08T00:00:00+00:00"):
    ticks = [ts]
    return AuditLog(tmp_path / "review_audit.db", clock=lambda: ticks[0])


def test_record_returns_entry_and_persists(tmp_path):
    log = _log(tmp_path)
    entry = log.record("chunk1", "approve", "alice", note="looks good",
                       prev_status="pending", new_status="approved")
    assert isinstance(entry, AuditEntry)
    assert entry.item_id == "chunk1" and entry.action == "approve"
    assert entry.actor == "alice" and entry.note == "looks good"
    rows = log.history("chunk1")
    assert len(rows) == 1
    assert rows[0]["new_status"] == "approved" and rows[0]["actor"] == "alice"


def test_history_is_per_item_and_newest_first(tmp_path):
    ticks = ["2026-07-08T00:00:01+00:00"]
    log = AuditLog(tmp_path / "a.db", clock=lambda: ticks[0])
    log.record("a", "approve", "u")
    ticks[0] = "2026-07-08T00:00:02+00:00"
    log.record("a", "reject", "u")
    log.record("b", "approve", "u")
    a_hist = log.history("a")
    assert [r["action"] for r in a_hist] == ["reject", "approve"]  # newest first
    assert len(log.history("b")) == 1


def test_reopening_db_keeps_rows(tmp_path):
    p = tmp_path / "persist.db"
    AuditLog(p, clock=lambda: "2026-07-08T00:00:00+00:00").record("x", "approve", "u")
    reopened = AuditLog(p, clock=lambda: "2026-07-08T00:00:00+00:00")
    assert len(reopened.history("x")) == 1


def test_all_returns_recent_newest_first(tmp_path):
    ticks = ["2026-07-08T00:00:01+00:00"]
    log = AuditLog(tmp_path / "a.db", clock=lambda: ticks[0])
    log.record("a", "approve", "u")
    ticks[0] = "2026-07-08T00:00:02+00:00"
    log.record("b", "reject", "u")
    rows = log.all()
    assert [r["item_id"] for r in rows] == ["b", "a"]


def test_defaults_and_auto_actor(tmp_path):
    log = _log(tmp_path)
    e = log.record("r1", "auto-approve", "auto", new_status="approved")
    assert e.note == "" and e.prev_status == ""
    assert log.history("r1")[0]["action"] == "auto-approve"


def test_concurrent_writes_and_reads(tmp_path):
    log = _log(tmp_path)
    errors = []

    def worker(n):
        try:
            for i in range(20):
                log.record(f"item{n}", "approve", "u", note=str(i))
                log.history(f"item{n}")
                log.all()
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(log.all(limit=1000)) == 100
    for n in range(5):
        assert len(log.history(f"item{n}")) == 20
