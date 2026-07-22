"""Read-only stage: store/graph → ExportBundle. Zero LLM, zero writes."""
from __future__ import annotations

from .models import (ExportArticle, ExportBundle, ExportRule, ExportWorkflow,
                     split_comma, split_pipe)

_PAGE = 100


def _page_kind(store, kind: str) -> list[dict]:
    items, offset = [], 0
    while True:
        page = store.get_items(limit=_PAGE, offset=offset, where={"kind": kind})
        items.extend(page)
        if len(page) < _PAGE:
            return items
        offset += _PAGE


def collect_bundle(store, graph, graph_enabled: bool) -> ExportBundle:
    articles = []
    for it in _page_kind(store, "article"):
        m = it["metadata"]
        articles.append(ExportArticle(
            id=it["id"], title=str(m.get("title", "")),
            topic=str(m.get("topic", "")), body=it["text"],
            sources=split_pipe(m.get("sources")),
            source_chunk_ids=split_comma(m.get("source_chunk_ids"))))

    rules = []
    for it in _page_kind(store, "rule"):
        m = it["metadata"]
        rules.append(ExportRule(
            id=it["id"], statement=str(m.get("statement", "")),
            trust=str(m.get("trust", "normal")),
            corroborations=int(m.get("corroborations", 1) or 1),
            layers=split_comma(m.get("layers")),
            sources=split_pipe(m.get("sources")),
            evidence=it.get("evidence", []),
            review_status=str(m.get("review_status", ""))))

    workflows = []
    if graph_enabled:
        for row in graph.list_workflows(limit=500):
            wf = graph.get_workflow(row["name"])
            if wf is None:
                continue
            workflows.append(ExportWorkflow(
                name=wf["workflow_name"], display_name=wf["workflow_name"],
                prerequisites=list(wf.get("prerequisites", [])),
                steps=list(wf.get("steps", []))))

    return ExportBundle(articles=articles, rules=rules, workflows=workflows,
                        stats=store.stats(), graph_enabled=graph_enabled)
