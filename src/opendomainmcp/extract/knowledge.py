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
from ..models import KnowledgeUnit

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You extract reusable domain knowledge from a single snippet of a document or "
    "source code. Respond with ONLY a JSON object with these keys:\n"
    '  "summary": one or two sentences describing what this snippet is about,\n'
    '  "concepts": a list of 1-8 short domain terms or entities it introduces,\n'
    '  "relations": a list of short "A -> B" statements describing relationships '
    "(may be empty).\n"
    "Do not include any prose outside the JSON object."
)


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
    return KnowledgeUnit(
        summary=str(data.get("summary", "")).strip(),
        concepts=[str(c).strip() for c in data.get("concepts", []) if str(c).strip()],
        relations=[str(r).strip() for r in data.get("relations", []) if str(r).strip()],
    )


class NullExtractor:
    """No-op extractor used when knowledge extraction is disabled."""

    def extract(self, text: str, kind: str, language=None) -> KnowledgeUnit:
        return KnowledgeUnit()


class ClaudeExtractor:
    def __init__(self, model: str, max_tokens: int = 600,
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
