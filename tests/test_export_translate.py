import pytest

from opendomainmcp.export.models import (ExportArticle, ExportBundle,
                                         ExportReport, ExportRule,
                                         ExportWorkflow)
from opendomainmcp.export.translate import TranslationCache, translate_bundle


def _bundle():
    return ExportBundle(
        articles=[ExportArticle(id="a1", title="Order flow", topic="orders",
                                body="Orders ship after approval.")],
        rules=[ExportRule(id="r1", statement="Credit limit must be checked.")],
        workflows=[ExportWorkflow(name="Fulfillment", display_name="Fulfillment",
                                  prerequisites=["stock synced"],
                                  steps=[{"order": 1, "text": "pick items",
                                          "precondition": "paid", "chunk_id": "c"}])])


def test_translate_all_fields_and_fills_cache(tmp_path):
    cache = TranslationCache(tmp_path / "t.json")
    calls = []

    def fake(text):
        calls.append(text)
        return f"譯{text}"

    report = ExportReport()
    b = _bundle()
    translate_bundle(b, fake, cache, report)
    assert b.articles[0].title == "譯Order flow"
    assert b.articles[0].body == "譯Orders ship after approval."
    assert b.articles[0].topic == "orders"          # key untouched
    assert b.rules[0].statement == "譯Credit limit must be checked."
    assert b.workflows[0].display_name == "譯Fulfillment"
    assert b.workflows[0].name == "Fulfillment"     # key untouched
    assert b.workflows[0].steps[0]["text"] == "譯pick items"
    assert b.workflows[0].steps[0]["precondition"] == "譯paid"
    assert b.workflows[0].prerequisites == ["譯stock synced"]
    assert report.translate_errors == []
    cache.save()
    assert (tmp_path / "t.json").exists()


def test_cache_hit_skips_llm_call(tmp_path):
    path = tmp_path / "t.json"
    calls = []

    def fake(text):
        calls.append(text)
        return f"譯{text}"

    c = TranslationCache(path)
    translate_bundle(_bundle(), fake, c, ExportReport())
    first = len(calls)
    assert first > 0
    c.save()
    translate_bundle(_bundle(), fake, TranslationCache(path), ExportReport())
    assert len(calls) == first  # warm cache from disk: zero new calls


def test_failure_keeps_original_marks_and_reports(tmp_path):
    def boom(text):
        if "Credit" in text:
            raise RuntimeError("api down")
        return f"譯{text}"

    b = _bundle()
    report = ExportReport()
    translate_bundle(b, boom, TranslationCache(tmp_path / "t.json"), report)
    r = b.rules[0]
    assert r.statement == "Credit limit must be checked."   # original kept
    assert r.untranslated is True
    assert len(report.translate_errors) == 1
    assert report.translate_errors[0]["id"] == "r1"
    # other objects still translated
    assert b.articles[0].title == "譯Order flow"
