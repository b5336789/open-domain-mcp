from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Article
from .llm import get_article_llms, keep_article
from .topics import gather_topics


@dataclass
class SynthesisReport:
    topics_gated: int = 0
    # drafts produced (passed to the critic); not all are stored.
    articles_written: int = 0
    stored: int = 0
    rejected: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


def _evidence_block(results) -> tuple[str, list[str], list[str], bool, bool]:
    """Number the evidence and collect provenance. Returns
    (text, chunk_ids, sources, in_code, in_docs)."""
    lines, ids, sources = [], [], []
    in_code = in_docs = False
    for n, r in enumerate(results, 1):
        meta = r.metadata or {}
        src = meta.get("source", "?")
        loc = f"{src}:{meta.get('start_line')}" if meta.get("start_line") else src
        side = "code" if str(meta.get("kind", "")).lower() == "code" else "doc"
        in_code = in_code or side == "code"
        in_docs = in_docs or side == "doc"
        lines.append(f"[{n}] ({side}) {loc}\n{r.text}")
        ids.append(r.id)
        sources.append(loc)
    return "\n\n".join(lines), ids, sources, in_code, in_docs


def synthesize_articles(store, settings, *, graph=None, writer=None, critic=None,
                        limit=None, dry_run=False) -> SynthesisReport:
    if writer is None or critic is None:
        w, c = get_article_llms(settings)
        writer, critic = writer or w, critic or c

    items = store.get_items(limit=10_000)
    extra = []
    if graph is not None:
        extra = [e.get("name", "") for e in graph.list_entities(limit=500)]
    topics = gather_topics(items, extra_topics=extra)
    if limit is not None:
        topics = topics[:limit]

    article_store = store.sibling(f"{store.stats()['collection']}__articles")
    report = SynthesisReport(topics_gated=len(topics))

    for tc in topics:
        try:
            results = store.search(tc.name, top_k=8, mode="hybrid")
            if not results:
                # Fail Loud: record the topic so it is accounted for in the report.
                report.rejected.append(
                    {"topic": tc.name, "verdict": {"note": "no evidence retrieved"}}
                )
                continue
            evidence, ids, sources, in_code, in_docs = _evidence_block(results)
            draft = writer.write(tc.name, evidence)
            report.articles_written += 1
            verdict = critic.judge(tc.name, draft["body"], evidence)
            if not keep_article(verdict):
                report.rejected.append({"topic": tc.name, "verdict": verdict})
                continue
            article = Article(
                title=draft["title"], topic=tc.name, body=draft["body"],
                business_relevance=draft["business_relevance"],
                source_chunk_ids=ids, sources=sources,
                cross_validated=in_code and in_docs, critic_verdict=verdict,
            )
            if not dry_run:
                article_store.upsert([article])
            report.stored += 1
        except Exception as exc:  # noqa: BLE001 - Fail Loud into the report, keep going
            report.errors.append({"topic": tc.name, "error": str(exc)})
    return report
