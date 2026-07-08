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


# ---------------------------------------------------------------------------
# Fix 1: human review decisions preserved across re-runs
# ---------------------------------------------------------------------------

def test_human_approve_preserved_across_rerun(store, fake_graph, tmp_path):
    """Approve a rule, re-run consensus, review_status stays 'approved'."""
    _seed(store)
    adj = _same_adjudicator(tmp_path)
    run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj)

    # Mimic the API: human approves the rule.
    rules = store.get_items(limit=10, where={"kind": "rule"})
    assert len(rules) == 1
    rule_id = rules[0]["id"]
    store.update_metadata(rule_id, {"review_status": "approved"})

    # Re-run with cache-backed adjudicator (no LLM calls).
    adj2 = RuleAdjudicator(Settings(), cache_path=tmp_path / "v.json",
                           complete=lambda s, u: (_ for _ in ()).throw(
                               AssertionError("cache must hit")))
    run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj2)

    rules_after = store.get_items(limit=10, where={"kind": "rule"})
    assert len(rules_after) == 1
    assert rules_after[0]["metadata"]["review_status"] == "approved"


def test_human_reject_preserved_across_rerun(store, fake_graph, tmp_path):
    """Reject a rule, re-run consensus, review_status stays 'rejected'."""
    _seed(store)
    adj = _same_adjudicator(tmp_path)
    run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj)

    rules = store.get_items(limit=10, where={"kind": "rule"})
    rule_id = rules[0]["id"]
    store.update_metadata(rule_id, {"review_status": "rejected"})

    adj2 = RuleAdjudicator(Settings(), cache_path=tmp_path / "v.json",
                           complete=lambda s, u: (_ for _ in ()).throw(
                               AssertionError("cache must hit")))
    run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj2)

    rules_after = store.get_items(limit=10, where={"kind": "rule"})
    assert rules_after[0]["metadata"]["review_status"] == "rejected"


# ---------------------------------------------------------------------------
# Fix 4: pruned rules must remove graph rows
# ---------------------------------------------------------------------------

def test_pruned_rule_graph_rows_deleted(store, fake_graph, tmp_path):
    """Re-running consensus with a changed corpus prunes the old rule's graph
    entity from the graph store."""
    from opendomainmcp.models import Chunk, KnowledgeUnit

    # First run: two chunks whose claims hash to the same rule.
    claim_a = "amount must not be negative"
    claim_b = "amount should not be negative"
    _seed(store, claim_a=claim_a, claim_b=claim_b)
    adj = _same_adjudicator(tmp_path)
    r1 = run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj)
    assert r1["rules_created"] == 1

    # Confirm the rule entity landed in the graph.
    rules = store.get_items(limit=10, where={"kind": "rule"})
    old_rule_id = rules[0]["id"]
    assert fake_graph.get_entity(rules[0]["metadata"]["statement"]) is not None

    # Replace the corpus with entirely new claims → old rule id becomes stale.
    claim_c = "price must not exceed budget limit"
    claim_d = "price should not exceed budget maximum"
    # Remove old chunks by re-seeding with new content.
    existing_chunks = [i for i in store.get_items(limit=10)
                       if i["metadata"].get("kind") != "rule"]
    store.delete_ids({c["id"] for c in existing_chunks})

    for i, (claim, lang, src) in enumerate(
            [(claim_c, "java", "Pricing.java"), (claim_d, "plsql", "prices.pkb")]):
        k = KnowledgeUnit(summary="S", confidence=0.9, evidence=[
            {"claim": claim, "quote": f"q{i}", "source": src,
             "start_line": 1, "end_line": 1, "verified": True}])
        store.upsert([Chunk(text=f"q{i} price budget limit", source=src,
                            kind="code", language=lang, knowledge=k)])

    adj2 = _same_adjudicator(tmp_path)
    r2 = run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj2)
    assert r2["pruned"] == 1

    # The OLD rule id must no longer appear as a chunk_id in any graph entity.
    for entity_row in fake_graph.list_entities():
        full = fake_graph.get_entity(entity_row["name"])
        assert old_rule_id not in (full or {}).get("chunk_ids", []), (
            f"old rule id {old_rule_id} still in graph entity {entity_row['name']}"
        )


# ---------------------------------------------------------------------------
# Fix 5: graph failures surfaced in errors; >255-char statement truncated
# ---------------------------------------------------------------------------

def test_graph_upsert_failure_in_errors(store, tmp_path):
    """A graph store whose upsert_entities raises must surface the error in
    result['errors'], but the overall pass must still succeed (pass=True)."""
    from tests.conftest import FakeGraphStore

    class FailingGraph(FakeGraphStore):
        def upsert_entities(self, entities):
            raise RuntimeError("DB is down")

    _seed(store)
    adj = _same_adjudicator(tmp_path)
    result = run_consensus(store, Settings(), graph=FailingGraph(), adjudicator=adj)

    # Pass still succeeds (rules upserted).
    assert result["rules_created"] >= 1
    # Graph failure is visible in errors.
    graph_errors = [e for e in result["errors"] if e.get("stage") == "graph"]
    assert graph_errors, f"expected graph error in result['errors'], got {result['errors']}"


def test_long_statement_entity_name_truncated(store, fake_graph, tmp_path):
    """A rule whose statement exceeds 255 chars must produce a graph entity
    whose normalized_name is at most 255 chars."""
    from opendomainmcp.models import Chunk, KnowledgeUnit

    long_claim = "amount must not be " + ("very " * 60) + "negative"
    assert len(long_claim) > 255

    # Seed two similar long claims so they pair up and produce a rule.
    for i, (claim, lang, src) in enumerate([
            (long_claim, "java", "Billing.java"),
            (long_claim + " at all", "plsql", "pkg.pkb"),
    ]):
        k = KnowledgeUnit(summary="S", confidence=0.9, evidence=[
            {"claim": claim, "quote": f"q{i}", "source": src,
             "start_line": 1, "end_line": 1, "verified": True}])
        store.upsert([Chunk(text=f"q{i} " + " ".join(long_claim.split()[:6]),
                            source=src, kind="code", language=lang, knowledge=k)])

    adj = _same_adjudicator(tmp_path)
    run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj)

    for entity_row in fake_graph.list_entities(type="rule"):
        assert len(entity_row["normalized_name"]) <= 255, (
            f"entity normalized_name too long: {len(entity_row['normalized_name'])}"
        )


# ---------------------------------------------------------------------------
# Task 4: optional auto-approve of high-trust verified rules
# ---------------------------------------------------------------------------

def test_auto_approve_high_trust_verified_rules(store, fake_graph, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.consensus.adjudicate import RuleAdjudicator
    from opendomainmcp.consensus.run import run_consensus
    from opendomainmcp.review.audit import AuditLog

    _seed(store)  # two cross-layer chunks, verified evidence -> high trust
    settings = Settings(data_dir=tmp_path, review_auto_approve_high_trust=True)
    audit = AuditLog(tmp_path / "review_audit.db",
                     clock=lambda: "2026-07-08T00:00:00+00:00")
    result = run_consensus(store, settings, graph=fake_graph, audit=audit,
                           adjudicator=_same_adjudicator(tmp_path))
    assert result["auto_approved"] == 1
    rules = store.get_items(limit=10, where={"kind": "rule"})
    assert rules[0]["metadata"]["review_status"] == "approved"
    hist = audit.history(rules[0]["id"])
    assert hist and hist[0]["action"] == "auto-approve" and hist[0]["actor"] == "auto"


def test_auto_approve_off_by_default(store, fake_graph, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.consensus.run import run_consensus

    _seed(store)
    result = run_consensus(store, Settings(data_dir=tmp_path), graph=fake_graph,
                           adjudicator=_same_adjudicator(tmp_path))
    assert result.get("auto_approved", 0) == 0
    rules = store.get_items(limit=10, where={"kind": "rule"})
    assert rules[0]["metadata"]["review_status"] == "approved" or \
        rules[0]["metadata"]["review_status"] == "pending"  # per review_mode, not auto
