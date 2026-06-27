from __future__ import annotations

import json

import pytest

from opendomainmcp.validation import (
    VALIDATION_FAILED,
    VALIDATION_PASSED,
    ValidationStore,
    build_run,
    build_scenario,
    summarize_validation,
)


def _result(hits=2, tool_results=2):
    return {
        "view": "product",
        "grounding": {
            "hits": hits,
            "avg_score": 0.75,
            "knowledge_types": ["Runbook"],
        },
        "tools": [
            {
                "tool": "search_features",
                "results": [
                    {
                        "id": "chunk-1",
                        "text": "rollback steps",
                        "score": 0.75,
                        "metadata": {"knowledge_type": "Runbook"},
                    }
                ][:tool_results],
            }
        ],
    }


def test_scenarios_persist_and_filter_by_collection_and_view(tmp_path):
    store = ValidationStore(tmp_path)
    alpha = store.append_scenario(
        build_scenario(
            collection="alpha",
            view="product",
            name="Rollback",
            query="How do I roll back?",
        )
    )
    store.append_scenario(
        build_scenario(
            collection="alpha",
            view="operations",
            name="Deploy",
            query="How do I deploy?",
        )
    )
    store.append_scenario(
        build_scenario(
            collection="beta",
            view="product",
            name="Billing",
            query="How do I bill?",
        )
    )

    reloaded = ValidationStore(tmp_path)

    assert reloaded.scenarios("alpha", "product") == [alpha]
    assert [s["name"] for s in reloaded.scenarios("alpha")] == ["Rollback", "Deploy"]


def test_run_builder_marks_passed_when_grounded_with_tool_results():
    scenario = build_scenario(
        collection="alpha",
        view="product",
        name="Rollback",
        query="How do I roll back?",
    )

    run = build_run(collection="alpha", scenario=scenario, result=_result())

    assert run["status"] == VALIDATION_PASSED
    assert run["grounding_hits"] == 2
    assert run["avg_score"] == 0.75
    assert run["tool_results"] == 1
    assert run["knowledge_types"] == ["Runbook"]
    assert run["error"] == ""


def test_run_builder_marks_failed_without_grounding_or_tool_results():
    scenario = build_scenario(
        collection="alpha",
        view="product",
        name="Rollback",
        query="How do I roll back?",
    )

    no_grounding = build_run(collection="alpha", scenario=scenario, result=_result(hits=0))
    no_results = build_run(collection="alpha", scenario=scenario, result=_result(tool_results=0))

    assert no_grounding["status"] == VALIDATION_FAILED
    assert no_results["status"] == VALIDATION_FAILED


def test_failed_run_can_record_error():
    scenario = build_scenario(
        collection="alpha",
        view="product",
        name="Rollback",
        query="How do I roll back?",
    )

    run = build_run(collection="alpha", scenario=scenario, error="simulator exploded")

    assert run["status"] == VALIDATION_FAILED
    assert run["grounding_hits"] == 0
    assert run["tool_results"] == 0
    assert run["error"] == "simulator exploded"


def test_summary_uses_latest_run_per_scenario(tmp_path):
    store = ValidationStore(tmp_path)
    scenario = store.append_scenario(
        build_scenario(
            collection="alpha",
            view="product",
            name="Rollback",
            query="How do I roll back?",
        )
    )
    first = build_run(collection="alpha", scenario=scenario, result=_result(hits=0))
    first["created_at"] = 1
    latest = build_run(collection="alpha", scenario=scenario, result=_result(hits=2))
    latest["created_at"] = 2
    store.append_run(first)
    store.append_run(latest)

    summary = summarize_validation(store, "alpha", "product")

    assert summary["status"] == VALIDATION_PASSED
    assert summary["scenario_count"] == 1
    assert summary["latest_run_count"] == 1
    assert summary["passed"] == 1
    assert summary["failed"] == 0
    assert summary["pass_rate"] == 1.0
    assert summary["latest_run"]["id"] == latest["id"]


def test_summary_is_validating_when_no_runs(tmp_path):
    store = ValidationStore(tmp_path)
    store.append_scenario(
        build_scenario(
            collection="alpha",
            view="product",
            name="Rollback",
            query="How do I roll back?",
        )
    )

    summary = summarize_validation(store, "alpha", "product")

    assert summary["status"] == "validating"
    assert summary["scenario_count"] == 1
    assert summary["latest_run_count"] == 0
    assert summary["passed"] == 0
    assert summary["failed"] == 0
    assert summary["pass_rate"] == 0.0


def test_corrupt_validation_file_fails_loudly(tmp_path):
    path = tmp_path / "validation_runs.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="Corrupt validation file"):
        ValidationStore(tmp_path)


def test_validation_file_requires_object(tmp_path):
    path = tmp_path / "validation_runs.json"
    path.write_text(json.dumps([]), encoding="utf-8")

    with pytest.raises(ValueError, match="must contain an object"):
        ValidationStore(tmp_path)
