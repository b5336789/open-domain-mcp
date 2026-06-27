from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

VALIDATION_PASSED = "passed"
VALIDATION_FAILED = "failed"
VALIDATION_VALIDATING = "validating"


def build_scenario(*, collection: str, view: str, name: str, query: str) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "collection": collection,
        "view": view,
        "name": name.strip(),
        "query": query.strip(),
        "created_at": time.time(),
    }


def _tool_result_count(result: dict) -> int:
    return sum(len(tool.get("results") or []) for tool in result.get("tools", []))


def build_run(
    *,
    collection: str,
    scenario: dict,
    result: dict | None = None,
    error: str = "",
) -> dict:
    result = result or {}
    grounding = result.get("grounding") or {}
    hits = int(grounding.get("hits") or 0)
    tool_results = _tool_result_count(result)
    status = (
        VALIDATION_PASSED
        if hits > 0 and tool_results > 0 and not error
        else VALIDATION_FAILED
    )
    return {
        "id": uuid.uuid4().hex,
        "scenario_id": scenario["id"],
        "collection": collection,
        "view": scenario["view"],
        "query": scenario["query"],
        "status": status,
        "grounding_hits": hits,
        "avg_score": float(grounding.get("avg_score") or 0.0),
        "tool_results": tool_results,
        "knowledge_types": list(grounding.get("knowledge_types") or []),
        "error": error,
        "created_at": time.time(),
    }


class ValidationStore:
    FILENAME = "validation_runs.json"

    def __init__(self, data_dir):
        self._path = Path(data_dir) / self.FILENAME
        self._data = self._load()

    def _load(self) -> dict[str, list[dict]]:
        if not self._path.exists():
            return {"scenarios": [], "runs": []}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Corrupt validation file {self._path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Validation file {self._path} must contain an object.")
        return {
            "scenarios": list(data.get("scenarios", [])),
            "runs": list(data.get("runs", [])),
        }

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(self._data), encoding="utf-8")
        os.replace(tmp, self._path)

    def append_scenario(self, scenario: dict) -> dict:
        self._data["scenarios"].append(scenario)
        self._persist()
        return scenario

    def append_run(self, run: dict) -> dict:
        self._data["runs"].append(run)
        self._persist()
        return run

    def scenarios(self, collection: str, view: str | None = None) -> list[dict]:
        return [
            scenario
            for scenario in self._data["scenarios"]
            if scenario.get("collection") == collection
            and (view is None or scenario.get("view") == view)
        ]

    def scenario(self, collection: str, scenario_id: str) -> dict | None:
        for scenario in self._data["scenarios"]:
            if (
                scenario.get("collection") == collection
                and scenario.get("id") == scenario_id
            ):
                return scenario
        return None

    def runs(
        self,
        collection: str,
        view: str | None = None,
        scenario_id: str | None = None,
    ) -> list[dict]:
        items = [
            run
            for run in self._data["runs"]
            if run.get("collection") == collection
            and (view is None or run.get("view") == view)
            and (scenario_id is None or run.get("scenario_id") == scenario_id)
        ]
        return sorted(items, key=lambda run: run.get("created_at", 0), reverse=True)


def summarize_validation(
    store: ValidationStore,
    collection: str,
    view: str | None = None,
) -> dict:
    scenarios = store.scenarios(collection, view)
    latest_runs: list[dict] = []
    for scenario in scenarios:
        runs = store.runs(collection, view=scenario["view"], scenario_id=scenario["id"])
        if runs:
            latest_runs.append(runs[0])
    failed = sum(1 for run in latest_runs if run.get("status") == VALIDATION_FAILED)
    passed = sum(1 for run in latest_runs if run.get("status") == VALIDATION_PASSED)
    latest = sorted(latest_runs, key=lambda run: run.get("created_at", 0), reverse=True)
    if not latest_runs:
        status = VALIDATION_VALIDATING
    elif failed:
        status = VALIDATION_FAILED
    else:
        status = VALIDATION_PASSED
    return {
        "collection": collection,
        "view": view,
        "status": status,
        "scenario_count": len(scenarios),
        "latest_run_count": len(latest_runs),
        "passed": passed,
        "failed": failed,
        "pass_rate": (passed / len(latest_runs)) if latest_runs else 0.0,
        "latest_run": latest[0] if latest else None,
    }
