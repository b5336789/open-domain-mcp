"""Resumable ingest job state for asynchronous (background) ingestion.

A :class:`Checkpoint` persists one ingest run's progress to
``<data_dir>/.checkpoints/<job_id>.json`` so the web UI can start an ingest,
navigate away, and poll its status — and so an interrupted run can resume
without redoing files that already completed.

Resume granularity is per-file: completed source files are skipped on a re-run.
The expensive stage is LLM extraction, which is per-file, so skipping a completed
file avoids both its extraction and its (possibly paid) re-embedding. An
``extractor_signature`` invalidates the completed-file set when the extraction
provider/model changes, so a different model re-extracts everything.

Writes are atomic (temp file + ``os.replace``) so a crash mid-write cannot
corrupt the checkpoint.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

CHECKPOINT_VERSION = 1


def extractor_signature(settings) -> str:
    """Identity of the extraction config. Cached completed files are invalid when
    this changes (a different provider/model would extract different knowledge)."""
    return ":".join([
        str(bool(settings.extract_knowledge)),
        settings.resolved_extract_provider(),
        settings.extraction_model,
    ])


class Checkpoint:
    def __init__(self, path: Path, job_id: str, ingest_path: str, sync: bool,
                 signature: str):
        self.path = Path(path)
        self.job_id = job_id
        self.ingest_path = ingest_path
        self.sync = sync
        self.signature = signature
        self.status = "queued"  # queued | running | done | error | cancelled
        self.completed_files: set[str] = set()
        self.errors: list[dict] = []
        self.files_indexed = 0
        self.chunks_indexed = 0
        self.chunks_pruned = 0
        self.current_file = ""
        self.cancel_requested = False
        self.report: Optional[dict] = None

    # -- locations ------------------------------------------------------
    @staticmethod
    def directory(data_dir) -> Path:
        return Path(data_dir) / ".checkpoints"

    @classmethod
    def new(cls, data_dir, job_id: str, ingest_path, sync: bool,
            signature: str) -> "Checkpoint":
        path = cls.directory(data_dir) / f"{job_id}.json"
        return cls(path, job_id, str(ingest_path), sync, signature)

    @classmethod
    def load(cls, path) -> "Checkpoint":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        cp = cls(Path(path), data["job_id"], data["ingest_path"],
                 data.get("sync", False), data.get("signature", ""))
        cp.status = data.get("status", "queued")
        cp.completed_files = set(data.get("completed_files", []))
        cp.errors = data.get("errors", [])
        cp.files_indexed = data.get("files_indexed", 0)
        cp.chunks_indexed = data.get("chunks_indexed", 0)
        cp.chunks_pruned = data.get("chunks_pruned", 0)
        cp.current_file = data.get("current_file", "")
        cp.cancel_requested = data.get("cancel_requested", False)
        cp.report = data.get("report")
        return cp

    # -- persistence ----------------------------------------------------
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": CHECKPOINT_VERSION,
            "job_id": self.job_id,
            "ingest_path": self.ingest_path,
            "sync": self.sync,
            "signature": self.signature,
            "status": self.status,
            "completed_files": sorted(self.completed_files),
            "errors": self.errors,
            "files_indexed": self.files_indexed,
            "chunks_indexed": self.chunks_indexed,
            "chunks_pruned": self.chunks_pruned,
            "current_file": self.current_file,
            "cancel_requested": self.cancel_requested,
            "report": self.report,
        }
        # Atomic write: a crash mid-write leaves the previous good file intact.
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- resume / invalidation ------------------------------------------
    def reset_if_signature_changed(self, signature: str) -> None:
        """Drop completed-file state when the extraction config changed, so the
        run re-extracts everything with the new provider/model."""
        if self.signature != signature:
            self.completed_files.clear()
            self.signature = signature

    def is_file_done(self, file_path) -> bool:
        return str(file_path) in self.completed_files

    def mark_file_done(self, file_path) -> None:
        self.completed_files.add(str(file_path))

    # -- cancellation ---------------------------------------------------
    @property
    def cancelled(self) -> bool:
        return self.cancel_requested

    def request_cancel(self) -> None:
        self.cancel_requested = True

    # -- progress -------------------------------------------------------
    def update_from_report(self, report, current_file: Optional[str] = None) -> None:
        self.files_indexed = report.files_indexed
        self.chunks_indexed = report.chunks_indexed
        self.chunks_pruned = report.chunks_pruned
        self.errors = report.errors
        if current_file is not None:
            self.current_file = current_file

    def to_status(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "ingest_path": self.ingest_path,
            "cancel_requested": self.cancel_requested,
            "progress": {
                "files_indexed": self.files_indexed,
                "chunks_indexed": self.chunks_indexed,
                "chunks_pruned": self.chunks_pruned,
                "current_file": self.current_file,
                "completed_files": len(self.completed_files),
            },
            "errors": self.errors,
            "report": self.report,
        }
