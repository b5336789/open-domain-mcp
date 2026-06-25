from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..models import Article
from .llm import get_article_llms, keep_article
from .topics import gather_topics


@dataclass
class SynthesisReport:
    topics_gated: int = 0
    # drafts produced (passed to the critic); not all are stored.
    articles_written: int = 0
    stored: int = 0
    # stale articles deleted: rejected/no-evidence topics + dead-chunk prune.
    removed: int = 0
    rejected: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _evidence_block(results) -> tuple[str, list[str], list[str]]:
    """Number the evidence and collect provenance. Returns
    (text, chunk_ids, sources)."""
    lines, ids, sources = [], [], []
    for n, r in enumerate(results, 1):
        meta = r.metadata or {}
        src = meta.get("source", "?")
        loc = f"{src}:{meta.get('start_line')}" if meta.get("start_line") else src
        side = "code" if str(meta.get("kind", "")).lower() == "code" else "doc"
        lines.append(f"[{n}] ({side}) {loc}\n{r.text}")
        ids.append(r.id)
        sources.append(loc)
    return "\n\n".join(lines), ids, sources


def synthesize_articles(store, settings, *, graph=None, writer=None, critic=None,
                        limit=None, dry_run=False, on_event=None) -> SynthesisReport:
    # on_event (optional) receives a progress dict per stage so a UI/CLI can show
    # live progress; default is a no-op so existing callers are unaffected.
    emit = on_event or (lambda event: None)
    if writer is None or critic is None:
        w, c = get_article_llms(settings)
        writer, critic = writer or w, critic or c

    PAGE = 1000
    items, off = [], 0
    while True:
        page = store.get_items(limit=PAGE, offset=off)
        items.extend(page)
        if len(page) < PAGE:
            break
        off += PAGE
    extra = []
    if graph is not None:
        extra = [e.get("name", "") for e in graph.list_entities(limit=500)]
    topics = gather_topics(items, extra_topics=extra)
    if limit is not None:
        topics = topics[:limit]

    article_store = store.sibling(f"{store.stats()['collection']}__articles")
    report = SynthesisReport(topics_gated=len(topics))
    total = len(topics)
    emit({"stage": "start", "total": total, "dry_run": dry_run})

    dropped: set[str] = set()  # article ids already removed/counted in the loop

    def _drop(topic: str) -> None:
        """Remove a topic's stale article, counting it (even under dry_run, where
        the delete is skipped). Idempotent: absent topics are a no-op, not counted."""
        aid = Article.id_for_topic(topic)
        if dry_run:
            if article_store.get_item(aid) is not None:
                report.removed += 1
                dropped.add(aid)
        elif article_store.delete_item(aid):
            report.removed += 1
            dropped.add(aid)

    for index, tc in enumerate(topics, 1):
        emit({"stage": "topic", "topic": tc.name, "index": index, "total": total})
        try:
            results = store.search(tc.name, top_k=8, mode="hybrid")
            if not results:
                # Fail Loud: record the topic so it is accounted for in the report.
                report.rejected.append(
                    {"topic": tc.name, "verdict": {"note": "no evidence retrieved"}}
                )
                _drop(tc.name)
                emit({"stage": "rejected", "topic": tc.name,
                      "note": "no evidence retrieved"})
                continue
            evidence, ids, sources = _evidence_block(results)
            draft = writer.write(tc.name, evidence)
            report.articles_written += 1
            verdict = critic.judge(tc.name, draft["body"], evidence)
            if not keep_article(verdict):
                report.rejected.append({"topic": tc.name, "verdict": verdict})
                _drop(tc.name)
                emit({"stage": "rejected", "topic": tc.name,
                      "note": verdict.get("note", "")})
                continue
            article = Article(
                title=draft["title"], topic=tc.name, body=draft["body"],
                business_relevance=draft["business_relevance"],
                source_chunk_ids=ids, sources=sources,
                cross_validated=tc.cross_validated, critic_verdict=verdict,
            )
            if not dry_run:
                article_store.upsert([article])
            report.stored += 1
            emit({"stage": "stored", "topic": tc.name, "title": draft["title"]})
        except Exception as exc:  # noqa: BLE001 - Fail Loud into the report, keep going
            report.errors.append({"topic": tc.name, "error": str(exc)})
            emit({"stage": "topic_error", "topic": tc.name, "detail": str(exc)})

    # Dead-chunk prune: drop any stored article that cites a chunk no longer in
    # the main collection. `items` is that collection's current chunks, already
    # paged above, so building the live-id set costs no extra store query. The
    # criterion is independent of the gated-topic list, so it is --limit-safe.
    live_ids = {it["id"] for it in items}
    # Page the full article set first, then delete — deleting mid-pagination would
    # shift offsets and skip rows.
    stored_articles, off = [], 0
    while True:
        page = article_store.get_items(limit=PAGE, offset=off)
        stored_articles.extend(page)
        if len(page) < PAGE:
            break
        off += PAGE
    for row in stored_articles:
        if row["id"] in dropped:  # already counted/removed as a rejected topic
            continue
        meta = row.get("metadata") or {}
        cited = [c.strip() for c in str(meta.get("source_chunk_ids", "")).split(",")
                 if c.strip()]
        if cited and any(c not in live_ids for c in cited):
            if not dry_run:
                article_store.delete_item(row["id"])
            report.removed += 1
    return report
