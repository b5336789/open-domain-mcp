"""Deterministic evidence verification (enhancement #2).

The LLM reports only verbatim quotes — never line numbers (it cannot see
them). This verifier locates each quote in the known source text and
computes absolute line ranges from the chunk/function's known start line,
so a line number in stored evidence is a fact, not a claim. Quotes that
cannot be located (exactly or whitespace-normalized) are kept and flagged
unverified — never silently dropped (Fail Loud).

Note: repeated quotes anchor to the first occurrence in the text.

When ``base_line`` is None the chunk's origin line is unknown (e.g. plain
text chunks split from a file with no line-number tracking). Located quotes
are still marked ``verified: True`` to reflect that the quote was found in
the source text, but ``start_line`` and ``end_line`` are left as None so no
fabricated absolute line numbers are stored."""

from __future__ import annotations

import re
from typing import Optional

UNVERIFIED_PENALTY = 0.5


def verify_evidence(evidence: list[dict], text: str, source: str,
                    base_line: Optional[int] = 1) -> tuple[list[dict], str]:
    if not evidence:
        return [], ""
    out: list[dict] = []
    verified_count = 0
    for entry in evidence:
        quote = str(entry.get("quote", "") or "")
        claim = str(entry.get("claim", "") or "")
        located = _locate(quote, text)
        if located is None:
            out.append({"claim": claim, "quote": quote, "source": source,
                        "start_line": None, "end_line": None, "verified": False})
            continue
        if base_line is None:
            out.append({"claim": claim, "quote": quote, "source": source,
                        "start_line": None, "end_line": None, "verified": True})
        else:
            idx, matched = located
            start = base_line + text.count("\n", 0, idx)
            out.append({"claim": claim, "quote": quote, "source": source,
                        "start_line": start,
                        "end_line": start + matched.count("\n"),
                        "verified": True})
        verified_count += 1
    if verified_count > 0 and verified_count == len(out):
        status = "verified"
    elif verified_count:
        status = "partial"
    else:
        status = "unverified"
    return out, status


def verify_knowledge_evidence(knowledge, text: str, source: str,
                               start_line: Optional[int]) -> tuple[int, int]:
    """Verify knowledge.evidence in place; set evidence_status; penalize
    confidence. Returns (verified_count, unverified_count); (0, 0) when no
    evidence."""
    if not knowledge or not knowledge.evidence:
        return 0, 0
    verified, status = verify_evidence(knowledge.evidence, text, source,
                                       base_line=start_line)
    knowledge.evidence = verified
    knowledge.evidence_status = status
    knowledge.confidence = apply_penalty(knowledge.confidence, status)
    v = sum(1 for e in verified if e.get("verified"))
    u = len(verified) - v
    return v, u


def _locate(quote: str, text: str) -> tuple[int, str] | None:
    """(index, matched text) of ``quote`` in ``text``, or None."""
    if not quote.strip():
        return None
    idx = text.find(quote)
    if idx != -1:
        return idx, quote
    # stage 2 needs at least one word character to anchor the regex
    if not re.search(r"\w", quote):
        return None
    # whitespace-normalized: any whitespace run in the quote matches any
    # whitespace run (incl. newlines) in the text
    parts = [re.escape(p) for p in quote.split()]
    if not parts:
        return None
    if len(parts) > 30:  # worst-case backtracking guard
        return None
    m = re.search(r"\s+".join(parts), text)
    if m:
        return m.start(), m.group(0)
    return None


def apply_penalty(confidence: float, status: str) -> float:
    return confidence * UNVERIFIED_PENALTY if status == "unverified" else confidence
