"""Deterministic evidence verification (enhancement #2).

The LLM reports only verbatim quotes — never line numbers (it cannot see
them). This verifier locates each quote in the known source text and
computes absolute line ranges from the chunk/function's known start line,
so a line number in stored evidence is a fact, not a claim. Quotes that
cannot be located (exactly or whitespace-normalized) are kept and flagged
unverified — never silently dropped (Fail Loud)."""

from __future__ import annotations

import re

UNVERIFIED_PENALTY = 0.5


def verify_evidence(evidence: list[dict], text: str, source: str,
                    base_line: int = 1) -> tuple[list[dict], str]:
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
        idx, matched = located
        start = base_line + text.count("\n", 0, idx)
        out.append({"claim": claim, "quote": quote, "source": source,
                    "start_line": start,
                    "end_line": start + matched.count("\n"),
                    "verified": True})
        verified_count += 1
    if verified_count == len(out):
        status = "verified"
    elif verified_count:
        status = "partial"
    else:
        status = "unverified"
    return out, status


def _locate(quote: str, text: str) -> tuple[int, str] | None:
    """(index, matched text) of ``quote`` in ``text``, or None."""
    if not quote.strip():
        return None
    idx = text.find(quote)
    if idx != -1:
        return idx, quote
    # whitespace-normalized: any whitespace run in the quote matches any
    # whitespace run (incl. newlines) in the text
    parts = [re.escape(p) for p in quote.split()]
    if not parts:
        return None
    m = re.search(r"\s+".join(parts), text)
    if m:
        return m.start(), m.group(0)
    return None


def apply_penalty(confidence: float, status: str) -> float:
    return confidence * UNVERIFIED_PENALTY if status == "unverified" else confidence
