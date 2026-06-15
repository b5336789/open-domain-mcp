def _make_corpus(root):
    (root / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\n"
        "class Calculator:\n    def multiply(self, a, b):\n        return a * b\n"
    )
    (root / "notes.md").write_text(
        "# Vector databases\n\n"
        "A vector database stores embeddings and supports similarity search. "
        "It is the backbone of retrieval augmented generation.\n"
    )
    (root / "image.bin").write_bytes(b"\x00\xff\x80\x01\xfe")


def test_ingest_dir_indexes_and_skips(pipeline, store, tmp_path):
    _make_corpus(tmp_path)
    report = pipeline.ingest_path(tmp_path)

    assert report.files_indexed == 2  # py + md, binary skipped
    assert report.chunks_indexed >= 2
    assert any("image.bin" in s["path"] for s in report.skipped)
    assert store.stats()["count"] == report.chunks_indexed


def test_extracted_knowledge_is_stored_and_searchable(pipeline, store, tmp_path):
    _make_corpus(tmp_path)
    pipeline.ingest_path(tmp_path)

    results = store.search("similarity search over embeddings", top_k=1)
    assert results
    # FakeExtractor attaches a summary; it must be persisted as metadata.
    assert "summary" in results[0].metadata

    code_hit = store.search("add two numbers function", top_k=3)
    assert any(r.metadata.get("symbol") == "add" for r in code_hit)


def test_reingest_is_idempotent(pipeline, store, tmp_path):
    _make_corpus(tmp_path)
    first = pipeline.ingest_path(tmp_path)
    count_after_first = store.stats()["count"]
    pipeline.ingest_path(tmp_path)
    assert store.stats()["count"] == count_after_first == first.chunks_indexed


def test_progress_events_emitted(pipeline, tmp_path):
    _make_corpus(tmp_path)
    events = []
    pipeline.ingest_path(tmp_path, progress=events.append)
    stages = {e["stage"] for e in events}
    assert {"load", "split", "embed", "store", "done"} <= stages
    assert any(e["stage"] == "skip" for e in events)  # the binary file
