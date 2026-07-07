"""RuleItem model + rule-unit collection (enhancement #5)."""

import json

from opendomainmcp.models import ChainItem, Chunk, KnowledgeUnit, RuleItem
from opendomainmcp.consensus.units import collect_rule_units

EV = {"claim": "amount must not be negative", "quote": "if (amt < 0)",
      "source": "Billing.java", "start_line": 5, "end_line": 5, "verified": True}


def test_rule_item_contract():
    r = RuleItem(statement="Amount must not be negative", trust="high",
                 corroborations=2, layers=["service", "db"],
                 member_chunk_ids=["c1", "c2"], sources=["A.java:1-5"],
                 evidence=[EV], evidence_status="verified")
    assert r.id == RuleItem.id_for_statement("Amount must not be negative")
    assert r.id == RuleItem.id_for_statement("  amount must not be NEGATIVE ")
    meta = r.metadata()
    assert meta["kind"] == "rule" and meta["trust"] == "high"
    assert meta["corroborations"] == 2
    assert json.loads(meta["evidence"])[0]["claim"] == EV["claim"]
    assert all(not isinstance(v, (list, dict)) for v in meta.values())
    assert "Amount must not be negative" in r.text and "2" in r.text


def test_collect_units_from_chunks_and_chains(store):
    k = KnowledgeUnit(summary="S", knowledge_type="Code", confidence=0.9,
                      evidence=[EV], evidence_status="verified")
    store.upsert([Chunk(text="if (amt < 0) throw", source="Billing.java",
                        kind="code", language="java", knowledge=k)])
    chains = store.sibling(f"{store.stats()['collection']}__chains")
    chains.upsert([ChainItem(entry="api.charge", title="T", body="B",
                             rules=["amount must not be negative"],
                             member_chunk_ids=["c9"],
                             evidence=[EV], evidence_status="verified")])

    units = collect_rule_units(store)
    origins = {u.origin for u in units}
    assert origins == {"chunk", "chain"}
    chunk_unit = next(u for u in units if u.origin == "chunk")
    assert chunk_unit.layer == "service" and chunk_unit.claim == EV["claim"]
    assert chunk_unit.evidence and chunk_unit.chunk_ids
    chain_unit = next(u for u in units if u.origin == "chain")
    assert chain_unit.layer == "chain" and chain_unit.chunk_ids == ["c9"]
    assert chain_unit.source == "api.charge"


def test_collect_units_skips_claimless_and_paginates(store):
    from opendomainmcp.models import Chunk, KnowledgeUnit

    for i in range(7):
        k = KnowledgeUnit(summary="S", evidence=[
            {"claim": f"rule {i}", "quote": f"q{i}", "source": "a.sql",
             "start_line": 1, "end_line": 1, "verified": True}])
        store.upsert([Chunk(text=f"q{i} body", source=f"p{i}.sql", kind="code",
                            language="plsql", knowledge=k)])
    store.upsert([Chunk(text="no evidence here", source="plain.md", kind="text")])

    units = collect_rule_units(store, page_size=3)   # forces pagination
    assert len(units) == 7
    assert all(u.layer == "db" for u in units)


def test_collect_units_excludes_canonical_rules(store):
    # A prior consensus pass stored a RuleItem (kind="rule", evidence-bearing)
    # in the main collection.  Rules are consensus OUTPUTS: re-collecting them
    # as inputs would self-amplify candidates on every re-run.
    k = KnowledgeUnit(summary="S", confidence=0.9, evidence=[EV],
                      evidence_status="verified")
    store.upsert([Chunk(text="if (amt < 0) throw", source="Billing.java",
                        kind="code", language="java", knowledge=k)])
    store.upsert([RuleItem(statement="amount must not be negative",
                           trust="high", corroborations=2,
                           member_chunk_ids=["c1", "c2"],
                           evidence=[EV], evidence_status="verified")])

    units = collect_rule_units(store)
    assert len(units) == 1
    assert units[0].origin == "chunk" and units[0].source == "Billing.java"
