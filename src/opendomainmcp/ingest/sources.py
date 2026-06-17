"""Resolve ingestion sources that are not plain local paths.

A *source spec* may be a local file/directory (used as-is), a Git repository URL
(shallow-cloned), or a ``.zip`` archive (safely extracted). Git/zip sources are
materialised under ``<data_dir>/.sources/<token>`` and removed afterwards. The
materialised directory doubles as the ``allowed_root`` so the pipeline's existing
path-confinement protects the rest of the run.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

_GIT_PREFIXES = ("git@", "git+", "ssh://", "git://")
_GIT_HOSTS = ("github.com", "gitlab.com", "bitbucket.org")


class SourceError(Exception):
    """Raised when a Git clone or zip extraction fails or is unsafe."""


def is_git_spec(spec: str) -> bool:
    s = spec.strip()
    if s.startswith(_GIT_PREFIXES) or s.endswith(".git"):
        return True
    if s.startswith(("http://", "https://")):
        return any(host in s for host in _GIT_HOSTS)
    return False


def is_zip_spec(spec: str) -> bool:
    s = spec.strip()
    return s.lower().endswith(".zip") and Path(s).is_file()


def _git_clone(spec: str, dest: Path, timeout: float = 300.0) -> None:
    url = spec.strip()
    if url.startswith("git+"):
        url = url[len("git+"):]
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            check=True, capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as exc:  # git not installed
        raise SourceError("git is not available on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceError(f"git clone timed out after {timeout}s") from exc
    except subprocess.CalledProcessError as exc:
        raise SourceError(f"git clone failed: {exc.stderr.strip() or exc}") from exc


def _safe_extract(zip_path: Path, dest: Path) -> None:
    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            # Reject absolute paths and any entry that would escape dest
            # (zip-slip via "../"), checking the resolved target.
            target = (dest / member).resolve()
            if target != dest and dest not in target.parents:
                raise SourceError(f"unsafe path in zip archive: {member!r}")
        zf.extractall(dest)


@contextmanager
def prepared_source(spec, data_dir):
    """Yield a local path to ingest, or ``None`` if ``spec`` is already a path.

    Git/zip sources are materialised in a temporary directory that is removed on
    exit (Fail Loud: clone/extraction errors propagate as :class:`SourceError`).
    """
    spec = str(spec)
    if not (is_git_spec(spec) or is_zip_spec(spec)):
        yield None
        return
    root = Path(data_dir) / ".sources" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    try:
        if is_git_spec(spec):
            _git_clone(spec, root)
        else:
            _safe_extract(Path(spec), root)
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
