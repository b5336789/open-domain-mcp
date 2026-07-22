import json

from opendomainmcp.export.models import (ExportArticle, ExportBundle,
                                         ExportReport, ExportRule,
                                         ExportWorkflow)
from opendomainmcp.export.organize import build_outline


def _bundle():
    return ExportBundle(
        articles=[ExportArticle(id="a1", title="T1", topic="orders", body="b"),
                  ExportArticle(id="a2", title="T2", topic="billing", body="b")],
        rules=[ExportRule(id="R-LONG-1", statement="s1"),
               ExportRule(id="R-LONG-2", statement="s2")],
        workflows=[ExportWorkflow(name="Fulfillment", display_name="Fulfillment")])


def _llm_response():
    return json.dumps({"domains": [{
        "name": "訂單管理",
        "flows": [{"workflow": "Fulfillment", "articles": ["orders"],
                   "rules": ["r1"]}],
        "articles": [], "rules": ["r2", "r99"]}]})  # r99 is unknown


def test_outline_maps_tokens_and_flags_unknown(tmp_path):
    report = ExportReport()
    outline = build_outline(_bundle(), lambda prompt: _llm_response(),
                            tmp_path / "o.json", report)
    d = outline.domains[0]
    assert d.name == "訂單管理"
    assert d.flows[0].workflow == "Fulfillment"
    assert d.flows[0].articles == ["orders"]
    assert d.flows[0].rules == ["R-LONG-1"]        # r1 → real id
    assert d.rules == ["R-LONG-2"]                 # r2 → real id, r99 dropped
    assert any("r99" in w for w in report.outline_warnings)


def test_outline_computes_unassigned_leftovers(tmp_path):
    outline = build_outline(_bundle(), lambda p: _llm_response(),
                            tmp_path / "o.json", ExportReport())
    assert outline.unassigned_articles == ["billing"]
    assert outline.unassigned_workflows == []
    assert outline.unassigned_rules == []


def test_outline_cache_hit_skips_call(tmp_path):
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return _llm_response()

    path = tmp_path / "o.json"
    build_outline(_bundle(), llm, path, ExportReport())
    build_outline(_bundle(), llm, path, ExportReport())
    assert len(calls) == 1  # second run served from cache


def test_no_llm_returns_none(tmp_path):
    report = ExportReport()
    assert build_outline(_bundle(), None, tmp_path / "o.json", report) is None


def test_garbage_output_returns_none_with_warning(tmp_path):
    report = ExportReport()
    outline = build_outline(_bundle(), lambda p: "not json at all",
                            tmp_path / "o.json", report)
    assert outline is None
    assert report.outline_warnings
