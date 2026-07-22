from opendomainmcp.export.collect import collect_bundle
from opendomainmcp.export.models import ExportReport


class FakeStore:
    """Pages like ChromaStore.get_items: filters on where["kind"]."""

    def __init__(self, items):
        self._items = items

    def get_items(self, limit=50, offset=0, where=None):
        kind = (where or {}).get("kind")
        rows = [i for i in self._items if i["metadata"].get("kind") == kind]
        return rows[offset:offset + limit]

    def stats(self):
        return {"count": len(self._items), "collection": "test"}


class FakeGraph:
    def __init__(self, workflows):
        self._wf = workflows

    def list_workflows(self, q=None, limit=50):
        return [{"name": n} for n in self._wf]

    def get_workflow(self, name):
        wf = self._wf.get(name)
        if wf is None:
            return None
        return {"workflow_name": name, "prerequisites": wf["prereqs"],
                "steps": wf["steps"]}


def _article_item(i):
    return {"id": f"a{i}", "text": f"body {i}", "metadata": {
        "kind": "article", "title": f"Title {i}", "topic": f"topic-{i}",
        "sources": "a.py | b.py", "source_chunk_ids": "c1, c2"}}


def _rule_item(i, trust="normal"):
    return {"id": f"r{i}", "text": "ignored", "metadata": {
        "kind": "rule", "statement": f"Rule {i}", "trust": trust,
        "corroborations": 2, "layers": "code, docs",
        "sources": "a.py:1-5 | b.vb:9-20", "review_status": "approved"},
        "evidence": [{"claim": "c", "quote": "q"}]}


def test_collect_pages_all_kinds_completely():
    # 120 articles + 90 rules forces >1 page per kind (page size 100)
    items = [_article_item(i) for i in range(120)] + [_rule_item(i) for i in range(90)]
    items.append({"id": "x", "text": "chunk", "metadata": {"kind": "code"}})
    bundle = collect_bundle(FakeStore(items), FakeGraph({}), graph_enabled=False)
    assert len(bundle.articles) == 120
    assert len(bundle.rules) == 90
    assert bundle.stats["count"] == len(items)
    assert bundle.graph_enabled is False
    assert bundle.workflows == []


def test_collect_parses_metadata_fields():
    bundle = collect_bundle(FakeStore([_article_item(1), _rule_item(1, "conflicted")]),
                            FakeGraph({}), graph_enabled=False)
    a, r = bundle.articles[0], bundle.rules[0]
    assert a.title == "Title 1" and a.topic == "topic-1" and a.body == "body 1"
    assert a.sources == ["a.py", "b.py"]
    assert a.source_chunk_ids == ["c1", "c2"]
    assert r.statement == "Rule 1" and r.trust == "conflicted"
    assert r.corroborations == 2 and r.layers == ["code", "docs"]
    assert r.sources == ["a.py:1-5", "b.vb:9-20"]
    assert r.evidence == [{"claim": "c", "quote": "q"}]


def test_collect_reads_workflows_from_graph():
    graph = FakeGraph({"Order Fulfillment": {
        "prereqs": ["stock synced"],
        "steps": [{"order": 1, "text": "pick", "precondition": "", "chunk_id": "c1"},
                  {"order": 2, "text": "ship", "precondition": "picked", "chunk_id": "c2"}]}})
    bundle = collect_bundle(FakeStore([]), graph, graph_enabled=True)
    assert len(bundle.workflows) == 1
    wf = bundle.workflows[0]
    assert wf.name == "Order Fulfillment" and wf.display_name == "Order Fulfillment"
    assert wf.prerequisites == ["stock synced"]
    assert [s["text"] for s in wf.steps] == ["pick", "ship"]


def test_collect_skips_graph_when_disabled():
    graph = FakeGraph({"W": {"prereqs": [], "steps": []}})
    bundle = collect_bundle(FakeStore([]), graph, graph_enabled=False)
    assert bundle.workflows == [] and bundle.graph_enabled is False


def test_collect_fails_loud_when_workflows_hit_500_cap():
    # Create exactly 500 workflows (W0 to W499)
    workflows = {f"W{i}": {"prereqs": [], "steps": []} for i in range(500)}
    graph = FakeGraph(workflows)
    report = ExportReport()
    bundle = collect_bundle(FakeStore([]), graph, graph_enabled=True, report=report)

    # Should report truncation in skipped
    assert len(report.skipped) == 1
    assert "workflows" in report.skipped[0].lower()
    assert "truncated" in report.skipped[0].lower() or "500" in report.skipped[0]
    assert len(bundle.workflows) == 500


def test_collect_backward_compatible_without_report():
    # Ensure calling without report arg still works (backward compatibility)
    graph = FakeGraph({"W": {"prereqs": [], "steps": []}})
    bundle = collect_bundle(FakeStore([]), graph, graph_enabled=True)
    assert len(bundle.workflows) == 1
    assert bundle.workflows[0].name == "W"
