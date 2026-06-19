import json

from opendomainmcp.metrics import MetricEvent, MetricsRecorder


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
