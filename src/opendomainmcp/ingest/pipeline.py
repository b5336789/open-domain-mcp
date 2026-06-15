"""Ingestion pipeline: load -> split -> extract -> embed -> store.

Dependencies (store / extractor / splitter) are injected so tests can run fully
offline with fakes. Failures on individual files or extractions are recorded and
reported (Fail Loud) rather than silently dropped; the run continues.

An optional ``progress`` callback receives ``{"stage", "path", "detail", ...}``
dicts so callers (e.g. the web UI) can stream live status.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..models import Chunk
from .code_splitter import split_code
from .loader import UnsupportedFileError, load_file
from .text_splitter import RecursiveTextSplitter

logger = logging.getLogger(__name__)

# Directories never worth indexing.
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    ".opendomain", "dist", "build", ".pytest_cache", ".mypy_cache",
}

Progress = Callable[[dict], None]


@dataclass
class IngestReport:
    files_indexed: int = 0
    chunks_indexed: int = 0
    chunks_pruned: int = 0                         # stale chunks removed by sync
    skipped: list = field(default_factory=list)   # [{"path", "reason"}]
    errors: list = field(default_factory=list)    # [{"path", "error"}]

    def to_dict(self) -> dict:
        return asdict(self)


class Pipeline:
    def __init__(self, store, extractor, settings, splitter: Optional[RecursiveTextSplitter] = None):
        self._store = store
        self._extractor = extractor
        self._settings = settings
        self._splitter = splitter or RecursiveTextSplitter(
            settings.chunk_size, settings.chunk_overlap
        )

    # -- public API -----------------------------------------------------
    def ingest_path(
        self,
        path: str | Path,
        progress: Optional[Progress] = None,
        sync: bool = False,
    ) -> IngestReport:
        path = Path(path)
        report = IngestReport()
        if path.is_dir():
            files = list(self._walk(path))
        elif path.is_file():
            files = [path]
        else:
            raise FileNotFoundError(f"{path} does not exist")
        for file_path in files:
            self._ingest_file(file_path, report, progress)
        if sync and path.is_dir():
            self._sync_deletions(path, {str(f) for f in files}, report, progress)
        self._emit(progress, "done", str(path), detail=f"{report.files_indexed} files")
        return report

    # -- internals ------------------------------------------------------
    def _walk(self, root: Path):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in sorted(filenames):
                if not name.startswith("."):
                    yield Path(dirpath) / name

    def _ingest_file(self, path: Path, report: IngestReport, progress: Optional[Progress]):
        self._emit(progress, "load", str(path))
        try:
            doc = load_file(path)
        except UnsupportedFileError as exc:
            report.skipped.append({"path": str(path), "reason": str(exc)})
            self._emit(progress, "skip", str(path), detail=str(exc))
            return
        except Exception as exc:  # unexpected read error: report, keep going
            report.errors.append({"path": str(path), "error": repr(exc)})
            self._emit(progress, "error", str(path), detail=repr(exc))
            return

        self._emit(progress, "split", str(path))
        if doc.kind == "code":
            chunks = split_code(doc.text, doc.language, str(path),
                                self._settings.code_max_chunk_chars)
        else:
            chunks = [Chunk(text=t, source=str(path), kind="text")
                      for t in self._splitter.split(doc.text)]
        if not chunks:
            report.skipped.append({"path": str(path), "reason": "no content"})
            self._emit(progress, "skip", str(path), detail="no content")
            return

        self._emit(progress, "extract", str(path), detail=f"{len(chunks)} chunks")
        self._extract_all(chunks, path, report)

        # Reconcile against what's already stored for this source: drop chunks
        # that no longer exist (e.g. an edited function shifted line ranges).
        new_ids = {c.id for c in chunks}
        stale = self._store.get_ids_for_source(str(path)) - new_ids
        if stale:
            self._store.delete_ids(stale)
            report.chunks_pruned += len(stale)
            self._emit(progress, "prune", str(path), detail=f"{len(stale)} stale")

        self._emit(progress, "embed", str(path))
        stored = self._store.upsert(chunks)
        self._emit(progress, "store", str(path), detail=f"{stored} chunks")

        report.files_indexed += 1
        report.chunks_indexed += stored

    def _extract_all(self, chunks: list[Chunk], path: Path, report: IngestReport):
        """Extract knowledge for each chunk, in parallel when configured.

        Order is irrelevant (results are written onto the chunk objects); per-chunk
        failures are recorded and never abort the run (Fail Loud)."""
        workers = max(1, int(getattr(self._settings, "extract_concurrency", 1)))
        if workers == 1 or len(chunks) <= 1:
            for chunk in chunks:
                self._extract_one(chunk, path, report)
            return
        with ThreadPoolExecutor(max_workers=min(workers, len(chunks))) as pool:
            list(pool.map(lambda c: self._extract_one(c, path, report), chunks))

    def _extract_one(self, chunk: Chunk, path: Path, report: IngestReport):
        try:
            chunk.knowledge = self._extractor.extract(
                chunk.text, chunk.kind, chunk.language
            )
        except Exception as exc:  # extraction is best-effort; record and continue
            report.errors.append({"path": str(path), "error": f"extract: {exc!r}"})

    def _sync_deletions(self, root: Path, seen: set, report: IngestReport,
                        progress: Optional[Progress]):
        """Remove stored chunks under ``root`` whose source file was not seen
        in this run (i.e. the file was deleted)."""
        prefix = str(root) + os.sep
        for source in self._store.get_all_sources():
            if source in seen:
                continue
            if source == str(root) or source.startswith(prefix):
                removed = self._store.delete_ids(self._store.get_ids_for_source(source))
                report.chunks_pruned += removed
                self._emit(progress, "prune", source, detail="file removed")

    @staticmethod
    def _emit(progress: Optional[Progress], stage: str, path: str, detail: str = ""):
        if progress is not None:
            progress({"stage": stage, "path": path, "detail": detail})
