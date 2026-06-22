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


def test_review_mode_marks_extractions_pending(store, fake_extractor, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline

    _make_corpus(tmp_path)
    settings = Settings(chunk_size=200, chunk_overlap=20, review_mode=True)
    Pipeline(store, fake_extractor, settings).ingest_path(tmp_path)

    items = store.get_items(limit=100)
    classified = [i for i in items if i["metadata"].get("review_status")]
    assert classified and all(
        i["metadata"]["review_status"] == "pending" for i in classified
    )


def test_default_mode_marks_extractions_approved(pipeline, store, tmp_path):
    _make_corpus(tmp_path)
    pipeline.ingest_path(tmp_path)
    items = store.get_items(limit=100)
    classified = [i for i in items if i["metadata"].get("review_status")]
    assert classified and all(
        i["metadata"]["review_status"] == "approved" for i in classified
    )


def test_progress_events_emitted(pipeline, tmp_path):
    _make_corpus(tmp_path)
    events = []
    pipeline.ingest_path(tmp_path, progress=events.append)
    stages = {e["stage"] for e in events}
    assert {"load", "split", "embed", "store", "done"} <= stages
    assert any(e["stage"] == "skip" for e in events)  # the binary file


def test_load_and_split_returns_indexed_chunks(pipeline, tmp_path):
    f = tmp_path / "calc.py"
    f.write_text("def add(a, b):\n    return a + b\n")
    chunks = pipeline._load_and_split(f)
    assert chunks and all(c.chunk_index == i for i, c in enumerate(chunks))
    assert all(c.kind == "code" for c in chunks)


def test_batch_mode_uses_prepass_cache(store, fake_graph, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline
    from opendomainmcp.models import KnowledgeUnit

    (tmp_path / "notes.md").write_text(
        "# Vector databases\n\nEmbeddings power similarity search for RAG.\n"
    )

    class BoomExtractor:  # live extraction must NOT happen in batch mode
        def extract(self, *a, **k):
            raise AssertionError("live extract called; cache miss in batch mode")

    settings = Settings(chunk_size=200, chunk_overlap=20,
                        extract_batch=True, llm_backend="anthropic")
    pipe = Pipeline(store, BoomExtractor(), settings, graph=fake_graph)

    # Fake batch extractor: cache every chunk text the pre-pass collects.
    class FakeBatch:
        def extract_many(self, items, progress=None):
            return {it.text_hash: KnowledgeUnit(summary=f"batch {it.kind}")
                    for it in items}

    pipe._build_batch_extractor = lambda: FakeBatch()

    report = pipe.ingest_path(tmp_path)
    assert report.files_indexed == 1
    items = store.get_items(limit=10)
    assert items and all(i["metadata"]["summary"].startswith("batch")
                         for i in items if "summary" in i["metadata"])


def test_batch_mode_requires_anthropic_backend(store, fake_graph, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline

    (tmp_path / "notes.md").write_text("# x\n\nsome content here for a chunk.\n")
    settings = Settings(chunk_size=200, chunk_overlap=20,
                        extract_batch=True, llm_backend="openai")
    pipe = Pipeline(store, None, settings, graph=fake_graph)

    import pytest
    with pytest.raises(ValueError, match="anthropic"):
        pipe.ingest_path(tmp_path)
