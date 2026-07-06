"""LLM rule adjudication with content-hash verdict cache.

Adjudicate pairs of claims using an LLM to classify their relationship as:
- "same": identical business rules
- "related": same topic, different rules
- "conflict": contradictory constraints

Verdicts are cached by order-independent pair hash and persisted to
<data_dir>/.consensus/verdicts.json (atomic write).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Callable, Optional

from ..codegraph.analyze_llm import _default_complete
from ..extract.knowledge import parse_llm_json

logger = logging.getLogger(__name__)

VERDICTS = ("same", "related", "conflict")

_SYSTEM = (
    "You are a rule adjudicator. Given two claims with supporting quotes, "
    "classify their relationship.\n\n"
    "Respond with ONLY a JSON object:\n"
    '{"verdict": "same" | "related" | "conflict", "reason": "brief explanation"}\n\n'
    '"same" = the two statements express the SAME business rule\n'
    '"related" = same topic, different rules\n'
    '"conflict" = contradictory constraints (e.g., ">= 0" vs "> 0")\n'
    "Do not include any prose outside the JSON object."
)


class RuleAdjudicator:
    """LLM-based rule pair adjudicator with verdict cache."""

    def __init__(self, settings, complete: Optional[Callable[[str, str], str]] = None,
                 cache_path: Optional[Path] = None):
        """Initialize adjudicator.

        Args:
            settings: Settings object (for data_dir and LLM config)
            complete: Optional LLM completion function (system, user) -> str.
                     Defaults to _default_complete(settings).
            cache_path: Optional cache file path. Defaults to
                       <data_dir>/.consensus/verdicts.json
        """
        self.settings = settings
        self._complete = complete or _default_complete(settings)

        if cache_path is None:
            cache_path = Path(settings.data_dir) / ".consensus" / "verdicts.json"
        self.cache_path = Path(cache_path)

        self._cache: dict[str, str] = {}
        self._cache_hits = 0

        # Lazy load cache (tolerate missing/corrupt files)
        self._load_cache()

    def _load_cache(self) -> None:
        """Load cache from disk, tolerating missing or corrupt files."""
        if not self.cache_path.exists():
            return

        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._cache = data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Corrupt or unreadable cache {self.cache_path}: {e}; "
                          f"starting with empty cache")

    @staticmethod
    def pair_key(claim_a: str, claim_b: str) -> str:
        """Order-independent SHA256 hash of a claim pair."""
        # Sort claims to make key order-independent
        pair = tuple(sorted([claim_a, claim_b]))
        text = "\n".join(pair)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def judge(self, claim_a: str, quotes_a: list[str],
              claim_b: str, quotes_b: list[str]) -> str:
        """Adjudicate a pair of claims.

        Returns a VERDICT string ("same", "related", or "conflict").
        Caches by order-independent pair hash; cache hit skips the LLM.

        Args:
            claim_a: First claim string
            quotes_a: Supporting quotes for claim_a (truncated to ~300 chars each)
            claim_b: Second claim string
            quotes_b: Supporting quotes for claim_b (truncated to ~300 chars each)

        Returns:
            A verdict string from VERDICTS, or "related" if the LLM returns
            an unknown verdict string.

        Raises:
            RuntimeError or other exceptions from the LLM if completion fails.
        """
        key = self.pair_key(claim_a, claim_b)

        # Cache hit
        if key in self._cache:
            self._cache_hits += 1
            return self._cache[key]

        # Call LLM with truncated quotes (~300 chars each)
        def truncate(s: str, limit: int = 300) -> str:
            return s[:limit] if len(s) > limit else s

        quotes_a_text = "\n".join(f"- {truncate(q)}" for q in quotes_a)
        quotes_b_text = "\n".join(f"- {truncate(q)}" for q in quotes_b)

        user_msg = (
            f"Claim A: {claim_a}\n"
            f"Quotes for A:\n{quotes_a_text if quotes_a else '(none)'}\n\n"
            f"Claim B: {claim_b}\n"
            f"Quotes for B:\n{quotes_b_text if quotes_b else '(none)'}"
        )

        # LLM exceptions propagate (Fail Loud)
        raw_response = self._complete(_SYSTEM, user_msg)

        # Parse JSON response
        data = parse_llm_json(raw_response)
        verdict = str(data.get("verdict", "")).strip().lower()

        # Normalize unknown verdict to "related" (safe middle ground)
        if verdict not in VERDICTS:
            logger.warning(f"Unknown verdict from LLM: {verdict!r}; using 'related'")
            verdict = "related"

        # Cache and return
        self._cache[key] = verdict
        return verdict

    @property
    def cache_hits(self) -> int:
        """Number of cache hits so far."""
        return self._cache_hits

    def save(self) -> None:
        """Persist cache to disk (atomic write via temp file + os.replace)."""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: temp file + os.replace
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        tmp.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")
        os.replace(tmp, self.cache_path)
