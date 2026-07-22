"""Optional LLM pass: translate bundle content to Chinese, content-hash cached.

Per-object failure keeps the original text, sets ``untranslated`` and records
the error (Fail Loud); it never aborts the export.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable

from ..config import Settings
from .models import ExportBundle, ExportReport

_SYSTEM = (
    "You translate technical/business documentation from English to Traditional "
    "Chinese (繁體中文). Keep code identifiers, file paths, API names and [n] "
    "citations exactly as-is. Respond with ONLY the translation, no preamble."
)


class TranslationCache:
    """sha256(source text) → translated text, persisted as one JSON file."""

    def __init__(self, path: Path):
        self._path = Path(path)
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}
        self._dirty = False

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get(self, text: str):
        return self._data.get(self._key(text))

    def put(self, text: str, translated: str) -> None:
        self._data[self._key(text)] = translated
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False)
        os.replace(tmp, self._path)
        self._dirty = False


def _cached(text: str, translate: Callable[[str], str],
            cache: TranslationCache) -> str:
    if not text.strip():
        return text
    hit = cache.get(text)
    if hit is not None:
        return hit
    out = translate(text).strip()
    cache.put(text, out)
    return out


def translate_bundle(bundle: ExportBundle, translate: Callable[[str], str],
                     cache: TranslationCache, report: ExportReport,
                     progress=None) -> None:
    total = len(bundle.articles) + len(bundle.rules) + len(bundle.workflows)
    done = 0

    def _tick():
        nonlocal done
        done += 1
        if progress:
            progress({"stage": "translate", "done": done, "total": total})

    for a in bundle.articles:
        try:
            title, body = _cached(a.title, translate, cache), _cached(a.body, translate, cache)
            a.title, a.body = title, body
        except Exception as exc:  # noqa: BLE001 - one bad item must not kill the export
            a.untranslated = True
            report.translate_errors.append({"id": a.id, "kind": "article",
                                            "error": str(exc)})
        _tick()

    for r in bundle.rules:
        try:
            r.statement = _cached(r.statement, translate, cache)
        except Exception as exc:  # noqa: BLE001
            r.untranslated = True
            report.translate_errors.append({"id": r.id, "kind": "rule",
                                            "error": str(exc)})
        _tick()

    for w in bundle.workflows:
        try:
            display = _cached(w.display_name, translate, cache)
            prereqs = [_cached(p, translate, cache) for p in w.prerequisites]
            steps = []
            for s in w.steps:
                steps.append({**s,
                              "text": _cached(s.get("text", ""), translate, cache),
                              "precondition": _cached(s.get("precondition", ""),
                                                      translate, cache)})
            w.display_name, w.prerequisites, w.steps = display, prereqs, steps
        except Exception as exc:  # noqa: BLE001
            w.untranslated = True
            report.translate_errors.append({"id": w.name, "kind": "workflow",
                                            "error": str(exc)})
        _tick()


def get_translator(settings: Settings) -> Callable[[str], str]:
    """LLM-backed translator on the synthesis provider settings."""
    from ..synthesis.llm import _caller
    c = _caller(settings.resolved_synthesize_provider(),
                model=settings.resolved_synthesize_model(), system=_SYSTEM,
                max_tokens=2000, timeout=settings.request_timeout,
                max_retries=settings.max_retries,
                base_url=settings.synthesize_base_url or None)
    return c._call
