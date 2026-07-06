"""LLM rule adjudication with content-hash verdict cache (enhancement #5)."""

import json

import pytest

from opendomainmcp.config import Settings
from opendomainmcp.consensus.adjudicate import RuleAdjudicator


def _adj(tmp_path, replies):
    calls = {"n": 0}

    def fake(system, user):
        calls["n"] += 1
        return json.dumps(replies[min(calls["n"] - 1, len(replies) - 1)])

    a = RuleAdjudicator(Settings(), complete=fake,
                        cache_path=tmp_path / "verdicts.json")
    return a, calls


def test_judge_parses_verdict_and_caches(tmp_path):
    adj, calls = _adj(tmp_path, [{"verdict": "same", "reason": "identical"}])
    v1 = adj.judge("amount >= 0", ["if (amt < 0)"], "no negative amounts", ["CHECK amt >= 0"])
    v2 = adj.judge("no negative amounts", ["CHECK amt >= 0"], "amount >= 0", ["if (amt < 0)"])
    assert v1 == v2 == "same"
    assert calls["n"] == 1 and adj.cache_hits == 1   # order-independent cache key


def test_cache_persists_across_instances(tmp_path):
    adj, calls = _adj(tmp_path, [{"verdict": "conflict", "reason": "boundary"}])
    adj.judge("a >= 0", [], "a > 0", [])
    adj.save()

    def boom(system, user):
        raise AssertionError("must not be called on cache hit")

    adj2 = RuleAdjudicator(Settings(), complete=boom,
                           cache_path=tmp_path / "verdicts.json")
    assert adj2.judge("a > 0", [], "a >= 0", []) == "conflict"


def test_unknown_verdict_normalizes_to_related(tmp_path):
    adj, _ = _adj(tmp_path, [{"verdict": "maybe?", "reason": ""}])
    assert adj.judge("x", [], "y", []) == "related"


def test_llm_failure_propagates(tmp_path):
    def broken(system, user):
        raise RuntimeError("llm down")

    adj = RuleAdjudicator(Settings(), complete=broken,
                          cache_path=tmp_path / "v.json")
    with pytest.raises(RuntimeError):
        adj.judge("x", [], "y", [])


def test_corrupt_cache_tolerated(tmp_path):
    p = tmp_path / "verdicts.json"
    p.write_text("{broken", encoding="utf-8")
    adj, _ = _adj(tmp_path, [{"verdict": "same", "reason": ""}])
    assert adj.judge("x", [], "y", []) == "same"


def test_non_dict_cache_tolerated(tmp_path):
    p = tmp_path / "verdicts.json"
    p.write_text("[]", encoding="utf-8")  # valid JSON, wrong shape
    adj, calls = _adj(tmp_path, [{"verdict": "same", "reason": ""}])
    assert adj.judge("x", [], "y", []) == "same"
    assert calls["n"] == 1 and adj.cache_hits == 0  # cache started empty
