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


def test_cache_merges_across_distinct_bundles(tmp_path):
    # Cache entries for different bundles must not clobber each other:
    # caching bundle B must not evict bundle A's already-cached entry.
    path = tmp_path / "o.json"
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return _llm_response()

    bundle_a = _bundle()
    bundle_b = ExportBundle(
        articles=[ExportArticle(id="a3", title="T3", topic="returns", body="b")],
        rules=[], workflows=[])

    build_outline(bundle_a, llm, path, ExportReport())          # caches key(A)
    build_outline(bundle_b, lambda p: json.dumps(
        {"domains": [{"name": "退貨", "flows": [], "articles": ["returns"],
                       "rules": []}]}), path, ExportReport())    # caches key(B)
    assert len(calls) == 1
    build_outline(bundle_a, llm, path, ExportReport())          # must hit cache
    assert len(calls) == 1  # no new call: both keys survived on disk


def test_cache_poisoning_bad_response_not_cached(tmp_path):
    # A parseable-but-unusable response (truthy dict, no usable domains) must
    # NOT be cached, so a later run with a good LLM still gets invoked and
    # produces a real outline instead of being stuck on the flat layout.
    path = tmp_path / "o.json"
    report1 = ExportReport()
    outline1 = build_outline(_bundle(), lambda p: json.dumps(
        {"note": "cannot comply"}), path, report1)
    assert outline1 is None

    calls = []

    def good_llm(prompt):
        calls.append(prompt)
        return _llm_response()

    report2 = ExportReport()
    outline2 = build_outline(_bundle(), good_llm, path, report2)
    assert len(calls) == 1  # LLM was actually invoked, not skipped via stale cache
    assert outline2 is not None
    assert outline2.domains[0].name == "訂單管理"


def test_no_llm_returns_none(tmp_path):
    report = ExportReport()
    assert build_outline(_bundle(), None, tmp_path / "o.json", report) is None


def test_garbage_output_returns_none_with_warning(tmp_path):
    report = ExportReport()
    outline = build_outline(_bundle(), lambda p: "not json at all",
                            tmp_path / "o.json", report)
    assert outline is None
    assert report.outline_warnings


def test_duplicate_placement_keeps_first_and_warns(tmp_path):
    # "orders" placed under two domains -> only the first keeps it, and a
    # warning names the duplicate.
    report = ExportReport()
    response = json.dumps({"domains": [
        {"name": "訂單管理", "flows": [], "articles": ["orders"], "rules": []},
        {"name": "帳務", "flows": [], "articles": ["orders", "billing"],
         "rules": []}]})
    outline = build_outline(_bundle(), lambda p: response,
                            tmp_path / "o.json", report)
    placements = [d.articles for d in outline.domains]
    assert placements[0] == ["orders"]
    assert placements[1] == ["billing"]
    assert any("orders" in w for w in report.outline_warnings)


def test_non_dict_domain_entry_skipped_not_raised(tmp_path):
    # A parseable-but-malformed shape: domains is a list of bare strings
    # instead of objects. Must not raise AttributeError.
    report = ExportReport()
    outline = build_outline(_bundle(), lambda p: json.dumps({"domains": ["Sales"]}),
                            tmp_path / "o.json", report)
    assert outline is None
    assert report.outline_warnings


def test_non_dict_flow_entry_skipped_with_warning(tmp_path):
    # domain is a proper object but one of its flows is a bare string.
    report = ExportReport()
    response = json.dumps({"domains": [{
        "name": "訂單管理", "flows": ["not-an-object"],
        "articles": ["orders"], "rules": []}]})
    outline = build_outline(_bundle(), lambda p: response,
                            tmp_path / "o.json", report)
    assert outline is not None
    assert outline.domains[0].flows == []
    assert outline.domains[0].articles == ["orders"]
    assert report.outline_warnings


def test_unrepairable_json_braces_returns_none_with_warning(tmp_path):
    # Braces are present but the content is not repairable JSON -> parse_llm_json
    # raises json.JSONDecodeError (not ExtractionError). Must degrade, not crash.
    report = ExportReport()
    outline = build_outline(_bundle(), lambda p: "{completely broken not json}",
                            tmp_path / "o.json", report)
    assert outline is None
    assert report.outline_warnings
