"""Deterministic quote-locating verifier (enhancement #2)."""

from opendomainmcp.extract.verify import UNVERIFIED_PENALTY, apply_penalty, verify_evidence

TEXT = "def charge(amt):\n    if amt < 0:\n        raise ValueError('neg')\n    return amt\n"


def _ev(quote, claim="amount must not be negative"):
    return [{"claim": claim, "quote": quote}]


def test_exact_match_computes_absolute_lines():
    out, status = verify_evidence(_ev("if amt < 0:"), TEXT, "billing.py", base_line=10)
    assert status == "verified"
    e = out[0]
    assert e["verified"] and e["source"] == "billing.py"
    assert e["start_line"] == 11 and e["end_line"] == 11
    assert e["claim"] == "amount must not be negative"


def test_whitespace_drift_still_verifies():
    # local models often collapse/expand whitespace when copying
    out, status = verify_evidence(_ev("if amt < 0:  raise ValueError('neg')"),
                                  TEXT, "billing.py", base_line=1)
    assert status == "verified"
    assert out[0]["start_line"] == 2 and out[0]["end_line"] == 3


def test_fabricated_quote_is_unverified_not_dropped():
    out, status = verify_evidence(_ev("if amount.is_negative():"), TEXT, "b.py")
    assert status == "unverified"
    assert out[0]["verified"] is False
    assert out[0]["start_line"] is None and out[0]["end_line"] is None
    assert len(out) == 1


def test_mixed_evidence_is_partial_and_order_preserved():
    ev = _ev("return amt") + _ev("nothing like this")
    out, status = verify_evidence(ev, TEXT, "b.py")
    assert status == "partial"
    assert out[0]["verified"] and not out[1]["verified"]


def test_empty_evidence_and_blank_quote():
    assert verify_evidence([], TEXT, "b.py") == ([], "")
    out, status = verify_evidence(_ev("   "), TEXT, "b.py")
    assert status == "unverified" and not out[0]["verified"]


def test_apply_penalty():
    assert apply_penalty(0.8, "unverified") == 0.8 * UNVERIFIED_PENALTY
    assert apply_penalty(0.8, "partial") == 0.8
    assert apply_penalty(0.8, "verified") == 0.8
    assert apply_penalty(0.8, "") == 0.8
