"""Eval case definitions and loaders.

An :class:`EvalCase` pins a query to the *evidence we expect a grounded system
to surface*: substrings that should appear in the retrieved sources and
substrings that a faithful answer should contain (typically copied/cited from
those sources). Keeping expectations as plain substrings makes the harness
deterministic and free of any model or network dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union


@dataclass
class EvalCase:
    """A single grounding expectation.

    ``expected_sources`` are substrings expected to appear in the *source
    identifiers* of retrieved evidence (e.g. a file path or ``path::symbol``).
    ``expected_answer`` are substrings a grounded answer should contain.
    """

    id: str
    query: str
    expected_sources: list[str] = field(default_factory=list)
    expected_answer: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "EvalCase":
        # Fail loud on a malformed case rather than silently dropping fields.
        missing = [k for k in ("id", "query") if k not in data]
        if missing:
            raise ValueError(f"eval case missing required field(s): {missing}")
        return cls(
            id=str(data["id"]),
            query=str(data["query"]),
            expected_sources=list(data.get("expected_sources", [])),
            expected_answer=list(data.get("expected_answer", [])),
        )


def load_evalset(path: Union[str, Path]) -> list[EvalCase]:
    """Load an eval set from ``.jsonl`` (one case per line) or ``.json``.

    A ``.json`` file may hold either a top-level list of cases or an object with
    a ``"cases"`` list. Blank lines in ``.jsonl`` are ignored.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".jsonl":
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        parsed = json.loads(text)
        records = parsed["cases"] if isinstance(parsed, dict) else parsed
    return [EvalCase.from_dict(r) for r in records]
