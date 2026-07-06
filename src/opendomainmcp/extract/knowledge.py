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
    '  "evidence": a list of {"claim", "quote"} objects — for each important concept, relation, or constraint, "quote" is an EXACT contiguous snippet copied character-for-character from the snippet above that supports the "claim" (may be empty),\n'
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


def _parse_evidence(value) -> list[dict]:
    """Normalize LLM evidence to [{"claim", "quote"}]; quoteless entries drop."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str):
            claim, quote = "", item
        elif isinstance(item, dict):
            claim = str(item.get("claim", "") or "").strip()
            quote = str(item.get("quote", "") or "")
        else:
            continue
        if quote.strip():
            out.append({"claim": claim, "quote": quote})
    return out


class ExtractionError(Exception):
    pass


# JSON schema for the extraction output. Sent as OpenAI ``response_format`` so
# servers that support structured output (LM Studio, vLLM, OpenAI) constrain
# generation to valid JSON — eliminating malformed-JSON failures at the source,
# which local models otherwise emit ~10-15% of the time. Types are permissive
# (the enums are normalised in ``_parse``); only the always-present keys are
# required so the model can omit the optional structures.
_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "concepts": {"type": "array", "items": {"type": "string"}},
        "relations": {"type": "array", "items": {"type": "string"}},
        "knowledge_type": {"type": "string"},
        "audience": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "version": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "permissions": {"type": "array", "items": {"type": "string"}},
        "references": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "type": {"type": "string"}},
            "required": ["name", "type"]}},
        "typed_relations": {"type": "array", "items": {
            "type": "object",
            "properties": {"src": {"type": "string"}, "dst": {"type": "string"},
                           "type": {"type": "string"}},
            "required": ["src", "dst", "type"]}},
        "workflow": {"type": "object"},
    },
    "required": ["summary", "concepts", "relations", "knowledge_type",
                 "audience", "confidence"],
}

KNOWLEDGE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "knowledge_unit", "strict": True, "schema": _JSON_SCHEMA},
}


def _loads_lenient(candidate: str) -> dict:
    """Parse model JSON, repairing the malformations local models routinely emit
    (missing commas, unquoted keys, missing colons, unescaped inner quotes,
    trailing commas, truncation). Tries strict ``json.loads`` first for the happy
    path, then ``json-repair`` which handles those structural errors. Re-raises
    the original ``JSONDecodeError`` (Fail Loud) if repair yields no usable
    object, so genuine non-JSON is still recorded as a failure rather than stored
    as empty knowledge."""
    try:
        return json.loads(candidate, strict=False)
    except json.JSONDecodeError:
        from json_repair import repair_json

        repaired = repair_json(candidate, return_objects=True)
        if isinstance(repaired, dict) and repaired:
            return repaired
        raise


def parse_llm_json(raw: str) -> dict:
    """Extract and parse the JSON object from an LLM reply.

    Handles markdown code fences and lenient/repaired JSON — the shared
    tolerance layer for every LLM-JSON call site (extraction, chain analysis).
    Raises ExtractionError if no JSON object is found; raises JSONDecodeError
    (Fail Loud) if parsing cannot be repaired."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        # drop an optional leading 'json' language tag
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start = text.find("{")
    if start == -1:
        raise ExtractionError(f"No JSON object in model output: {raw[:120]!r}")
    # Use the last closing brace when present; otherwise (truncated output) take
    # everything from the first ``{`` and let the repair pass close it.
    end = text.rfind("}")
    candidate = text[start: end + 1] if end > start else text[start:]
    # _loads_lenient parses with strict=False (tolerating literal control
    # characters inside string values, which some models — local
    # OpenAI-compatible ones especially — emit instead of escaping them) and
    # adds malformation repair on top.
    return _loads_lenient(candidate)


def _parse(raw: str) -> KnowledgeUnit:
    data = parse_llm_json(raw)
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
        evidence=_parse_evidence(data.get("evidence")),
    )


def _is_bad_request(exc: Exception) -> bool:
    """True if ``exc`` looks like an HTTP 400 (unsupported param) rather than a
    transient/auth error — so we only fall back from structured output when the
    endpoint genuinely rejects it, and let real errors propagate."""
    if getattr(exc, "status_code", None) == 400:
        return True
    return exc.__class__.__name__ == "BadRequestError"


class NullExtractor:
    """No-op extractor used when knowledge extraction is disabled."""

    def extract(self, text: str, kind: str, language=None) -> KnowledgeUnit:
        return KnowledgeUnit()


class ClaudeExtractor:
    def __init__(self, model: str, max_tokens: int = 900,
                 timeout: float = 60.0, max_retries: int = 2,
                 base_url: str | None = None):
        import anthropic

        # timeout bounds a single call; max_retries lets the SDK back off and
        # retry transient errors (overloaded / network) rather than hang or fail.
        # base_url (when set) points at a custom Anthropic-compatible endpoint,
        # overriding ANTHROPIC_BASE_URL for extraction only.
        kwargs = {"timeout": timeout, "max_retries": max_retries}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**kwargs)
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


class OpenAIExtractor:
    """Extractor backed by the OpenAI chat-completions API, so any
    OpenAI-compatible endpoint (e.g. a local LM Studio / vLLM server, via
    ``OPENAI_BASE_URL``) can perform extraction. ``client`` is injectable for
    tests; otherwise a client is built lazily from the environment."""

    def __init__(self, model: str, max_tokens: int = 900,
                 timeout: float = 60.0, max_retries: int = 2, client=None,
                 structured: bool = False, base_url: str | None = None):
        if client is None:
            from openai import OpenAI

            # OpenAI() reads OPENAI_API_KEY / OPENAI_BASE_URL from the env;
            # base_url (when set) overrides OPENAI_BASE_URL for extraction only.
            kwargs = {"timeout": timeout, "max_retries": max_retries}
            if base_url:
                kwargs["base_url"] = base_url
            client = OpenAI(**kwargs)
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        # When enabled, request json_schema structured output; flip off
        # permanently if the endpoint rejects the param. Off by default because
        # constrained decoding is slow on some local servers (see config).
        self._structured = structured

    def extract(self, text: str, kind: str, language=None) -> KnowledgeUnit:
        label = f"{kind}" + (f" ({language})" if language else "")
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Snippet type: {label}\n\n{text}"},
        ]
        kwargs = {"model": self._model, "max_tokens": self._max_tokens,
                  "messages": messages}
        if self._structured:
            try:
                resp = self._client.chat.completions.create(
                    response_format=KNOWLEDGE_RESPONSE_FORMAT, **kwargs)
            except Exception as exc:  # endpoint rejects json_schema -> fall back
                if not _is_bad_request(exc):
                    raise
                logger.warning(
                    "structured output unsupported (%s); falling back to "
                    "plain JSON prompt for extraction", exc)
                self._structured = False
                resp = self._client.chat.completions.create(**kwargs)
        else:
            resp = self._client.chat.completions.create(**kwargs)
        raw = resp.choices[0].message.content or ""
        return _parse(raw)


def get_extractor(settings: Settings):
    if not settings.extract_knowledge:
        return NullExtractor()
    base_url = settings.extract_base_url or None
    if settings.resolved_extract_provider() == "openai":
        return OpenAIExtractor(
            settings.extraction_model,
            timeout=settings.request_timeout,
            max_retries=settings.max_retries,
            structured=settings.extract_structured_output,
            base_url=base_url,
        )
    return ClaudeExtractor(
        settings.extraction_model,
        timeout=settings.request_timeout,
        max_retries=settings.max_retries,
        base_url=base_url,
    )
