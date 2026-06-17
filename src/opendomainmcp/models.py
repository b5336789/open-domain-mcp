"""Core data structures shared across the pipeline.

These are plain dataclasses (no business logic) so they can be passed between
the loader, splitters, extractor, store, and the API/CLI/MCP surfaces without
coupling them together.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Optional

# Allowed domain-knowledge classifications (single source of truth shared by the
# extractor prompt, the MCP views, and the web UI). Keep these in sync.
KNOWLEDGE_TYPES = (
    "Feature", "Workflow", "API", "Permission", "Constraint", "Error",
    "Troubleshooting", "Architecture", "Code", "Glossary", "Runbook", "FAQ",
)

# Intended consumer of a piece of knowledge.
AUDIENCES = (
    "product_manager", "solutions_architect", "operations", "engineering", "support",
)


@dataclass
class KnowledgeUnit:
    """Domain knowledge extracted from a chunk by the LLM.

    Beyond the free-form ``summary``/``concepts``/``relations``, knowledge is
    classified into a ``knowledge_type`` and ``audience`` so MCP views can serve
    role-specific slices, plus review/provenance fields. All fields default to
    empty so chunks from older indexes (and the ``NullExtractor``) stay valid.
    """

    summary: str = ""
    concepts: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    knowledge_type: str = ""
    audience: list[str] = field(default_factory=list)
    confidence: float = 0.0
    version: str = ""
    permissions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    # Review workflow state. New extractions default to "approved" so existing
    # behaviour is unchanged; an opt-in review mode (see Settings) sets "pending".
    review_status: str = "approved"

    def is_empty(self) -> bool:
        return not (
            self.summary or self.concepts or self.relations
            or self.knowledge_type or self.audience or self.tags
        )


@dataclass
class Chunk:
    """A unit of content to be embedded and stored.

    ``kind`` is ``"code"`` or ``"text"``. Code chunks carry AST metadata
    (language/node_type/symbol/lines); text chunks leave those as ``None``.
    """

    text: str
    source: str
    kind: str = "text"
    language: Optional[str] = None
    node_type: Optional[str] = None
    symbol: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    knowledge: Optional[KnowledgeUnit] = None

    @property
    def content_hash(self) -> str:
        """Stable hash of source + location + text for idempotent upserts."""
        loc = f"{self.source}:{self.start_line}-{self.end_line}"
        digest = hashlib.sha256(f"{loc}\n{self.text}".encode("utf-8"))
        return digest.hexdigest()

    @property
    def id(self) -> str:
        return self.content_hash

    def embedding_text(self) -> str:
        """Text fed to the embedder. Enriched with extracted knowledge so that
        retrieval matches on intent, not just surface tokens."""
        if self.knowledge and not self.knowledge.is_empty():
            parts = [self.text]
            if self.knowledge.summary:
                parts.append(f"Summary: {self.knowledge.summary}")
            if self.knowledge.concepts:
                parts.append("Concepts: " + ", ".join(self.knowledge.concepts))
            if self.knowledge.knowledge_type:
                parts.append(f"Type: {self.knowledge.knowledge_type}")
            if self.knowledge.tags:
                parts.append("Tags: " + ", ".join(self.knowledge.tags))
            return "\n".join(parts)
        return self.text

    def metadata(self) -> dict:
        """Flat, JSON/Chroma-friendly metadata (no None values)."""
        meta = {
            "source": self.source,
            "kind": self.kind,
            "language": self.language,
            "node_type": self.node_type,
            "symbol": self.symbol,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }
        if self.knowledge and not self.knowledge.is_empty():
            k = self.knowledge
            meta["summary"] = k.summary
            meta["concepts"] = ", ".join(k.concepts)
            meta["relations"] = " | ".join(k.relations)
            # Classification + review fields. Lists are flattened to strings
            # because Chroma metadata values must be scalars.
            meta["knowledge_type"] = k.knowledge_type
            meta["audience"] = ", ".join(k.audience)
            meta["confidence"] = k.confidence
            meta["version"] = k.version
            meta["permissions"] = ", ".join(k.permissions)
            meta["tags"] = ", ".join(k.tags)
            meta["references"] = " | ".join(k.references)
            meta["review_status"] = k.review_status
        # Drop None and empty strings so Chroma metadata stays compact and old
        # filters keep matching (a missing key is treated as "not set").
        return {key: v for key, v in meta.items() if v is not None and v != ""}


@dataclass
class SearchResult:
    id: str
    text: str
    score: float
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
