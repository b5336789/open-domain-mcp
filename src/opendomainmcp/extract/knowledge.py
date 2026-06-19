"""Domain-knowledge extraction.

For each chunk, Claude produces a compact structured summary (summary / key
concepts / relationships). That structure is stored as metadata and folded into
the embedding text so retrieval matches on meaning, not just surface tokens.

Extractors implement ``extract(text, kind, language) -> KnowledgeUnit``. The
``NullExtractor`` is used when extraction is disabled. The Anthropic client reads
``ANTHROPIC_API_KEY`` / ``ANTHROPIC_BASE_URL`` from the environment.
"""

from __future__ import annotations

import json
import logging

from ..config import Settings
from ..models import (
    AUDIENCES,
    ENTITY_TYPES,
    KNOWLEDGE_TYPES,
    RELATION_TYPES,
    KnowledgeUnit,
)

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You extract reusable domain knowledge from a single snippet of a document or "
    "source code. Respond with ONLY a JSON object with these keys:\n"
    '  "summary": one or two sentences describing what this snippet is about,\n'
    '  "concepts": a list of 1-8 short domain terms or entities it introduces,\n'
    '  "relations": a list of short "A -> B" statements describing relationships '
    "(may be empty),\n"
    '  "knowledge_type": exactly one of ' + ", ".join(KNOWLEDGE_TYPES) + ",\n"
    '  "audience": a list of the roles this helps, each one of '
    + ", ".join(AUDIENCES) + ",\n"
    '  "confidence": a number from 0 to 1 for how confident you are,\n'
    '  "version": a version or release identifier if the snippet clearly '
    'references one, else an empty string,\n'
    '  "tags": a list of 0-6 short free-form tags (may be empty),\n'
    '  "permissions": a list of permissions/roles required, if any (may be empty),\n'
    '  "references": a list of external identifiers it cites such as URLs, ticket '
    "or error codes (may be empty),\n"
    '  "entities": a list of {"name", "type"} for the key entities, each type one of '
    + ", ".join(ENTITY_TYPES) + " (may be empty),\n"
    '  "typed_relations": a list of {"src", "dst", "type"} directed relations '
    "between entity names, each type one of "
    + ", ".join(RELATION_TYPES) + " (may be empty),\n"
    '  "workflow": if this snippet is a runbook, workflow, or step-by-step '
    'procedure, an object {"name": a short title, "prerequisites": [conditions '
    'that must hold before starting], "steps": [{"order": 1-based integer, '
    '"text": what to do, "precondition": an optional condition for this step}]}; '
    'otherwise an empty object {}.\n'
    "Do not include any prose outside the JSON object."
)


def _clamp_confidence(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _norm_choice(value, allowed: tuple[str, ...]) -> str:
    """Return ``value`` if it matches an allowed term (case-insensitive), else ''."""
    text = str(value).strip()
    lower = {a.lower(): a for a in allowed}
    return lower.get(text.lower(), "")


def _norm_choice_default(value, allowed: tuple[str, ...], default: str) -> str:
    """Like _norm_choice but falls back to ``default`` instead of '' for
    unknown values (the model occasionally invents type names)."""
    return _norm_choice(value, allowed) or default


def _parse_entities(values) -> list[dict]:
    if not isinstance(values, list):
        return []
    out = []
    for v in values:
        if not isinstance(v, dict):
            continue
        name = str(v.get("name", "")).strip()
        if not name:
            continue
        out.append({"name": name,
                    "type": _norm_choice_default(v.get("type", ""), ENTITY_TYPES, "Concept")})
    return out


def _parse_relations(values) -> list[dict]:
    if not isinstance(values, list):
        return []
    out = []
    for v in values:
        if not isinstance(v, dict):
            continue
        src, dst = str(v.get("src", "")).strip(), str(v.get("dst", "")).strip()
        if not src or not dst:
            continue
        out.append({"src": src, "dst": dst,
                    "type": _norm_choice_default(v.get("type", ""), RELATION_TYPES, "related_to")})
    return out


def _parse_workflow(value) -> dict:
    """Normalize the optional ``workflow`` object. Requires a name and at least
    one non-empty step, else returns {} (the snippet is not a real procedure)."""
    if not isinstance(value, dict):
        return {}
    name = str(value.get("name", "")).strip()
    raw_steps = value.get("steps", [])
    steps = []
    if isinstance(raw_steps, list):
        for i, s in enumerate(raw_steps, start=1):
            if not isinstance(s, dict):
                continue
            text = str(s.get("text", "")).strip()
            if not text:
                continue
            try:
                order = int(s.get("order", i))
            except (TypeError, ValueError):
                order = i
            steps.append({"order": order, "text": text,
                          "precondition": str(s.get("precondition", "")).strip()})
    if not name or not steps:
        return {}
    return {"name": name, "prerequisites": _str_list(value.get("prerequisites", [])),
            "steps": steps}


def _str_list(values) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(v).strip() for v in values if str(v).strip()]


class ExtractionError(Exception):
    pass


def _parse(raw: str) -> KnowledgeUnit:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        # drop an optional leading 'json' language tag
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ExtractionError(f"No JSON object in model output: {raw[:120]!r}")
    data = json.loads(text[start: end + 1])
    # Audience may come back as a single string or a list; normalise to a list
    # and drop anything outside the allowed vocabulary (Fail Loud is too harsh
    # here — the model occasionally invents terms; we keep the valid ones).
    raw_audience = data.get("audience", [])
    if isinstance(raw_audience, str):
        raw_audience = [raw_audience]
    audience = [a for a in (_norm_choice(x, AUDIENCES) for x in _str_list(raw_audience)) if a]
    return KnowledgeUnit(
        summary=str(data.get("summary", "")).strip(),
        concepts=_str_list(data.get("concepts", [])),
        relations=_str_list(data.get("relations", [])),
        knowledge_type=_norm_choice(data.get("knowledge_type", ""), KNOWLEDGE_TYPES),
        audience=audience,
        confidence=_clamp_confidence(data.get("confidence", 0.0)),
        version=str(data.get("version", "")).strip(),
        permissions=_str_list(data.get("permissions", [])),
        tags=_str_list(data.get("tags", [])),
        references=_str_list(data.get("references", [])),
        entities=_parse_entities(data.get("entities", [])),
        typed_relations=_parse_relations(data.get("typed_relations", [])),
        workflow=_parse_workflow(data.get("workflow", {})),
    )


class NullExtractor:
    """No-op extractor used when knowledge extraction is disabled."""

    def extract(self, text: str, kind: str, language=None) -> KnowledgeUnit:
        return KnowledgeUnit()


class ClaudeExtractor:
    def __init__(self, model: str, max_tokens: int = 900,
                 timeout: float = 60.0, max_retries: int = 2):
        import anthropic

        # timeout bounds a single call; max_retries lets the SDK back off and
        # retry transient errors (overloaded / network) rather than hang or fail.
        self._client = anthropic.Anthropic(timeout=timeout, max_retries=max_retries)
        self._model = model
        self._max_tokens = max_tokens

    def extract(self, text: str, kind: str, language=None) -> KnowledgeUnit:
        label = f"{kind}" + (f" ({language})" if language else "")
        message = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Snippet type: {label}\n\n{text}",
            }],
        )
        raw = "".join(
            block.text for block in message.content if block.type == "text"
        )
        return _parse(raw)


def get_extractor(settings: Settings):
    if not settings.extract_knowledge:
        return NullExtractor()
    return ClaudeExtractor(
        settings.extraction_model,
        timeout=settings.request_timeout,
        max_retries=settings.max_retries,
    )
