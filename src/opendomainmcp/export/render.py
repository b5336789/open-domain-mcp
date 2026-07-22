"""Pure-template stage: (bundle, outline) → Markdown tree + merged handbook.

No LLM, no store access. Business-language body first; every document ends
with a 技術對照 appendix for engineers. Conflicted rules render ONLY into
rules-conflicted.md.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Optional

from .models import (ExportArticle, ExportBundle, ExportReport, ExportRule,
                     ExportWorkflow, Outline)

_BADGE = {"high": "🟢 high", "normal": "🟡 normal"}


def slugify(name: str, used: set) -> str:
    norm = unicodedata.normalize("NFKC", str(name)).strip().lower()
    slug = re.sub(r"[^\w一-鿿-]+", "-", norm).strip("-") or "item"
    base, n = slug, 1
    while slug in used:
        n += 1
        slug = f"{base}-{n}"
    used.add(slug)
    return slug


def _mark(obj) -> str:
    return " 〔未翻譯〕" if getattr(obj, "untranslated", False) else ""


def _rule_row(r: ExportRule) -> str:
    badge = _BADGE.get(r.trust, r.trust)
    src = ", ".join(r.sources) or "—"
    return f"| {r.statement}{_mark(r)} | {badge} | {r.corroborations} | {src} |"


def _rules_table(rules: list[ExportRule]) -> list[str]:
    if not rules:
        return []
    lines = ["| 規則 | 信心 | 佐證數 | 出處 |", "| --- | --- | --- | --- |"]
    order = {"high": 0, "normal": 1}
    for r in sorted(rules, key=lambda r: order.get(r.trust, 2)):
        lines.append(_rule_row(r))
    return lines


def _tech_appendix(sources: list[str], chunk_ids: list[str],
                   evidence: list[dict]) -> list[str]:
    lines = ["", "## 技術對照", ""]
    if sources:
        lines.append("**來源位置：** " + ", ".join(f"`{s}`" for s in sources))
    if evidence:
        lines.append("")
        lines.append("**佐證引文：**")
        for ev in evidence:
            claim = str(ev.get("claim", "")).strip()
            quote = str(ev.get("quote", "")).strip()
            lines.append(f"- {claim}：`{quote}`" if claim else f"- `{quote}`")
    if chunk_ids:
        lines.append("")
        lines.append("**關聯 chunk：** " + ", ".join(f"`{c}`" for c in chunk_ids))
    if len(lines) == 3:  # nothing to show
        return []
    return lines


def _render_article(a: ExportArticle, level: int = 1) -> str:
    h = "#" * level
    lines = [f"{h} {a.title}{_mark(a)}", "", a.body]
    lines += _tech_appendix(a.sources, a.source_chunk_ids, [])
    return "\n".join(lines) + "\n"


def _render_workflow(w: ExportWorkflow, articles: list[ExportArticle],
                     rules: list[ExportRule]) -> str:
    lines = [f"# {w.display_name}{_mark(w)}", ""]
    if w.prerequisites:
        lines.append("**前置條件：** " + "、".join(w.prerequisites))
        lines.append("")
    if w.steps:
        lines += ["| 步驟 | 內容 | 前置 |", "| --- | --- | --- |"]
        for s in w.steps:
            lines.append(f"| {s.get('order', '')} | {s.get('text', '')} "
                         f"| {s.get('precondition', '') or '—'} |")
        lines.append("")
    for a in articles:
        lines.append(_render_article(a, level=2))
    if rules:
        lines += ["## 相關規則", ""] + _rules_table(rules) + [""]
    chunk_ids = [s.get("chunk_id", "") for s in w.steps if s.get("chunk_id")]
    src = [e for r in rules for e in r.sources]
    ev = [e for r in rules for e in r.evidence]
    lines += _tech_appendix(src, chunk_ids, ev)
    return "\n".join(lines) + "\n"


def _render_rules_page(title: str, rules: list[ExportRule]) -> str:
    lines = [f"# {title}", ""] + _rules_table(rules)
    return "\n".join(lines) + "\n"


def _render_conflicted(rules: list[ExportRule]) -> str:
    lines = ["# 待釐清規則（conflicted）", "",
             "以下規則在不同來源間存在衝突，需人工裁決後再採信。", ""]
    for r in rules:
        lines.append(f"## {r.statement}{_mark(r)}")
        lines.append("")
        lines.append("衝突來源：")
        for s in r.sources or ["(來源不明)"]:
            lines.append(f"- `{s}`")
        lines += _tech_appendix([], [], r.evidence)
        lines.append("")
    return "\n".join(lines) + "\n"


def _write(path: Path, text: str, handbook: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    handbook.append(text)


def render_export(bundle: ExportBundle, outline: Optional[Outline],
                  out_dir: Path, report: ExportReport) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    handbook: list[str] = []
    articles = {a.topic: a for a in bundle.articles}
    rules = {r.id: r for r in bundle.rules}
    workflows = {w.name: w for w in bundle.workflows}
    active = [r for r in bundle.rules if r.trust != "conflicted"]
    conflicted = [r for r in bundle.rules if r.trust == "conflicted"]

    if outline is not None:
        dom_slugs: set = set()
        for d in outline.domains:
            ddir = out / "domains" / slugify(d.name, dom_slugs)
            flow_slugs: set = set()
            flow_lines = []
            for f in d.flows:
                w = workflows[f.workflow]
                fslug = slugify(f.workflow, flow_slugs)
                fa = [articles[t] for t in f.articles if t in articles]
                fr = [rules[i] for i in f.rules
                      if i in rules and rules[i].trust != "conflicted"]
                _write(ddir / f"{fslug}.md", _render_workflow(w, fa, fr), handbook)
                flow_lines.append(f"- [{w.display_name}]({fslug}.md)")
            dr = [rules[i] for i in d.rules
                  if i in rules and rules[i].trust != "conflicted"]
            da = [articles[t] for t in d.articles if t in articles]
            readme = [f"# {d.name}", "", "## 主流程", ""] + \
                (flow_lines or ["（無）"]) + [""]
            for a in da:
                readme.append(_render_article(a, level=2))
            if dr:
                readme += ["## 領域規則", ""] + _rules_table(dr)
            _write(ddir / "README.md", "\n".join(readme) + "\n", handbook)

        un_a = [articles[t] for t in outline.unassigned_articles if t in articles]
        un_w = [workflows[n] for n in outline.unassigned_workflows
                if n in workflows]
        un_r = [rules[i] for i in outline.unassigned_rules
                if i in rules and rules[i].trust != "conflicted"]
        report.unassigned = {"articles": len(un_a), "workflows": len(un_w),
                             "rules": len(un_r)}
        if un_a or un_w or un_r:
            misc = ["# 未分類", "", "大綱未能歸類的項目，內容仍完整保留。", ""]
            for a in un_a:
                misc.append(_render_article(a, level=2))
            for w in un_w:
                misc.append(_render_workflow(w, [], []))
            if un_r:
                misc += ["## 未分類規則", ""] + _rules_table(un_r)
            _write(out / "misc" / "README.md", "\n".join(misc) + "\n", handbook)
    else:
        a_slugs: set = set()
        for a in bundle.articles:
            _write(out / "articles" / f"{slugify(a.topic, a_slugs)}.md",
                   _render_article(a), handbook)
        w_slugs: set = set()
        for w in bundle.workflows:
            _write(out / "workflows" / f"{slugify(w.name, w_slugs)}.md",
                   _render_workflow(w, [], []), handbook)
        if active:
            _write(out / "rules.md", _render_rules_page("業務規則", active),
                   handbook)
        report.unassigned = {}

    if conflicted:
        _write(out / "rules-conflicted.md", _render_conflicted(conflicted),
               handbook)

    if not bundle.graph_enabled:
        report.skipped.append("workflows (graph store not enabled)")

    index = ["# 知識庫匯出總覽", "",
             f"- 索引物件總數：{bundle.stats.get('count', 0)}",
             f"- 文章：{len(bundle.articles)}　規則：{len(bundle.rules)}"
             f"（含待釐清 {len(conflicted)}）　流程：{len(bundle.workflows)}",
             "- 信心圖例：🟢 high（多來源佐證）　🟡 normal　🔴 conflicted（見待釐清專章）",
             ""]
    if not bundle.graph_enabled:
        index.append("> 注意：圖庫未啟用，本次匯出不含流程章節。")
        index.append("")
    if outline is not None:
        index.append("## 領域目錄")
        index.append("")
        seen: set = set()
        for d in outline.domains:
            index.append(f"- [{d.name}](domains/{slugify(d.name, seen)}/README.md)")
    (out / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")

    hb = "\n\n---\n\n".join(["# Handbook", ((out / 'index.md')
                             .read_text(encoding='utf-8'))] + handbook)
    (out / "handbook.md").write_text(hb, encoding="utf-8")

    report.counts = {"articles": len(bundle.articles), "rules": len(active),
                     "conflicted_rules": len(conflicted),
                     "workflows": len(bundle.workflows),
                     "domains": len(outline.domains) if outline else 0}
    report.out_dir = str(out)
