"""End-to-end consensus pass with fakes (enhancement #5).

Note: FakeEmbedder is a 64-dim bag-of-words embedder; cosine similarity
is exact for token-overlap pairs. Default claims are chosen so the pair
reaches the 0.80 threshold (4/5 shared tokens → sim = 0.80).
"""

import json

from opendomainmcp.config import Settings
from opendomainmcp.consensus.adjudicate import RuleAdjudicator
from opendomainmcp.consensus.run import run_consensus
from opendomainmcp.models import Chunk, KnowledgeUnit


def _seed(store, claim_a="amount must not be negative",
          claim_b="amount should not be negative"):
    # Default claims share 4 of 5 tokens → FakeEmbedder cosine = 0.80 (meets
    # the default threshold exactly).  Tests that need conflict use overrides.
    for i, (claim, lang, src) in enumerate(
            [(claim_a, "java", "Billing.java"), (claim_b, "plsql", "pkg.pkb")]):
        k = KnowledgeUnit(summary="S", confidence=0.9, evidence=[
            {"claim": claim, "quote": f"q{i}", "source": src,
             "start_line": 1, "end_line": 1, "verified": True}])
        store.upsert([Chunk(text=f"q{i} negative amount guard", source=src,
                            kind="code", language=lang, knowledge=k)])


def _same_adjudicator(tmp_path):
    return RuleAdjudicator(
        Settings(), cache_path=tmp_path / "v.json",
        complete=lambda s, u: json.dumps({"verdict": "same", "reason": "r"}))


def test_run_creates_high_trust_rule(store, fake_graph, tmp_path):
    _seed(store)
    result = run_consensus(store, Settings(), graph=fake_graph,
                           adjudicator=_same_adjudicator(tmp_path))
    assert result["rules_created"] == 1
    assert result["trust"]["high"] == 1 and result["errors"] == []

    rules = store.get_items(limit=10, where={"kind": "rule"})
    assert len(rules) == 1
    meta = rules[0]["metadata"]
    assert meta["trust"] == "high" and meta["corroborations"] == 2
    assert meta["evidence_status"] == "verified"


def test_rerun_hits_cache_and_prunes_stale(store, fake_graph, tmp_path):
    _seed(store)
    adj = _same_adjudicator(tmp_path)
    run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj)

    # second run: cache hit, same rule id, no dupes, nothing genuinely new
    adj2 = RuleAdjudicator(Settings(), cache_path=tmp_path / "v.json",
                           complete=lambda s, u: (_ for _ in ()).throw(
                               AssertionError("cache must hit")))
    r2 = run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj2)
    assert r2["cache_hits"] >= 1
    assert r2["rules_created"] == 0  # rule already existed; upserted, not new
    assert len(store.get_items(limit=10, where={"kind": "rule"})) == 1


def test_cache_hits_is_per_run_delta_with_reused_adjudicator(store, fake_graph,
                                                             tmp_path):
    # ONE adjudicator instance reused across runs: each run must report its
    # own cache-hit delta, not the instance's cumulative counter.
    _seed(store)
    adj = _same_adjudicator(tmp_path)

    r1 = run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj)
    assert r1["cache_hits"] == 0  # cold cache: every pair hit the LLM

    # Stored rules are excluded from unit collection, so candidates stay
    # stable across re-runs and runs 2/3 are fully warm — every candidate
    # pair is a hit.  A cumulative counter would report run2_hits + run3_hits
    # in run 3 and overshoot the pair count.
    r2 = run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj)
    assert r2["candidates"] == r1["candidates"]  # no self-amplification
    assert r2["cache_hits"] == r2["candidates"] >= 1
    r3 = run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj)
    assert r3["candidates"] == r1["candidates"]
    assert r3["cache_hits"] == r3["candidates"]


def test_total_adjudication_failure_preserves_prior_rules(store, fake_graph,
                                                          tmp_path):
    _seed(store)
    run_consensus(store, Settings(), graph=fake_graph,
                  adjudicator=_same_adjudicator(tmp_path))

    def broken(system, user):
        raise RuntimeError("llm down")

    adj = RuleAdjudicator(Settings(), cache_path=tmp_path / "other.json",
                          complete=broken)
    result = run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj)
    assert result["errors"] and result["rules_created"] == 0
    assert len(store.get_items(limit=10, where={"kind": "rule"})) == 1  # preserved


def test_conflict_creates_pending_rules_and_graph_edge(store, fake_graph,
                                                       tmp_path):
    # "amount must be >= 0" vs "> 0": 4/5 shared tokens → sim = 0.80 exactly
    _seed(store, claim_a="amount must be >= 0", claim_b="amount must be > 0")
    adj = RuleAdjudicator(
        Settings(), cache_path=tmp_path / "v.json",
        complete=lambda s, u: json.dumps({"verdict": "conflict", "reason": "r"}))
    result = run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj)
    assert result["conflicts"] >= 1 and result["trust"]["conflicted"] == 2

    pending = store.get_items(limit=10, where={"review_status": "pending"})
    assert any(i["metadata"].get("kind") == "rule" for i in pending)

    # both rule entities exist in the graph, connected by a "conflicts" edge
    rule_items = [i for i in pending if i["metadata"].get("kind") == "rule"]
    first_statement = rule_items[0]["metadata"]["statement"]
    ent = fake_graph.get_entity(first_statement)
    assert ent is not None

    # verify the conflicts edge exists via neighbors
    neighbors = fake_graph.neighbors(first_statement, relation_type="conflicts")
    assert len(neighbors["neighbors"]) >= 1
