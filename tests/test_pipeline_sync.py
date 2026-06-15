"""Phase A (change-aware sync) and Phase B (concurrent extraction) tests."""


def test_editing_a_file_prunes_orphan_chunks(pipeline, store, tmp_path):
    src = tmp_path / "calc.py"
    src.write_text(
        "def add(a, b):\n    return a + b\n\n\n"
        "def subtract(a, b):\n    return a - b\n"
    )
    pipeline.ingest_path(src)
    first = store.stats()["count"]
    assert first >= 2

    # Rewrite the file so the old chunk ids (old line ranges / bodies) no longer exist.
    src.write_text("def add(a, b, c):\n    return a + b + c\n")
    report = pipeline.ingest_path(src)

    assert report.chunks_pruned >= 1
    # No orphans: the stored count equals what this file now produces.
    ids = store.get_ids_for_source(str(src))
    assert store.stats()["count"] == len(ids)
    assert store.search("add three numbers", top_k=1)


def test_dir_sync_removes_deleted_files(pipeline, store, tmp_path):
    keep = tmp_path / "keep.py"
    gone = tmp_path / "gone.py"
    keep.write_text("def keep():\n    return 1\n")
    gone.write_text("def gone():\n    return 2\n")
    pipeline.ingest_path(tmp_path)
    assert store.get_ids_for_source(str(gone))

    gone.unlink()
    report = pipeline.ingest_path(tmp_path, sync=True)

    assert report.chunks_pruned >= 1
    assert store.get_ids_for_source(str(gone)) == set()
    assert store.get_ids_for_source(str(keep))  # untouched


def test_dir_sync_off_keeps_deleted_files(pipeline, store, tmp_path):
    f = tmp_path / "a.py"
    f.write_text("def a():\n    return 1\n")
    pipeline.ingest_path(tmp_path)
    f.unlink()
    pipeline.ingest_path(tmp_path)  # sync defaults to False
    assert store.get_ids_for_source(str(f))  # still present


def test_concurrent_extraction_covers_all_chunks(store, fake_extractor, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline

    settings = Settings(chunk_size=120, chunk_overlap=10, extract_concurrency=4)
    pipeline = Pipeline(store, fake_extractor, settings)

    big = "\n\n".join(
        f"Section {i} explains topic {i}. " + " ".join(f"w{i}x{j}" for j in range(30))
        for i in range(12)
    )
    (tmp_path / "doc.md").write_text(big)
    report = pipeline.ingest_path(tmp_path)

    assert report.chunks_indexed > 1
    # Every stored chunk was extracted exactly once, even across threads.
    assert fake_extractor.calls == report.chunks_indexed
    assert not report.errors
