import zipfile

import pytest

from opendomainmcp.ingest.sources import (
    SourceError,
    is_git_spec,
    is_zip_spec,
    prepared_source,
)


def test_git_spec_detection():
    assert is_git_spec("https://github.com/owner/repo")
    assert is_git_spec("https://example.com/repo.git")
    assert is_git_spec("git@github.com:owner/repo.git")
    assert is_git_spec("git+https://x/y")
    assert not is_git_spec("/local/path")
    assert not is_git_spec("https://example.com/docs.pdf")


def test_zip_spec_detection(tmp_path):
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.txt", "hi")
    assert is_zip_spec(str(archive))
    assert not is_zip_spec(str(tmp_path / "missing.zip"))
    assert not is_zip_spec("/some/dir")


def test_prepared_source_passthrough_for_plain_path(tmp_path):
    with prepared_source(str(tmp_path), tmp_path) as prepared:
        assert prepared is None


def test_prepared_source_extracts_zip(tmp_path):
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("notes.md", "content")
        zf.writestr("sub/code.py", "x = 1\n")
    with prepared_source(str(archive), tmp_path) as prepared:
        assert prepared is not None
        assert (prepared / "notes.md").read_text() == "content"
        assert (prepared / "sub" / "code.py").exists()
    # the temp extraction directory is cleaned up afterwards
    assert not prepared.exists()


def test_prepared_source_rejects_zip_slip(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.txt", "pwned")
    with pytest.raises(SourceError):
        with prepared_source(str(archive), tmp_path):
            pass


def test_ingest_zip_archive_end_to_end(pipeline, store, tmp_path):
    archive = tmp_path / "proj.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.md", "Vector databases store embeddings.\n")
        zf.writestr("calc.py", "def add(a, b):\n    return a + b\n")
    report = pipeline.ingest_path(str(archive))
    assert report.files_indexed == 2
    assert store.stats()["count"] >= 2
