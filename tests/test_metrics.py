import json

from opendomainmcp.metrics import (
    RELEVANCE_THRESHOLD,
    MetricEvent,
    MetricsRecorder,
    agent_metrics,
    count_distinct_sources,
    product_metrics,
)


def test_record_search_and_ask_in_memory():
    rec = MetricsRecorder()  # in-memory mode
    rec.record_search("how to deploy", hits=3, scores=[0.9, 0.8, 0.7],
                      knowledge_types=["Runbook", "Workflow", "Runbook"])
    rec.record_ask("what is x", hits=2, scores=[0.6, 0.5],
                   knowledge_types=["FAQ", "Glossary"])

    events = rec.read_events()
    assert len(events) == 2
    assert events[0].kind == "search"
    assert events[0].hits == 3
    assert events[1].kind == "ask"
    assert events[0].ts > 0  # timestamp stamped


def test_record_defaults_when_scores_and_types_omitted():
    rec = MetricsRecorder()
    event = rec.record_search("q", hits=0)
    assert event.scores == []
    assert event.knowledge_types == []


def test_aggregate_counts_and_averages():
    rec = MetricsRecorder()
    rec.record_search("a", hits=2, scores=[1.0, 0.0],
                      knowledge_types=["API", "API"])
    rec.record_ask("b", hits=4, scores=[0.5, 0.5],
                   knowledge_types=["API", "Error"])

    agg = rec.aggregate()
    assert agg["total_events"] == 2
    assert agg["by_kind"] == {"search": 1, "ask": 1}
    assert agg["avg_hits"] == 3.0  # (2 + 4) / 2
    assert agg["avg_score"] == 0.5  # (1.0 + 0.0 + 0.5 + 0.5) / 4
    assert agg["per_type_hits"] == {"API": 3, "Error": 1}


def test_aggregate_empty_is_safe():
    agg = MetricsRecorder().aggregate()
    assert agg["total_events"] == 0
    assert agg["avg_hits"] == 0.0
    assert agg["avg_score"] == 0.0
    assert agg["per_type_hits"] == {}


def test_jsonl_persistence_round_trip(tmp_path):
    rec = MetricsRecorder(data_dir=tmp_path)
    rec.record_search("deploy", hits=1, scores=[0.42],
                      knowledge_types=["Runbook"])
    rec.record_ask("billing", hits=2, scores=[0.3, 0.2],
                   knowledge_types=["Feature"])

    path = tmp_path / "metrics.jsonl"
    assert path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["query"] == "deploy"

    # A fresh recorder reads the same events back from disk.
    reloaded = MetricsRecorder(data_dir=tmp_path).read_events()
    assert len(reloaded) == 2
    assert reloaded[0].kind == "search"
    assert reloaded[0].scores == [0.42]
    assert reloaded[1].knowledge_types == ["Feature"]

    agg = MetricsRecorder(data_dir=tmp_path).aggregate()
    assert agg["total_events"] == 2
    assert agg["per_type_hits"] == {"Runbook": 1, "Feature": 1}


def test_jsonl_appends_across_recorder_instances(tmp_path):
    MetricsRecorder(data_dir=tmp_path).record_search("first", hits=1)
    MetricsRecorder(data_dir=tmp_path).record_search("second", hits=1)
    assert len(MetricsRecorder(data_dir=tmp_path).read_events()) == 2


def test_corrupt_jsonl_fails_loud(tmp_path):
    path = tmp_path / "metrics.jsonl"
    path.write_text("{not valid json}\n", encoding="utf-8")
    try:
        MetricsRecorder(data_dir=tmp_path).read_events()
        assert False, "expected ValueError on corrupt line"
    except ValueError as exc:
        assert "Corrupt metrics line 1" in str(exc)


def test_metric_event_from_dict_defaults():
    event = MetricEvent.from_dict({"kind": "search", "query": "q", "hits": 1})
    assert event.scores == []
    assert event.knowledge_types == []
    assert event.ts == 0.0


# --- Product metrics (TASKS.md #5.5) ------------------------------------


def test_product_metrics_assembles_clean_dict():
    # Arrange / Act
    result = product_metrics(
        knowledge_objects=42,
        indexed_sources=7,
        published_mcps=3,
    )

    # Assert
    assert result == {
        "published_mcps": 3,
        "knowledge_objects": 42,
        "indexed_sources": 7,
    }


def test_count_distinct_sources_counts_unique_values():
    # Arrange
    items = [
        {"metadata": {"source": "wiki"}},
        {"metadata": {"source": "wiki"}},
        {"metadata": {"source": "github"}},
    ]

    # Act / Assert
    assert count_distinct_sources(items) == 2


def test_count_distinct_sources_ignores_missing_and_empty():
    # Arrange
    items = [
        {"metadata": {"source": "wiki"}},
        {"metadata": {}},  # no source key
        {"metadata": {"source": ""}},  # empty
        {"metadata": {"source": "   "}},  # whitespace only
        {},  # no metadata at all
        {"metadata": None},  # null metadata
    ]

    # Act / Assert
    assert count_distinct_sources(items) == 1


def test_count_distinct_sources_empty_list():
    assert count_distinct_sources([]) == 0


# --- Agent metrics (TASKS.md #5.6) --------------------------------------


def test_agent_metrics_empty_is_safe():
    # Arrange / Act
    result = MetricsRecorder().agent_metrics()

    # Assert
    assert result == {
        "total_events": 0,
        "grounding_hit_rate": 0.0,
        "avg_hits": 0.0,
        "avg_score": 0.0,
        "retrieval_precision": 0.0,
    }


def test_agent_metrics_grounding_hit_rate_mix():
    # Arrange: two events with hits, two with zero hits.
    rec = MetricsRecorder()
    rec.record_search("a", hits=2, scores=[0.9, 0.8])
    rec.record_ask("b", hits=1, scores=[0.5])
    rec.record_search("c", hits=0, scores=[])
    rec.record_ask("d", hits=0, scores=[])

    # Act
    result = rec.agent_metrics()

    # Assert
    assert result["total_events"] == 4
    assert result["grounding_hit_rate"] == 0.5  # 2 of 4 grounded
    assert result["avg_hits"] == 0.75  # (2 + 1 + 0 + 0) / 4


def test_agent_metrics_retrieval_precision_proxy():
    # Arrange: with threshold 0.0, a 0.0 score is NOT counted as relevant.
    # Event 1: 2 hits, scores [0.9, 0.0] -> 1 relevant / 2 = 0.5
    # Event 2: 2 hits, scores [0.7, 0.6] -> 2 relevant / 2 = 1.0
    rec = MetricsRecorder()
    rec.record_search("a", hits=2, scores=[0.9, 0.0])
    rec.record_ask("b", hits=2, scores=[0.7, 0.6])

    # Act
    result = rec.agent_metrics()

    # Assert
    assert RELEVANCE_THRESHOLD == 0.0
    assert result["retrieval_precision"] == 0.75  # (0.5 + 1.0) / 2
    assert result["avg_score"] == 0.55  # (0.9 + 0.0 + 0.7 + 0.6) / 4


def test_agent_metrics_zero_hit_event_contributes_zero_precision():
    # Arrange: a zero-hit event must not divide by zero; it scores 0.0.
    events = [
        MetricEvent(kind="search", query="hit", hits=1, scores=[0.9]),
        MetricEvent(kind="search", query="miss", hits=0, scores=[]),
    ]

    # Act
    result = agent_metrics(events)

    # Assert
    assert result["retrieval_precision"] == 0.5  # (1.0 + 0.0) / 2
    assert result["grounding_hit_rate"] == 0.5
