"""Document export: corpus → formatted Markdown documents.

Single path used by the CLI and the web task runner:
collect → organize (optional LLM) → translate (optional LLM) → render.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .collect import collect_bundle
from .models import ExportReport
from .organize import build_outline, get_organizer
from .render import render_export
from .translate import TranslationCache, get_translator, translate_bundle

__all__ = ["ExportError", "export_documents"]


class ExportError(Exception):
    pass


def _graph_enabled(ctx) -> bool:
    from ..graph.store import NullGraphStore
    return not isinstance(ctx.graph, NullGraphStore)


def export_documents(ctx, out_dir, *, translate: bool = True,
                     use_llm: bool = True, zip_output: bool = False,
                     translator=None, organizer=None, progress=None) -> ExportReport:
    report = ExportReport()
    if progress:
        progress({"stage": "collect"})
    bundle = collect_bundle(ctx.store, ctx.graph, _graph_enabled(ctx), report=report)
    if not bundle.articles and not bundle.rules:
        raise ExportError(
            "Nothing to export: no articles and no rules in the collection. "
            "Run `synthesize` and/or `consolidate` first.")

    data_dir = Path(ctx.settings.data_dir)
    outline = None
    if use_llm:
        if organizer is None:
            organizer = get_organizer(ctx.settings)
        if progress:
            progress({"stage": "organize"})
        outline = build_outline(bundle, organizer,
                                data_dir / "outline_cache.json", report)
        if translate:
            if translator is None:
                translator = get_translator(ctx.settings)
            cache = TranslationCache(data_dir / "translation_cache.json")
            translate_bundle(bundle, translator, cache, report, progress=progress)
            cache.save()

    if progress:
        progress({"stage": "render"})
    render_export(bundle, outline, Path(out_dir), report)

    if zip_output:
        report.zip_path = shutil.make_archive(str(Path(out_dir)), "zip",
                                              root_dir=str(out_dir))
    return report
