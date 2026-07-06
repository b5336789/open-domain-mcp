from pathlib import Path


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


def _make_mixed_corpus(root):
    (root / "billing.py").write_text("def charge(amount):\n    return amount\n")
    (root / "test_billing.py").write_text("def test_charge():\n    assert True\n")
    (root / "vendor").mkdir()
    (root / "vendor" / "lib.py").write_text("def vendored():\n    pass\n")
    (root / "gen.py").write_text("# autogenerated\ndef g():\n    pass\n")
    (root / "package-lock.json").write_text("{}")


def test_ingest_filters_non_business_files(pipeline, store, tmp_path):
    _make_mixed_corpus(tmp_path)
    report = pipeline.ingest_path(tmp_path)

    assert report.files_indexed == 1  # only billing.py
    rules = {f["path"].split("/")[-1]: f["rule"] for f in report.filtered}
    assert rules["test_billing.py"] == "test_*.py"
    assert rules["lib.py"] == "vendor/"
    assert rules["gen.py"] == "generated-marker"
    assert rules["package-lock.json"] == "package-lock.json"
    sources = store.get_all_sources()
    assert all("billing.py" in s for s in sources)


def test_filter_report_is_serialised_and_events_emitted(pipeline, tmp_path):
    _make_mixed_corpus(tmp_path)
    events = []
    report = pipeline.ingest_path(tmp_path, progress=events.append)
    assert "filtered" in report.to_dict()
    filter_events = [e for e in events if e["stage"] == "filter"]
    assert len(filter_events) == len(report.filtered) == 4


def test_per_run_exclude_and_no_defaults(pipeline, tmp_path):
    _make_mixed_corpus(tmp_path)
    report = pipeline.ingest_path(tmp_path, exclude=["billing.*"])
    assert report.files_indexed == 0
    assert any(f["rule"] == "billing.*" for f in report.filtered)

    report2 = pipeline.ingest_path(tmp_path, use_default_excludes=False)
    # only the binary-safety and content rules of the loader apply now
    assert report2.files_indexed == 5  # all .py/.json files ingested


def test_single_file_ingest_is_filtered_with_report(pipeline, tmp_path):
    f = tmp_path / "test_only.py"
    f.write_text("def test_x():\n    pass\n")
    report = pipeline.ingest_path(f)
    assert report.files_indexed == 0
    assert report.filtered == [{"path": str(f), "rule": "test_*.py"}]


def test_list_files_applies_filter(pipeline, tmp_path):
    _make_mixed_corpus(tmp_path)
    files = pipeline.list_files(tmp_path)
    assert [Path(f).name for f in files] == ["billing.py"]


def test_sync_prunes_chunks_of_newly_excluded_files(pipeline, store, tmp_path):
    f = tmp_path / "was_business.py"
    f.write_text("def rule():\n    return 1\n")
    pipeline.ingest_path(tmp_path)
    assert store.get_ids_for_source(str(f))

    # The file becomes excluded (per-run rule) — sync must prune its chunks.
    events = []
    report = pipeline.ingest_path(tmp_path, sync=True, exclude=["was_business.py"],
                                  progress=events.append)
    assert report.chunks_pruned > 0
    assert not store.get_ids_for_source(str(f))
    prune_events = [e for e in events if e["stage"] == "prune"]
    assert any(e["detail"] == "excluded" for e in prune_events)
    assert {"source": str(f), "reason": "excluded"} in report.pruned_sources


def test_plsql_file_ingests_via_line_fallback(pipeline, store, tmp_path):
    f = tmp_path / "pkg_billing.pkb"
    f.write_text("CREATE OR REPLACE PACKAGE BODY pkg_billing AS\n"
                 "  PROCEDURE validate_amount(p IN NUMBER) IS\n"
                 "  BEGIN\n    NULL;\n  END validate_amount;\nEND pkg_billing;\n")
    report = pipeline.ingest_path(f)
    assert report.files_indexed == 1
    items = store.get_items(limit=10, where={"language": "plsql"})
    assert items and all(i["metadata"]["kind"] == "code" for i in items)


def test_codegraph_extract_mode_skips_code_chunks(store, fake_extractor,
                                                  fake_graph, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline

    (tmp_path / "billing.py").write_text("def charge():\n    return 1\n")
    (tmp_path / "notes.md").write_text("# Pricing\nRules for pricing.\n")
    settings = Settings(chunk_size=200, chunk_overlap=20, codegraph_extract=True)
    Pipeline(store, fake_extractor, settings, graph=fake_graph).ingest_path(tmp_path)

    code = store.get_items(limit=50, where={"kind": "code"})
    text = store.get_items(limit=50, where={"kind": "text"})
    assert code and all(not i["metadata"].get("summary") for i in code)
    assert text and any(i["metadata"].get("summary") for i in text)


def test_codegraph_extract_mode_excludes_code_from_batch_prepass(
        store, fake_graph, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline

    (tmp_path / "billing.py").write_text("def charge():\n    return 1\n")
    (tmp_path / "notes.md").write_text("# Pricing\nRules for pricing.\n")
    settings = Settings(chunk_size=200, chunk_overlap=20,
                        codegraph_extract=True, extract_batch=True,
                        llm_backend="anthropic")

    class BoomExtractor:  # text chunks hit the batch cache, never live extract
        def extract(self, *a, **k):
            raise AssertionError("live extract called; cache miss in batch mode")

    pipe = Pipeline(store, BoomExtractor(), settings, graph=fake_graph)

    submitted = []

    class FakeBatch:
        def extract_many(self, items, progress=None):
            submitted.extend(items)
            return {}

    pipe._build_batch_extractor = lambda: FakeBatch()

    pipe.ingest_path(tmp_path)
    # Code chunks must not reach the (paid) batch API — _extract_all discards
    # their results anyway. Only the markdown content is submitted.
    assert submitted
    assert all(it.kind != "code" for it in submitted)
    assert any("Pricing" in it.text for it in submitted)


def test_evidence_verified_at_ingest(store, fake_graph, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline
    from opendomainmcp.models import KnowledgeUnit

    class EvidenceExtractor:
        def extract(self, text, kind, language=None):
            return KnowledgeUnit(
                summary="S", knowledge_type="Code", confidence=0.8,
                evidence=[{"claim": "real", "quote": text[:10]},
                          {"claim": "fake", "quote": "zz_not_in_text_zz"}])

    f = tmp_path / "billing.py"
    f.write_text("def charge(amt):\n    return amt\n")
    settings = Settings(chunk_size=200, chunk_overlap=20)
    report = Pipeline(store, EvidenceExtractor(), settings,
                      graph=fake_graph).ingest_path(f)

    assert report.evidence_verified >= 1 and report.evidence_unverified >= 1
    items = store.get_items(limit=10, where={"evidence_status": "partial"})
    assert items, "partial evidence_status must be stored and filterable"
    import json as _json
    ev = _json.loads(items[0]["metadata"]["evidence"])
    assert any(e["verified"] and e["start_line"] for e in ev)
    assert any(not e["verified"] for e in ev)


def test_unverified_evidence_penalizes_confidence(store, fake_graph, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.extract.verify import UNVERIFIED_PENALTY
    from opendomainmcp.ingest.pipeline import Pipeline
    from opendomainmcp.models import KnowledgeUnit

    class FabricatingExtractor:
        def extract(self, text, kind, language=None):
            return KnowledgeUnit(summary="S", knowledge_type="Code",
                                 confidence=0.8,
                                 evidence=[{"claim": "x", "quote": "not there"}])

    f = tmp_path / "a.py"
    f.write_text("def f():\n    return 1\n")
    Pipeline(store, FabricatingExtractor(), Settings(chunk_size=200,
             chunk_overlap=20), graph=fake_graph).ingest_path(f)
    items = store.get_items(limit=10, where={"evidence_status": "unverified"})
    assert items and abs(float(items[0]["metadata"]["confidence"])
                         - 0.8 * UNVERIFIED_PENALTY) < 1e-6


def test_evidence_counts_exact_under_concurrent_extraction(
        store, fake_graph, tmp_path):
    """Counter increments must not be lost under the ThreadPool (a lock guards
    the non-atomic int += on the shared report)."""
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline
    from opendomainmcp.models import KnowledgeUnit

    class EvidenceExtractor:  # exactly 1 verified + 1 unverified per chunk
        def extract(self, text, kind, language=None):
            return KnowledgeUnit(
                summary="S", knowledge_type="Code", confidence=0.8,
                evidence=[{"claim": "real", "quote": text[:10]},
                          {"claim": "fake", "quote": "zz_not_in_text_zz"}])

    f = tmp_path / "notes.md"
    f.write_text("\n\n".join(f"Paragraph {i}: billing rule number {i} "
                             f"applies to invoices." for i in range(20)))
    settings = Settings(chunk_size=80, chunk_overlap=0, extract_concurrency=8)
    report = Pipeline(store, EvidenceExtractor(), settings,
                      graph=fake_graph).ingest_path(f)

    assert report.chunks_indexed >= 4  # genuinely multi-chunk, parallel path
    assert report.evidence_verified == report.chunks_indexed
    assert report.evidence_unverified == report.chunks_indexed


def test_text_chunk_evidence_has_no_fabricated_line_numbers(store, fake_graph, tmp_path):
    """Text chunks have start_line=None; verified quotes must not fabricate line
    numbers — start_line/end_line must stay None for those chunks."""
    import json as _json

    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline
    from opendomainmcp.models import KnowledgeUnit

    # Build a long enough file to split into multiple chunks with chunk_size=200.
    # The extractor emits a quote from the LAST chunk's text.
    paragraphs = [
        "Alpha section: the billing engine processes invoices for all accounts.\n",
        "Beta section: the pricing engine computes rates based on usage tiers.\n",
        "Gamma section: the reconciliation engine audits every transaction daily.\n",
        "Delta section: the notification engine sends alerts to stakeholders.\n",
        "Epsilon section: the reporting engine aggregates data for dashboards.\n",
    ]
    content = "\n".join(p * 3 for p in paragraphs)
    f = tmp_path / "docs.md"
    f.write_text(content)

    class LateChunkExtractor:
        def extract(self, text, kind, language=None):
            # Use a substring of the chunk being extracted as the quote.
            quote = text.strip()[:30] if text.strip() else "missing"
            return KnowledgeUnit(
                summary="doc chunk", knowledge_type="Feature", confidence=0.9,
                evidence=[{"claim": "from chunk", "quote": quote}])

    settings = Settings(chunk_size=200, chunk_overlap=0)
    report = Pipeline(store, LateChunkExtractor(), settings,
                      graph=fake_graph).ingest_path(f)

    assert report.chunks_indexed >= 2, "file must split into multiple chunks"
    items = store.get_items(limit=50)
    for item in items:
        if item["metadata"].get("kind") != "text":
            continue
        raw_ev = item["metadata"].get("evidence")
        if not raw_ev:
            continue
        ev = _json.loads(raw_ev)
        for e in ev:
            if e.get("verified"):
                # Text chunks have no start_line → lines must be None
                assert e["start_line"] is None, (
                    f"fabricated start_line {e['start_line']} on text chunk")
                assert e["end_line"] is None
