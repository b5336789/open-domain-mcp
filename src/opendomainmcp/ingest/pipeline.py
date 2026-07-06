"""Ingestion pipeline: load -> split -> extract -> embed -> store.

Dependencies (store / extractor / splitter) are injected so tests can run fully
offline with fakes. Failures on individual files or extractions are recorded and
reported (Fail Loud) rather than silently dropped; the run continues.

An optional ``progress`` callback receives ``{"stage", "path", "detail", ...}``
dicts so callers (e.g. the web UI) can stream live status.
"""

from __future__ import annotations

import contextlib
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from ..models import Chunk
from .code_splitter import split_code
from .filters import IngestFilter
from .loader import UnsupportedFileError, load_file
from .text_splitter import RecursiveTextSplitter

logger = logging.getLogger(__name__)

# Directories never worth indexing.
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    ".opendomain", "dist", "build", ".pytest_cache", ".mypy_cache",
}

Progress = Callable[[dict], None]


class PathNotAllowedError(Exception):
    """Raised when an ingest path escapes the configured ``ingest_root``."""


def _resolve_within(path: Path, root: Path) -> Path:
    """Resolve ``path`` and ensure it stays within ``root``.

    Resolution follows symlinks, so a link pointing outside ``root`` is caught.
    Raises :class:`PathNotAllowedError` if the target escapes the root.
    """
    rp = path.resolve()
    rr = root.resolve()
    if rp != rr and rr not in rp.parents:
        raise PathNotAllowedError(
            f"{path} resolves outside the allowed ingest root {root}"
        )
    return rp


@dataclass
class IngestReport:
    files_indexed: int = 0
    chunks_indexed: int = 0
    chunks_pruned: int = 0                         # stale chunks removed by sync
    skipped: list = field(default_factory=list)   # [{"path", "reason"}]
    errors: list = field(default_factory=list)    # [{"path", "error"}]
    filtered: list = field(default_factory=list)  # [{"path", "rule"}] excluded by filter rules
    pruned_sources: list = field(default_factory=list)  # [{"source", "reason"}] removed by sync
    evidence_verified: int = 0    # count of individual evidence entries confirmed in source text
    evidence_unverified: int = 0  # count of individual evidence entries not found in source text

    def to_dict(self) -> dict:
        return asdict(self)


class Pipeline:
    def __init__(self, store, extractor, settings,
                 splitter: Optional[RecursiveTextSplitter] = None, graph=None):
        from ..graph.store import NullGraphStore

        self._store = store
        self._extractor = extractor
        self._settings = settings
        self._splitter = splitter or RecursiveTextSplitter(
            settings.chunk_size, settings.chunk_overlap
        )
        self._graph = graph or NullGraphStore()

    # -- public API -----------------------------------------------------
    def ingest_path(
        self,
        path: str | Path,
        progress: Optional[Progress] = None,
        sync: bool = False,
        allowed_root: Optional[str | Path] = None,
        checkpoint=None,
        exclude: Optional[Sequence[str]] = None,
        use_default_excludes: bool = True,
    ) -> IngestReport:
        # A Git URL or .zip is materialised into a temp dir which then doubles as
        # the allowed_root; plain paths fall through unchanged. An optional
        # ``checkpoint`` (see ingest/checkpoint.py) makes the run resumable:
        # already-completed files are skipped and progress is persisted per file.
        from .sources import prepared_source

        ingest_filter = IngestFilter.from_settings(
            self._settings, tuple(exclude or ()), use_default_excludes
        )
        with prepared_source(path, self._settings.data_dir) as prepared:
            if prepared is not None:
                self._emit(progress, "fetch", str(path), detail="materialised source")
                return self._ingest(prepared, progress, sync=False,
                                    allowed_root=prepared, checkpoint=checkpoint,
                                    ingest_filter=ingest_filter)
            return self._ingest(Path(path), progress, sync=sync,
                                allowed_root=allowed_root, checkpoint=checkpoint,
                                ingest_filter=ingest_filter)

    def _ingest(
        self,
        path: Path,
        progress: Optional[Progress],
        sync: bool,
        allowed_root: Optional[str | Path],
        checkpoint=None,
        ingest_filter: Optional[IngestFilter] = None,
    ) -> IngestReport:
        report = IngestReport()
        # Confine ingestion to an allowed root when one is configured. The
        # explicit ``allowed_root`` argument (used by the web layer) takes
        # precedence over the global ``ingest_root`` setting.
        root = allowed_root if allowed_root is not None else getattr(
            self._settings, "ingest_root", None
        )
        if root is not None:
            root = Path(root)
            path = _resolve_within(path, root)
        if path.is_dir():
            files = list(self._walk(path))
        elif path.is_file():
            files = [path]
        else:
            raise FileNotFoundError(f"{path} does not exist")
        if root is not None:
            files = self._filter_within(files, root, report, progress)
        if ingest_filter is None:
            ingest_filter = IngestFilter.from_settings(self._settings)
        base = path if path.is_dir() else path.parent
        files = self._apply_ingest_filter(files, base, report, progress, ingest_filter)
        if checkpoint is not None:
            # If the extraction config changed since this checkpoint was written,
            # discard the completed-file set so everything re-extracts.
            from .checkpoint import extractor_signature
            checkpoint.reset_if_signature_changed(extractor_signature(self._settings))
        with self._batch_prepass(files, report, progress):
            for file_path in files:
                if checkpoint is not None:
                    if checkpoint.cancelled:
                        self._emit(progress, "cancelled", str(file_path),
                                   detail="run cancelled")
                        break
                    if checkpoint.is_file_done(file_path):
                        # Resumed run: this file was indexed previously. Skipping
                        # avoids re-extracting and re-embedding it.
                        self._emit(progress, "skip", str(file_path), detail="resumed")
                        continue
                self._ingest_file(file_path, report, progress)
                if checkpoint is not None:
                    checkpoint.mark_file_done(file_path)
                    checkpoint.update_from_report(report, current_file=str(file_path))
                    checkpoint.save()
        if sync and path.is_dir():
            self._sync_deletions(path, {str(f) for f in files}, report, progress)
        self._emit(progress, "done", str(path), detail=f"{report.files_indexed} files")
        return report

    def list_files(self, path: str | Path) -> list[str]:
        """The file paths ingest_path would process, in processing order.
        Used by the Task Center to enumerate child entries up front."""
        p = Path(path)
        flt = IngestFilter.from_settings(self._settings)
        if p.is_dir():
            return [str(f) for f in self._walk(p)
                    if flt.exclusion_reason(f, p) is None]
        if p.is_file():
            return [str(p)] if flt.exclusion_reason(p, p.parent) is None else []
        return []

    # -- internals ------------------------------------------------------
    def _walk(self, root: Path):
        # followlinks defaults to False, so symlinked sub-directories are not
        # traversed; symlinked files are caught later by _filter_within.
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in sorted(filenames):
                if not name.startswith("."):
                    yield Path(dirpath) / name

    def _apply_ingest_filter(self, files, base: Path, report: IngestReport,
                             progress: Optional[Progress],
                             ingest_filter: IngestFilter):
        """Drop files excluded by filter rules — always reported, never silent."""
        kept = []
        for f in files:
            rule = ingest_filter.exclusion_reason(f, base)
            if rule is None:
                kept.append(f)
            else:
                report.filtered.append({"path": str(f), "rule": rule})
                self._emit(progress, "filter", str(f), detail=rule)
        return kept

    def _filter_within(self, files, root: Path, report: IngestReport,
                       progress: Optional[Progress]):
        """Drop files that escape ``root`` (e.g. symlinks pointing outside)."""
        safe = []
        for f in files:
            try:
                _resolve_within(f, root)
                safe.append(f)
            except PathNotAllowedError:
                report.skipped.append({"path": str(f), "reason": "outside ingest root"})
                self._emit(progress, "skip", str(f), detail="outside ingest root")
        return safe

    def _load_and_split(self, path: Path) -> list[Chunk]:
        """Load and split one file into indexed chunks. Raises on load failure;
        returns [] for an empty document. Emits no progress (callers do)."""
        doc = load_file(path)
        if doc.kind == "code":
            chunks = split_code(doc.text, doc.language, str(path),
                                self._settings.code_max_chunk_chars)
        elif doc.kind == "api":
            if doc.language == "graphql":
                from .graphql import split_graphql

                chunks = split_graphql(doc.text, str(path))
            else:
                from .openapi import split_openapi

                chunks = split_openapi(doc.text, str(path))
        else:
            chunks = [Chunk(text=t, source=str(path), kind="text")
                      for t in self._splitter.split(doc.text)]
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i
        return chunks

    def _ingest_file(self, path: Path, report: IngestReport, progress: Optional[Progress]):
        self._emit(progress, "load", str(path))
        try:
            chunks = self._load_and_split(path)
        except UnsupportedFileError as exc:
            report.skipped.append({"path": str(path), "reason": str(exc)})
            self._emit(progress, "skip", str(path), detail=str(exc))
            return
        except Exception as exc:  # unexpected read error: report, keep going
            report.errors.append({"path": str(path), "error": repr(exc)})
            self._emit(progress, "error", str(path), detail=repr(exc))
            return

        self._emit(progress, "split", str(path))
        if not chunks:
            report.skipped.append({"path": str(path), "reason": "no content"})
            self._emit(progress, "skip", str(path), detail="no content")
            return

        self._emit(progress, "extract", str(path), detail=f"{len(chunks)} chunks")
        self._extract_all(chunks, path, report)

        new_ids = {c.id for c in chunks}
        stale = self._store.get_ids_for_source(str(path)) - new_ids
        if stale:
            self._store.delete_ids(stale)
            self._graph.delete_for_chunks(stale)
            report.chunks_pruned += len(stale)
            self._emit(progress, "prune", str(path), detail=f"{len(stale)} stale")

        self._emit(progress, "embed", str(path))
        stored = self._store.upsert(chunks)
        self._write_graph(chunks)
        self._write_deps(chunks)
        self._write_workflow(chunks)
        self._emit(progress, "store", str(path), detail=f"{stored} chunks")

        report.files_indexed += 1
        report.chunks_indexed += stored

    def _write_graph(self, chunks: list[Chunk]) -> None:
        from ..graph.builder import build_graph

        for chunk in chunks:
            if not (chunk.knowledge and not chunk.knowledge.is_empty()):
                continue
            entities, edges = build_graph(chunk.knowledge, chunk.id)
            self._graph.upsert_entities(entities)
            self._graph.upsert_edges(edges)

    def _write_deps(self, chunks: list[Chunk]) -> None:
        """Populate code dependency (``imports``) edges for code chunks (task 4.4).

        Runs alongside the knowledge graph so a Null/empty graph store still works;
        non-code chunks and chunks without imports yield nothing."""
        from ..graph.deps import extract_dependencies

        for chunk in chunks:
            if chunk.kind != "code":
                continue
            entities, edges = extract_dependencies(
                chunk.language or "", chunk.text, chunk.symbol, chunk.id
            )
            if not entities and not edges:
                continue
            self._graph.upsert_entities(entities)
            self._graph.upsert_edges(edges)

    def _write_workflow(self, chunks: list[Chunk]) -> None:
        from ..graph.workflow import build_workflow

        for chunk in chunks:
            if not chunk.knowledge:
                continue
            steps, prerequisites, name = build_workflow(chunk.knowledge)
            if not name:
                continue
            self._graph.upsert_workflow(name, chunk.id, chunk.chunk_index or 0,
                                        steps, prerequisites)

    def _extract_all(self, chunks: list[Chunk], path: Path, report: IngestReport):
        """Extract knowledge for each chunk, in parallel when configured.

        Order is irrelevant (results are written onto the chunk objects); per-chunk
        failures are recorded and never abort the run (Fail Loud)."""
        if getattr(self._settings, "codegraph_extract", False):
            # Code chunks are analyzed with call-chain context by the codegraph
            # analyze pass; only non-code content extracts per-chunk here.
            # Note: skipped code chunks carry no review_status until the analyze
            # pass backfills their knowledge — relevant when review_mode /
            # retrieve_approved_only are on (unreviewed chunks are filtered out).
            chunks = [c for c in chunks if c.kind != "code"]
            if not chunks:
                return
        workers = max(1, int(getattr(self._settings, "extract_concurrency", 1)))
        if workers == 1 or len(chunks) <= 1:
            for chunk in chunks:
                self._extract_one(chunk, path, report)
            return
        with ThreadPoolExecutor(max_workers=min(workers, len(chunks))) as pool:
            list(pool.map(lambda c: self._extract_one(c, path, report), chunks))

    def _extract_one(self, chunk: Chunk, path: Path, report: IngestReport):
        try:
            # Pre-classified chunks (e.g. OpenAPI operations) already carry a
            # knowledge_type; don't overwrite them with the LLM extractor.
            if not (chunk.knowledge and chunk.knowledge.knowledge_type):
                chunk.knowledge = self._extractor.extract(
                    chunk.text, chunk.kind, chunk.language
                )
            # In review mode, freshly extracted knowledge must be approved before
            # it counts as reviewed; otherwise it is born approved (default).
            if getattr(self._settings, "review_mode", False) and chunk.knowledge:
                chunk.knowledge.review_status = "pending"
            # Verify evidence entries against the source text; penalize confidence
            # when none are found. Accumulate counts with the same idiom as errors.
            if chunk.knowledge and chunk.knowledge.evidence:
                from ..extract.verify import apply_penalty, verify_evidence

                verified, status = verify_evidence(
                    chunk.knowledge.evidence, chunk.text, chunk.source,
                    base_line=chunk.start_line or 1)
                chunk.knowledge.evidence = verified
                chunk.knowledge.evidence_status = status
                chunk.knowledge.confidence = apply_penalty(
                    chunk.knowledge.confidence, status)
                for entry in verified:
                    if entry.get("verified"):
                        report.evidence_verified += 1
                    else:
                        report.evidence_unverified += 1
        except Exception as exc:  # extraction is best-effort; record and continue
            report.errors.append({"path": str(path), "error": f"extract: {exc!r}"})

    @contextlib.contextmanager
    def _batch_prepass(self, files, report: IngestReport,
                       progress: Optional[Progress]):
        """When extract_batch is on, batch-extract all chunk texts up front and
        run the per-file loop with a CachedExtractor. No-op otherwise."""
        if not getattr(self._settings, "extract_batch", False):
            yield
            return
        if self._settings.llm_backend.lower() != "anthropic":
            raise ValueError(
                "ODM_EXTRACT_BATCH requires the anthropic LLM backend"
            )
        cache = self._batch_extract_files(files, report, progress)
        from .batch_extract import CachedExtractor

        original = self._extractor
        self._extractor = CachedExtractor(cache, original)
        try:
            yield
        finally:
            self._extractor = original

    def _batch_extract_files(self, files, report: IngestReport,
                             progress: Optional[Progress]) -> dict:
        from .batch_extract import BatchItem, _text_hash

        items: dict[str, BatchItem] = {}
        for f in files:
            try:
                chunks = self._load_and_split(f)
            except Exception:
                continue  # the real per-file pass records skip/error
            for c in chunks:
                if c.knowledge and c.knowledge.knowledge_type:
                    continue  # pre-classified; not LLM-extracted
                if getattr(self._settings, "codegraph_extract", False) \
                        and c.kind == "code":
                    continue  # _extract_all will skip these; don't batch-extract them
                h = _text_hash(c.text)
                if h not in items:
                    items[h] = BatchItem(text_hash=h, text=c.text,
                                         kind=c.kind, language=c.language)
        if not items:
            return {}
        self._emit(progress, "batch", "extraction",
                   detail=f"{len(items)} chunks submitted")
        extractor = self._build_batch_extractor()
        return extractor.extract_many(
            list(items.values()),
            progress=lambda d: self._emit(progress, "batch", "extraction", detail=d),
        )

    def _build_batch_extractor(self):
        import anthropic

        from .batch_extract import BatchExtractor

        client = anthropic.Anthropic(
            timeout=self._settings.request_timeout,
            max_retries=self._settings.max_retries,
        )
        return BatchExtractor(client, self._settings.extraction_model)

    def _sync_deletions(self, root: Path, seen: set, report: IngestReport,
                        progress: Optional[Progress]):
        """Remove stored chunks under ``root`` whose source file was not seen
        in this run — either the file was deleted or it is now excluded by filter rules."""
        prefix = str(root) + os.sep
        for source in self._store.get_all_sources():
            if source in seen:
                continue
            if source == str(root) or source.startswith(prefix):
                ids = self._store.get_ids_for_source(source)
                removed = self._store.delete_ids(ids)
                self._graph.delete_for_chunks(ids)
                report.chunks_pruned += removed
                reason = "excluded" if Path(source).exists() else "file removed"
                report.pruned_sources.append({"source": source, "reason": reason})
                self._emit(progress, "prune", source, detail=reason)

    @staticmethod
    def _emit(progress: Optional[Progress], stage: str, path: str, detail: str = ""):
        if progress is not None:
            progress({"stage": stage, "path": path, "detail": detail})
