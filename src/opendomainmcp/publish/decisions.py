from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

READY_TO_PUBLISH = {"ready", "published"}


class PublishGateError(ValueError):
    pass


def require_publish_override(evidence: dict, override_reason: str | None) -> None:
    status = str(evidence.get("status") or "blocked")
    if status in READY_TO_PUBLISH:
        return
    if (override_reason or "").strip():
        return
    raise PublishGateError(
        f"Quality evidence is {status}; an override reason is required to publish."
    )


def build_decision(
    *,
    collection: str,
    view: str,
    action: str,
    endpoint_url: str,
    evidence: dict,
    override_reason: str = "",
) -> dict:
    readiness_status = str(evidence.get("status") or "blocked")
    status = "published" if action == "publish" else "unpublished"
    gates = [
        {
            "id": card.get("id", ""),
            "gate": card.get("gate", ""),
            "status": card.get("status", ""),
            "score": int(card.get("score") or 0),
            "summary": card.get("summary", ""),
        }
        for card in evidence.get("evidence", [])
    ]
    return {
        "id": uuid.uuid4().hex,
        "collection": collection,
        "view": view,
        "action": action,
        "status": status,
        "readiness_status": readiness_status,
        "readiness_score": int(evidence.get("score") or 0),
        "gates": gates,
        "override_reason": (override_reason or "").strip(),
        "endpoint_url": endpoint_url,
        "created_at": time.time(),
    }


class PublishDecisionStore:
    def __init__(self, data_dir):
        self._path = Path(data_dir) / "publish_decisions.json"
        self._decisions = self._load()

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return list(data.get("decisions", []))

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps({"decisions": self._decisions}), encoding="utf-8")
        os.replace(tmp, self._path)

    def append(self, decision: dict) -> dict:
        self._decisions.append(decision)
        self._persist()
        return decision

    def history(self, collection: str, view: str) -> list[dict]:
        items = [
            d
            for d in self._decisions
            if d.get("collection") == collection and d.get("view") == view
        ]
        return sorted(items, key=lambda d: d.get("created_at", 0), reverse=True)

    def latest(self, collection: str, view: str) -> dict | None:
        items = self.history(collection, view)
        return items[0] if items else None
