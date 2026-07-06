"""Union-find merge, trust tiers, conflict marking (enhancement #5)."""

from opendomainmcp.consensus.merge import merge_groups
from opendomainmcp.consensus.units import RuleUnit


def _u(key, claim, layer, chunk_ids=None, source="s"):
    return RuleUnit(key=key, claim=claim, origin=key.split(":")[0],
                    origin_id=key.split(":")[1], layer=layer, source=source,
                    chunk_ids=chunk_ids or [], evidence=[
                        {"claim": claim, "quote": f"q-{key}", "source": source,
                         "start_line": 1, "end_line": 1, "verified": True}])


def test_cross_layer_same_group_is_high_trust():
    units = [_u("chunk:a:0", "amount must not be negative", "service", ["ca"]),
             _u("chunk:b:0", "order amount cannot be negative at all", "db", ["cb"])]
    rules = merge_groups(units, [("chunk:a:0", "chunk:b:0", "same")])
    assert len(rules) == 1
    r = rules[0]
    assert r.trust == "high" and r.corroborations == 2
    assert r.statement == "order amount cannot be negative at all"  # longest claim
    assert set(r.layers) == {"db", "service"}
    assert set(r.member_chunk_ids) == {"ca", "cb"}
    assert len(r.evidence) == 2 and r.evidence_status == "verified"
    assert r.review_status == "approved"


def test_same_layer_group_is_normal():
    units = [_u("chunk:a:0", "rule one wording", "service"),
             _u("chunk:b:0", "rule one longer wording", "service")]
    rules = merge_groups(units, [("chunk:a:0", "chunk:b:0", "same")])
    assert rules[0].trust == "normal"


def test_conflict_marks_conflicted_and_pending():
    units = [_u("chunk:a:0", "amount must be >= 0", "service"),
             _u("chunk:b:0", "amount must be > 0", "db")]
    rules = merge_groups(units, [("chunk:a:0", "chunk:b:0", "conflict")])
    # conflict without same ⇒ two single-member conflicted rules
    assert len(rules) == 2
    assert all(r.trust == "conflicted" and r.review_status == "pending"
               for r in rules)


def test_singletons_without_verdicts_produce_nothing():
    units = [_u("chunk:a:0", "lonely rule", "docs")]
    assert merge_groups(units, []) == []


def test_related_does_not_merge():
    units = [_u("chunk:a:0", "rule A", "service"),
             _u("chunk:b:0", "rule B", "db")]
    assert merge_groups(units, [("chunk:a:0", "chunk:b:0", "related")]) == []


def test_review_mode_marks_pending():
    units = [_u("chunk:a:0", "r one", "service"), _u("chunk:b:0", "r one long", "db")]
    rules = merge_groups(units, [("chunk:a:0", "chunk:b:0", "same")],
                         review_mode=True)
    assert rules[0].review_status == "pending"
