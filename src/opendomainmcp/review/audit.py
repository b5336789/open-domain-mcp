"""Append-only SQLite audit log for review decisions.

Every approve/reject/auto-approve is recorded with actor, timestamp, note,
and the status transition. SQLite (not MariaDB) so review auditing works even
when the graph store is unwired. Append-only by construction: the class only
INSERTs and SELECTs — there is no update or delete path."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    item_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    prev_status TEXT NOT NULL DEFAULT '',
    new_status TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_review_audit_item ON review_audit(item_id);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AuditEntry:
    ts: str
    item_id: str
    action: str
    actor: str
    note: str = ""
    prev_status: str = ""
    new_status: str = ""


class AuditLog:
    def __init__(self, db_path, clock: Optional[Callable[[], str]] = None):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _utc_now
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def record(self, item_id: str, action: str, actor: str, note: str = "",
               prev_status: str = "", new_status: str = "") -> AuditEntry:
        entry = AuditEntry(ts=self._clock(), item_id=item_id, action=action,
                           actor=actor, note=note, prev_status=prev_status,
                           new_status=new_status)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO review_audit "
                "(ts, item_id, action, actor, note, prev_status, new_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (entry.ts, entry.item_id, entry.action, entry.actor,
                 entry.note, entry.prev_status, entry.new_status))
        return entry

    def history(self, item_id: str) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM review_audit WHERE item_id = ? ORDER BY id DESC",
                (item_id,))
            return [dict(r) for r in cur.fetchall()]

    def all(self, limit: int = 200) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM review_audit ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]
