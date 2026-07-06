"""Core data structures shared across the pipeline.

These are plain dataclasses (no business logic) so they can be passed between
the loader, splitters, extractor, store, and the API/CLI/MCP surfaces without
coupling them together.
"""

from __future__ import annotations

import hashlib
import json
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

# Entity/relation vocabularies for the knowledge graph (single source of truth
# shared by the extractor prompt and the graph builder). Keep in sync.
ENTITY_TYPES = (
    "Component", "Service", "Function", "Class", "API",
    "Concept", "Person/Team", "Resource",
)

RELATION_TYPES = (
    "depends_on", "calls", "owns", "part_of", "uses", "related_to",
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
    # Structured graph material extracted alongside the free-form concepts/
    # relations. Each entity is {"name", "type"}; each relation is
    # {"src", "dst", "type"}. Default empty so older indexes stay valid.
    entities: list[dict] = field(default_factory=list)
    typed_relations: list[dict] = field(default_factory=list)
    # Ordered procedure extracted from Workflow/Runbook chunks (see graph.workflow).
    # {"name", "prerequisites": [str], "steps": [{"order", "text", "precondition"}]}.
    workflow: dict = field(default_factory=dict)
    # Evidence trail: {"claim", "quote", "source", "start_line", "end_line", "verified"}.
    evidence: list[dict] = field(default_factory=list)
    # Status of evidence (e.g., "verified", "partial", "unverified").
    evidence_status: str = ""

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
    # Position of this chunk within its source document (set by the pipeline).
    # Used to order workflow steps across chunks. NOT part of content_hash/id.
    chunk_index: Optional[int] = None
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
            # Evidence serialization (JSON-encoded for complex list structure).
            if k.evidence:
                meta["evidence"] = json.dumps(k.evidence, ensure_ascii=False)
            if k.evidence_status:
                meta["evidence_status"] = k.evidence_status
        # Drop None and empty strings so Chroma metadata stays compact and old
        # filters keep matching (a missing key is treated as "not set").
        return {key: v for key, v in meta.items() if v is not None and v != ""}


@dataclass
class Article:
    """A synthesized, business-meaning article over several chunks.

    Duck-types the storage interface used by ``ChromaStore.upsert``/``search``
    (``id`` / ``text`` / ``embedding_text`` / ``metadata``) so articles reuse the
    same store with no special-casing. ``id`` is a content hash of the topic alone
    → exactly one article per topic, so re-synthesis under a shifting corpus
    overwrites it in place instead of accumulating a new row each run.
    """

    title: str
    topic: str
    body: str
    business_relevance: float = 0.0
    source_chunk_ids: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    cross_validated: bool = False
    critic_verdict: dict = field(default_factory=dict)

    @staticmethod
    def id_for_topic(topic: str) -> str:
        """The article id for a topic. The single source of the id formula, so the
        prune step can address an article by topic without building an Article."""
        return hashlib.sha256(topic.encode("utf-8")).hexdigest()

    @property
    def id(self) -> str:
        return Article.id_for_topic(self.topic)

    @property
    def text(self) -> str:
        return self.body

    def embedding_text(self) -> str:
        """Title + topic + body, so retrieval matches the article's subject."""
        return f"{self.title}\n{self.topic}\n{self.body}"

    def metadata(self) -> dict:
        v = self.critic_verdict or {}
        meta = {
            "kind": "article",
            "title": self.title,
            "topic": self.topic,
            "business_relevance": self.business_relevance,
            "cross_validated": self.cross_validated,
            "grounded": bool(v.get("grounded")),
            "business_meaningful": bool(v.get("business_meaningful")),
            "sources": " | ".join(self.sources),
            "source_chunk_ids": ", ".join(self.source_chunk_ids),
        }
        return {k: val for k, val in meta.items() if val is not None and val != ""}


@dataclass
class ChainItem:
    """End-to-end call-chain knowledge synthesized by chain analysis (4B).

    Duck-types the store contract (id/text/embedding_text/metadata) like
    Article; lives in the ``<collection>__chains`` sibling collection."""

    entry: str
    title: str
    body: str
    rules: list[str] = field(default_factory=list)
    members: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)       # "file:start-end"
    member_chunk_ids: list[str] = field(default_factory=list)
    truncated: bool = False
    # Evidence trail: {"claim", "quote", "source", "start_line", "end_line", "verified"}.
    evidence: list[dict] = field(default_factory=list)
    # Status of evidence (e.g., "verified", "partial", "unverified").
    evidence_status: str = ""

    @staticmethod
    def id_for_entry(entry: str) -> str:
        """Stable id for an entry point. Single source of the id formula."""
        return hashlib.sha256(entry.encode("utf-8")).hexdigest()

    @property
    def id(self) -> str:
        return ChainItem.id_for_entry(self.entry)

    @property
    def text(self) -> str:
        rules = "".join(f"\n- {r}" for r in self.rules)
        return f"{self.body}{rules}" if rules else self.body

    def embedding_text(self) -> str:
        """Title + body + rules + member names for semantic retrieval."""
        return f"{self.title}\n{self.text}\nFunctions: {', '.join(self.members)}"

    def metadata(self) -> dict:
        """Flat, JSON/Chroma-friendly metadata (no list or dict values)."""
        meta = {
            "kind": "chain",
            "title": self.title,
            "entry": self.entry,
            "members": ", ".join(self.members),
            "sources": " | ".join(self.sources),
            "rules": " | ".join(self.rules),
            "member_chunk_ids": ", ".join(self.member_chunk_ids),
            "truncated": self.truncated,
        }
        # Evidence serialization (JSON-encoded for complex list structure).
        if self.evidence:
            meta["evidence"] = json.dumps(self.evidence, ensure_ascii=False)
        if self.evidence_status:
            meta["evidence_status"] = self.evidence_status
        return {k: v for k, v in meta.items() if v is not None and v != ""}


@dataclass
class RuleItem:
    """A validated business rule synthesized from chunks and chains.

    Duck-types the store contract (id/text/embedding_text/metadata) like
    Article and ChainItem; lives in the ``<collection>__rules`` sibling collection."""

    statement: str
    trust: str = "normal"               # high | normal | conflicted
    corroborations: int = 1
    layers: list[str] = field(default_factory=list)
    member_keys: list[str] = field(default_factory=list)    # RuleUnit keys
    member_chunk_ids: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)        # "file:start-end" / entry names
    evidence: list[dict] = field(default_factory=list)      # union of member evidence entries
    evidence_status: str = ""
    review_status: str = "approved"

    @staticmethod
    def id_for_statement(statement: str) -> str:
        """Stable id for a rule statement. Single source of the id formula.
        Normalizes by stripping and lowercasing before hashing."""
        normalized = statement.strip().lower()
        digest = hashlib.sha256(f"rule:{normalized}".encode("utf-8"))
        return digest.hexdigest()

    @property
    def id(self) -> str:
        return RuleItem.id_for_statement(self.statement)

    @property
    def text(self) -> str:
        """Statement + corroboration summary."""
        summary = f"\nCorroborated by {self.corroborations} source{'s' if self.corroborations != 1 else ''}"
        return f"{self.statement}{summary}"

    def embedding_text(self) -> str:
        """Statement + trust/layers for semantic retrieval."""
        return f"{self.statement}\nTrust: {self.trust}\nLayers: {', '.join(self.layers)}"

    def metadata(self) -> dict:
        """Flat, JSON/Chroma-friendly metadata (no list or dict values)."""
        meta = {
            "kind": "rule",
            "statement": self.statement,
            "trust": self.trust,
            "corroborations": self.corroborations,
            "layers": ", ".join(self.layers),
            "member_keys": ", ".join(self.member_keys),
            "member_chunk_ids": ", ".join(self.member_chunk_ids),
            "sources": " | ".join(self.sources),
            "review_status": self.review_status,
        }
        # Evidence serialization (JSON-encoded for complex list structure).
        if self.evidence:
            meta["evidence"] = json.dumps(self.evidence, ensure_ascii=False)
        if self.evidence_status:
            meta["evidence_status"] = self.evidence_status
        return {k: v for k, v in meta.items() if v is not None and v != ""}


@dataclass
class SearchResult:
    id: str
    text: str
    score: float
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        ev = parse_evidence_field(self.metadata)
        if ev:
            d["evidence"] = ev
        return d


def parse_evidence_field(meta: dict) -> list[dict]:
    """Parse the JSON-string 'evidence' metadata field; [] on absence/corruption."""
    raw = meta.get("evidence")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []
