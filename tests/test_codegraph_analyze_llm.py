"""ChainAnalyzer prompt/parse behavior with an injected fake LLM (plan 4B)."""

import json

import pytest

from opendomainmcp.codegraph.analyze_llm import ChainAnalyzer, FunctionSummary
from opendomainmcp.codegraph.chains import Chain
from opendomainmcp.codegraph.models import FunctionDef
from opendomainmcp.config import Settings


def _fd(q, language="java"):
    return FunctionDef(qualified_name=q, file="F.java", start_line=1,
                       end_line=9, language=language)


def test_summarize_function_parses_json_and_builds_context():
    seen = {}

    def fake(system, user):
        seen["system"], seen["user"] = system, user
        return json.dumps({"summary": "Validates order amount.",
                           "rules": ["amount must not be negative"],
                           "confidence": 0.9})

    analyzer = ChainAnalyzer(Settings(), complete=fake)
    fs = analyzer.summarize_function(
        _fd("a.B.validate"), "if (amt < 0) throw ...",
        callee_sources={"pkg.check": "PROCEDURE check ..."},
        callee_summaries={"deep.fn": FunctionSummary("deep.fn", "Logs stuff.")},
    )
    assert fs.qualified_name == "a.B.validate"
    assert fs.rules == ["amount must not be negative"] and fs.confidence == 0.9
    # context assembly: own source, 1-hop callee source, deep summary
    assert "if (amt < 0)" in seen["user"]
    assert "PROCEDURE check" in seen["user"]
    assert "Logs stuff." in seen["user"]
    assert "JSON" in seen["system"]


def test_summarize_function_tolerates_fenced_json():
    def fake(system, user):
        return '```json\n{"summary": "S", "rules": [], "confidence": 0.5}\n```'

    fs = ChainAnalyzer(Settings(), complete=fake).summarize_function(
        _fd("x.Y.z"), "code", {}, {})
    assert fs.summary == "S" and fs.confidence == 0.5


def test_analyze_chain_includes_member_summaries_in_order():
    seen = {}

    def fake(system, user):
        seen["user"] = user
        return json.dumps({"title": "Charge flow", "body": "Entry to DB.",
                           "rules": ["r1"]})

    chain = Chain(entry="api.Ctl.post", members=["api.Ctl.post", "svc.A.a"])
    summaries = {
        "api.Ctl.post": FunctionSummary("api.Ctl.post", "Receives request."),
        "svc.A.a": FunctionSummary("svc.A.a", "Does work."),
    }
    out = ChainAnalyzer(Settings(), complete=fake).analyze_chain(chain, summaries)
    assert out == {"title": "Charge flow", "body": "Entry to DB.", "rules": ["r1"]}
    assert seen["user"].index("Receives request.") < seen["user"].index("Does work.")


def test_llm_failure_raises():
    def fake(system, user):
        return "not json at all {{{"

    with pytest.raises(Exception):
        ChainAnalyzer(Settings(), complete=fake).summarize_function(
            _fd("x.Y.z"), "code", {}, {})


def test_parse_llm_json_public_helper():
    from opendomainmcp.extract.knowledge import parse_llm_json

    assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_llm_json('{"a": 1}') == {"a": 1}


def test_parse_llm_json_no_braces_raises_extraction_error():
    from opendomainmcp.extract.knowledge import ExtractionError, parse_llm_json

    with pytest.raises(ExtractionError):
        parse_llm_json("no braces here")


def test_summarize_function_parses_evidence():
    def fake(system, user):
        assert '"evidence"' in system
        return json.dumps({"summary": "Validates.", "rules": ["amt >= 0"],
                           "confidence": 0.9,
                           "evidence": [{"claim": "amt >= 0",
                                         "quote": "if (amt < 0) throw"}]})

    fs = ChainAnalyzer(Settings(), complete=fake).summarize_function(
        _fd("a.B.validate"), "if (amt < 0) throw", {}, {})
    assert fs.evidence == [{"claim": "amt >= 0", "quote": "if (amt < 0) throw"}]


def test_summarize_function_evidence_defaults_empty():
    def fake(system, user):
        return json.dumps({"summary": "S", "rules": [], "confidence": 0.5})

    fs = ChainAnalyzer(Settings(), complete=fake).summarize_function(
        _fd("x.Y.z"), "code", {}, {})
    assert fs.evidence == []


def test_normalize_evidence_non_list_returns_empty():
    from opendomainmcp.codegraph.analyze_llm import _normalize_evidence

    assert _normalize_evidence(None) == []
    assert _normalize_evidence("a string") == []
    assert _normalize_evidence({"claim": "c", "quote": "q"}) == []
    assert _normalize_evidence(42) == []


def test_normalize_evidence_bare_string_becomes_quote():
    from opendomainmcp.codegraph.analyze_llm import _normalize_evidence

    assert _normalize_evidence(["if (x) throw"]) == [
        {"claim": "", "quote": "if (x) throw"}]


def test_normalize_evidence_whitespace_only_string_dropped():
    from opendomainmcp.codegraph.analyze_llm import _normalize_evidence

    assert _normalize_evidence(["   ", "\t\n"]) == []


def test_normalize_evidence_skips_non_dict_non_str_items():
    from opendomainmcp.codegraph.analyze_llm import _normalize_evidence

    assert _normalize_evidence([42, None, ["nested"],
                                {"claim": "c", "quote": "q"}]) == [
        {"claim": "c", "quote": "q"}]


def test_normalize_evidence_dict_with_blank_quote_dropped():
    from opendomainmcp.codegraph.analyze_llm import _normalize_evidence

    assert _normalize_evidence([{"claim": "c", "quote": ""},
                                {"claim": "c", "quote": "   "},
                                {"claim": "c"}]) == []
