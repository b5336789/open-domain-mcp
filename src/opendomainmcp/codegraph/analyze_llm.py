"""LLM prompts for chain analysis (plan 4B).

Two calls: a per-function summary (bottom-up, with 1-hop callee source and
deeper summaries as context) and a per-chain end-to-end synthesis. The LLM
transport is an injectable ``complete(system, user) -> str`` so tests run
offline; the default transport reuses the extraction provider settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..extract.knowledge import parse_llm_json

_FUNC_SYSTEM = (
    "You analyze one function from a business application, with context from "
    "the functions it calls. Respond with ONLY a JSON object:\n"
    '  "summary": 1-2 sentences on what the function does in business terms,\n'
    '  "rules": a list of short business rules/constraints enforced here '
    "(may be empty),\n"
    '  "confidence": a number 0..1.\n'
    "No prose outside the JSON."
)

_CHAIN_SYSTEM = (
    "You are given an end-to-end call chain from a business application: an "
    "entry point followed by the functions it reaches (with per-function "
    "summaries). Respond with ONLY a JSON object:\n"
    '  "title": a short name for this business flow,\n'
    '  "body": a paragraph describing the end-to-end workflow across layers,\n'
    '  "rules": business rules/constraints enforced anywhere along the chain.\n'
    "No prose outside the JSON."
)


@dataclass
class FunctionSummary:
    qualified_name: str
    summary: str
    rules: list[str] = field(default_factory=list)
    confidence: float = 0.0


def _default_complete(settings) -> Callable[[str, str], str]:
    provider = settings.resolved_extract_provider()
    base_url = settings.extract_base_url or None
    if provider == "openai":
        from openai import OpenAI

        client = OpenAI(timeout=settings.request_timeout,
                        max_retries=settings.max_retries,
                        **({"base_url": base_url} if base_url else {}))

        def complete(system: str, user: str) -> str:
            resp = client.chat.completions.create(
                model=settings.extraction_model, max_tokens=1200,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}])
            return resp.choices[0].message.content or ""

        return complete

    import anthropic

    client = anthropic.Anthropic(timeout=settings.request_timeout,
                                 max_retries=settings.max_retries,
                                 **({"base_url": base_url} if base_url else {}))

    def complete(system: str, user: str) -> str:
        msg = client.messages.create(model=settings.extraction_model,
                                     max_tokens=1200, system=system,
                                     messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in msg.content if b.type == "text")

    return complete


class ChainAnalyzer:
    def __init__(self, settings,
                 complete: Optional[Callable[[str, str], str]] = None):
        self._settings = settings
        self._complete = complete or _default_complete(settings)

    def summarize_function(self, fn, source: str,
                           callee_sources: dict[str, str],
                           callee_summaries: dict[str, "FunctionSummary"],
                           ) -> FunctionSummary:
        parts = [f"Function: {fn.qualified_name} ({fn.language})",
                 f"Source:\n{source}"]
        for name, src in callee_sources.items():
            parts.append(f"\nDirect callee {name}:\n{src}")
        for name, fs in callee_summaries.items():
            parts.append(f"\nDeeper callee {name} (summary): {fs.summary}")
        data = parse_llm_json(self._complete(_FUNC_SYSTEM, "\n".join(parts)))
        return FunctionSummary(
            qualified_name=fn.qualified_name,
            summary=str(data.get("summary", "")).strip(),
            rules=[str(r).strip() for r in data.get("rules", []) if str(r).strip()],
            confidence=float(data.get("confidence", 0.0) or 0.0),
        )

    def analyze_chain(self, chain, summaries: dict[str, FunctionSummary]) -> dict:
        lines = [f"Entry point: {chain.entry}"]
        for member in chain.members:
            fs = summaries.get(member)
            lines.append(f"- {member}: {fs.summary if fs else '(no summary)'}")
            if fs and fs.rules:
                for rule in fs.rules:
                    lines.append(f"    rule: {rule}")
        if chain.truncated:
            lines.append("(note: chain truncated by cycle/depth limit)")
        data = parse_llm_json(self._complete(_CHAIN_SYSTEM, "\n".join(lines)))
        return {
            "title": str(data.get("title", chain.entry)).strip() or chain.entry,
            "body": str(data.get("body", "")).strip(),
            "rules": [str(r).strip() for r in data.get("rules", [])
                      if str(r).strip()],
        }
