from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

TERMINAL = {"done", "error", "cancelled"}


@dataclass
class Task:
    id: str
    type: str               # ingest | synthesize | extract
    title: str
    collection: str
    status: str = "queued"  # queued | running | done | error | cancelled
    created_at: float = 0.0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    total: int = 0
    done: int = 0
    failures: list = field(default_factory=list)   # [{"name","status"}]
    cancel_requested: bool = False
    error: Optional[str] = None
    result: Optional[dict] = None
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        known = {f: d[f] for f in cls.__dataclass_fields__ if f in d}
        return cls(**known)

    def is_terminal(self) -> bool:
        return self.status in TERMINAL


def derive_child_status(index: int, done: int, running: bool,
                        failure: Optional[str]) -> str:
    if failure is not None:
        return failure
    if index < done:
        return "done"
    if index == done and running:
        return "running"
    return "pending"
