"""Ingestion path confinement (ingest_root) — guards against indexing files
outside an allowed directory tree when the server is exposed."""

import pytest

from opendomainmcp.config import Settings
from opendomainmcp.ingest.pipeline import PathNotAllowedError, Pipeline


def _pipeline(store, fake_extractor, root):
    settings = Settings(chunk_size=200, chunk_overlap=20, ingest_root=root)
    return Pipeline(store, fake_extractor, settings)


def test_path_outside_root_is_rejected(store, fake_extractor, tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("top secret")

    pipe = _pipeline(store, fake_extractor, root)
    with pytest.raises(PathNotAllowedError):
        pipe.ingest_path(outside)


def test_traversal_escape_is_rejected(store, fake_extractor, tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("top secret")

    pipe = _pipeline(store, fake_extractor, root)
    with pytest.raises(PathNotAllowedError):
        pipe.ingest_path(root / ".." / "secret.txt")


def test_path_within_root_is_ingested(store, fake_extractor, tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "notes.md").write_text(
        "# Note\n\nA vector database stores embeddings for similarity search.\n"
    )

    pipe = _pipeline(store, fake_extractor, root)
    report = pipe.ingest_path(root)
    assert report.files_indexed == 1


def test_symlink_file_escaping_root_is_skipped(store, fake_extractor, tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    (root / "keep.md").write_text("A vector database stores embeddings.\n")
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    link = root / "leak.md"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")

    pipe = _pipeline(store, fake_extractor, root)
    report = pipe.ingest_path(root)
    assert report.files_indexed == 1  # only keep.md
    assert any("leak.md" in s["path"] for s in report.skipped)
    assert all("secret" not in (e.get("path") or "") for e in report.errors)


def test_allowed_root_argument_overrides_when_no_setting(store, fake_extractor, tmp_path):
    # No ingest_root in settings, but the web layer passes allowed_root explicitly.
    root = tmp_path / "uploads"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("top secret")

    settings = Settings(chunk_size=200, chunk_overlap=20)
    pipe = Pipeline(store, fake_extractor, settings)
    with pytest.raises(PathNotAllowedError):
        pipe.ingest_path(outside, allowed_root=root)


def test_no_root_allows_arbitrary_paths(store, fake_extractor, tmp_path):
    # Default (no ingest_root): behaviour is unrestricted, preserving local use.
    f = tmp_path / "anywhere.md"
    f.write_text("A vector database stores embeddings for similarity search.\n")

    settings = Settings(chunk_size=200, chunk_overlap=20)
    pipe = Pipeline(store, fake_extractor, settings)
    report = pipe.ingest_path(f)
    assert report.files_indexed == 1
