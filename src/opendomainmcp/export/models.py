"""Plain dataclasses moved between export stages. No logic beyond parsing help."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ExportArticle:
    id: str
    title: str
    topic: str
    body: str
    sources: list[str] = field(default_factory=list)
    source_chunk_ids: list[str] = field(default_factory=list)
    untranslated: bool = False


@dataclass
class ExportRule:
    id: str
    statement: str
    trust: str = "normal"
    corroborations: int = 1
    layers: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    review_status: str = "approved"
    untranslated: bool = False


@dataclass
class ExportWorkflow:
    # ``name`` is the stable key used for slugs and outline references;
    # translation only ever touches ``display_name``/steps/prerequisites.
    name: str
    display_name: str
    prerequisites: list[str] = field(default_factory=list)
    # steps: [{"order": int, "text": str, "precondition": str, "chunk_id": str}]
    steps: list[dict] = field(default_factory=list)
    untranslated: bool = False


@dataclass
class ExportBundle:
    articles: list[ExportArticle] = field(default_factory=list)
    rules: list[ExportRule] = field(default_factory=list)
    workflows: list[ExportWorkflow] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    graph_enabled: bool = False


@dataclass
class OutlineFlow:
    workflow: str                    # ExportWorkflow.name
    articles: list[str] = field(default_factory=list)   # ExportArticle.topic
    rules: list[str] = field(default_factory=list)      # ExportRule.id


@dataclass
class OutlineDomain:
    name: str
    flows: list[OutlineFlow] = field(default_factory=list)
    articles: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)


@dataclass
class Outline:
    domains: list[OutlineDomain] = field(default_factory=list)
    # Computed leftovers (never trusted from the LLM):
    unassigned_articles: list[str] = field(default_factory=list)
    unassigned_workflows: list[str] = field(default_factory=list)
    unassigned_rules: list[str] = field(default_factory=list)


@dataclass
class ExportReport:
    counts: dict = field(default_factory=dict)
    translate_errors: list[dict] = field(default_factory=list)
    outline_warnings: list[str] = field(default_factory=list)
    unassigned: dict = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    out_dir: str = ""
    zip_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def split_pipe(value) -> list[str]:
    return [p.strip() for p in str(value or "").split("|") if p.strip()]


def split_comma(value) -> list[str]:
    return [p.strip() for p in str(value or "").split(",") if p.strip()]
