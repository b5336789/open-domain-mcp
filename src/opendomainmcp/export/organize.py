"""Optional LLM pass: organize items into a business-domain outline.

One call per export over titles/one-liners only (never full bodies). The JSON
response is validated against the bundle; unknown references are dropped with
a warning and leftovers become the unassigned set (computed, never trusted
from the LLM). Cached by sha256 of the input listing.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable, Optional

from ..config import Settings
from ..extract.knowledge import parse_llm_json, ExtractionError
from .models import (ExportBundle, ExportReport, Outline, OutlineDomain,
                     OutlineFlow)

_SYSTEM = (
    "You organize a legacy system's knowledge items into a business-oriented "
    "outline: functional domains (use Traditional Chinese domain names), each "
    "containing its main workflows, with related articles and rules attached "
    "to the workflow they belong to (or to the domain directly). Use ONLY the "
    "identifiers given. Respond with ONLY a JSON object:\n"
    '{"domains": [{"name": str, '
    '"flows": [{"workflow": str, "articles": [topic, ...], "rules": [rN, ...]}], '
    '"articles": [topic, ...], "rules": [rN, ...]}]}\n'
    "Every item should appear at most once. No prose outside the JSON."
)


def _listing(bundle: ExportBundle) -> str:
    lines = ["WORKFLOWS (refer by name):"]
    lines += [f"- {w.name}" for w in bundle.workflows] or ["(none)"]
    lines.append("ARTICLES (refer by topic):")
    lines += [f"- {a.topic}: {a.title}" for a in bundle.articles] or ["(none)"]
    lines.append("RULES (refer by rN token):")
    lines += [f"- r{i + 1}: {r.statement[:160]}"
              for i, r in enumerate(bundle.rules)] or ["(none)"]
    return "\n".join(lines)


def _load_cache(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _validated(raw: dict, bundle: ExportBundle,
               report: ExportReport) -> Optional[Outline]:
    topics = {a.topic for a in bundle.articles}
    wf_names = {w.name for w in bundle.workflows}
    rule_ids = {f"r{i + 1}": r.id for i, r in enumerate(bundle.rules)}

    def _warn(token, where):
        report.outline_warnings.append(
            f"outline referenced unknown {where} {token!r}; dropped")

    seen_rules: set = set()
    seen_topics: set = set()

    def _rules(tokens):
        out = []
        for t in tokens or []:
            if t not in rule_ids:
                _warn(t, "rule")
                continue
            rid = rule_ids[t]
            if rid in seen_rules:
                report.outline_warnings.append(
                    f"rule {rid!r} placed under multiple domains; kept first "
                    "placement only")
                continue
            seen_rules.add(rid)
            out.append(rid)
        return out

    def _topics(tokens):
        out = []
        for t in tokens or []:
            if t not in topics:
                _warn(t, "article")
                continue
            if t in seen_topics:
                report.outline_warnings.append(
                    f"article {t!r} placed under multiple domains; kept first "
                    "placement only")
                continue
            seen_topics.add(t)
            out.append(t)
        return out

    domains = []
    for d in raw.get("domains", []) or []:
        if not isinstance(d, dict):
            report.outline_warnings.append(
                f"outline domain entry was not an object ({d!r}); skipped")
            continue
        name = str(d.get("name", "")).strip()
        if not name:
            continue
        flows = []
        for f in d.get("flows", []) or []:
            if not isinstance(f, dict):
                report.outline_warnings.append(
                    f"outline flow entry was not an object ({f!r}); skipped")
                continue
            wf = str(f.get("workflow", "")).strip()
            if wf not in wf_names:
                if wf:
                    _warn(wf, "workflow")
                continue
            flows.append(OutlineFlow(workflow=wf,
                                     articles=_topics(f.get("articles")),
                                     rules=_rules(f.get("rules"))))
        domains.append(OutlineDomain(name=name, flows=flows,
                                     articles=_topics(d.get("articles")),
                                     rules=_rules(d.get("rules"))))
    if not domains:
        report.outline_warnings.append("outline had no usable domains; "
                                       "falling back to flat layout")
        return None

    placed_topics = {t for d in domains
                     for t in d.articles + [t2 for f in d.flows for t2 in f.articles]}
    placed_wfs = {f.workflow for d in domains for f in d.flows}
    placed_rules = {r for d in domains
                    for r in d.rules + [r2 for f in d.flows for r2 in f.rules]}
    return Outline(
        domains=domains,
        unassigned_articles=[a.topic for a in bundle.articles
                             if a.topic not in placed_topics],
        unassigned_workflows=[w.name for w in bundle.workflows
                              if w.name not in placed_wfs],
        unassigned_rules=[r.id for r in bundle.rules if r.id not in placed_rules])


def build_outline(bundle: ExportBundle, complete: Optional[Callable[[str], str]],
                  cache_path, report: ExportReport) -> Optional[Outline]:
    if complete is None:
        return None
    listing = _listing(bundle)
    key = hashlib.sha256(listing.encode("utf-8")).hexdigest()
    cache = _load_cache(cache_path)
    raw = cache.get(key)
    called_llm = raw is None
    if raw is None:
        response_text = complete(listing)
        try:
            raw = parse_llm_json(response_text)
        except (ExtractionError, json.JSONDecodeError):
            raw = {}
    if not isinstance(raw, dict) or not raw:
        report.outline_warnings.append("outline LLM returned no parseable JSON; "
                                       "falling back to flat layout")
        return None
    outline = _validated(raw, bundle, report)
    # Only persist a fresh (non-cache-hit) response once it has proven to
    # produce a usable outline — caching an unusable-but-parseable response
    # would permanently poison future runs onto the flat layout. Cache hits
    # are, by construction, already-validated-once raw payloads and are left
    # untouched (re-validated above so warnings stay accurate).
    if called_llm and outline is not None:
        _save_cache(cache_path, {**cache, key: raw})
    return outline


def get_organizer(settings: Settings) -> Callable[[str], str]:
    from ..synthesis.llm import _caller
    c = _caller(settings.resolved_synthesize_provider(),
                model=settings.resolved_synthesize_model(), system=_SYSTEM,
                max_tokens=4000, timeout=settings.request_timeout,
                max_retries=settings.max_retries,
                base_url=settings.synthesize_base_url or None)
    return c._call
