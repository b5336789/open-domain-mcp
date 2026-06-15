"""Core data structures shared across the pipeline.

These are plain dataclasses (no business logic) so they can be passed between
the loader, splitters, extractor, store, and the API/CLI/MCP surfaces without
coupling them together.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class KnowledgeUnit:
    """Domain knowledge extracted from a chunk by the LLM."""

    summary: str = ""
    concepts: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.summary or self.concepts or self.relations)


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
            meta["summary"] = self.knowledge.summary
            meta["concepts"] = ", ".join(self.knowledge.concepts)
            meta["relations"] = " | ".join(self.knowledge.relations)
        return {k: v for k, v in meta.items() if v is not None}


@dataclass
class SearchResult:
    id: str
    text: str
    score: float
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
