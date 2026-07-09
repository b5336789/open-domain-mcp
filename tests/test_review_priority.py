"""Risk-ordered review priority scoring (enhancement #3)."""

from opendomainmcp.review.priority import order_by_priority, priority_score


def test_scores():
    assert priority_score({"trust": "conflicted"}) == 0
    assert priority_score({"evidence_status": "unverified"}) == 1
    assert priority_score({"confidence": 0.3}) == 2
    assert priority_score({"confidence": 0.9}) == 3
    assert priority_score({}) == 3
    # conflicted beats unverified beats low-confidence
    assert priority_score({"trust": "conflicted",
                           "evidence_status": "unverified"}) == 0


def test_order_is_stable_within_a_tier():
    items = [{"id": "a", "metadata": {"confidence": 0.9}},
             {"id": "b", "metadata": {"trust": "conflicted"}},
             {"id": "c", "metadata": {"evidence_status": "unverified"}},
             {"id": "d", "metadata": {"confidence": 0.9}}]
    ordered = [i["id"] for i in order_by_priority(items)]
    assert ordered == ["b", "c", "a", "d"]   # b(0) c(1) then a,d(3) stable
