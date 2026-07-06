import pytest

from opendomainmcp import cli
from opendomainmcp.context import Context
from opendomainmcp.models import Chunk, KnowledgeUnit


def test_cli_ingest_search_stats(monkeypatch, capsys, tmp_path, store, pipeline, fake_graph):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    ctx = Context(settings=pipeline._settings, store=store, pipeline=pipeline, graph=fake_graph)
    monkeypatch.setattr(cli, "build_context", lambda: ctx)

    assert cli.main(["ingest", str(tmp_path)]) == 0
    assert "Indexed 1 files" in capsys.readouterr().out

    assert cli.main(["search", "add two numbers", "--kind", "code"]) == 0
    out = capsys.readouterr().out
    assert "add" in out

    assert cli.main(["stats"]) == 0
    assert "count" in capsys.readouterr().out


def _ctx(store, pipeline, fake_graph):
    return Context(settings=pipeline._settings, store=store, pipeline=pipeline, graph=fake_graph)


def _review_status(store, chunk_id):
    return store.get_item(chunk_id)["metadata"].get("review_status")


def test_backfill_review_stamps_missing_only(
    monkeypatch, capsys, store, pipeline, fake_graph
):
    # A chunk with no knowledge has no review_status; one with knowledge does.
    missing = Chunk(text="missing status chunk", source="a.md")
    approved = Chunk(
        text="already reviewed chunk", source="b.md",
        knowledge=KnowledgeUnit(summary="s", review_status="approved"),
    )
    store.upsert([missing, approved])
    assert _review_status(store, missing.id) is None
    assert _review_status(store, approved.id) == "approved"

    monkeypatch.setattr(cli, "build_context", lambda: _ctx(store, pipeline, fake_graph))
    assert cli.main(["backfill-review", "--status", "pending"]) == 0
    out = capsys.readouterr().out
    assert "1 chunk" in out

    # Only the missing one was stamped; the approved one is untouched.
    assert _review_status(store, missing.id) == "pending"
    assert _review_status(store, approved.id) == "approved"


def test_backfill_review_all_restamps_everything(
    monkeypatch, capsys, store, pipeline, fake_graph
):
    approved = Chunk(
        text="already reviewed chunk", source="b.md",
        knowledge=KnowledgeUnit(summary="s", review_status="approved"),
    )
    store.upsert([approved])

    monkeypatch.setattr(cli, "build_context", lambda: _ctx(store, pipeline, fake_graph))
    assert cli.main(["backfill-review", "--status", "rejected", "--all"]) == 0
    assert "1 chunk" in capsys.readouterr().out
    assert _review_status(store, approved.id) == "rejected"


def test_backfill_review_status_invalid_rejected(store):
    with pytest.raises(ValueError):
        store.backfill_review_status(status="bogus")


def test_ingest_help_documents_newer_sources():
    parser = cli.build_parser()
    sub = parser._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    help_text = sub["ingest"].format_help()
    for term in ("Git", "zip", "OpenAPI", "GraphQL"):
        assert term in help_text


class _FakeCtx:
    """Minimal context double for synthesize tests."""

    def __init__(self):
        self.store = None
        self.settings = None
        self.graph = None


def test_cli_search_includes_article_with_marker(monkeypatch, capsys):
    from opendomainmcp.models import SearchResult

    class _FakeSettings:
        search_mode = "vector"

    class _FakeCtxSearch:
        store = None
        settings = _FakeSettings()
        graph = None

    def fake_unified(store, query, *, top_k, mode, settings, where=None,
                     source_contains=None):
        return [SearchResult(id="art", text="body", score=0.9,
                             metadata={"kind": "article", "title": "Order Rule"})]

    monkeypatch.setattr(cli, "build_context", lambda **kw: _FakeCtxSearch())
    monkeypatch.setattr("opendomainmcp.retrieval.search_unified", fake_unified)
    rc = cli.main(["search", "approval"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[article]" in out and "Order Rule" in out


def test_cli_search_prints_chunk_output_unchanged(monkeypatch, capsys):
    from opendomainmcp.models import SearchResult

    class _FakeSettings:
        search_mode = "vector"

    class _FakeCtxSearch:
        store = None
        settings = _FakeSettings()
        graph = None

    def fake_unified(store, query, *, top_k, mode, settings, where=None,
                     source_contains=None):
        return [SearchResult(id="c1", text="python decorators wrap functions", score=0.8,
                             metadata={"kind": "code", "source": "deco.py", "symbol": "wrap"})]

    monkeypatch.setattr(cli, "build_context", lambda **kw: _FakeCtxSearch())
    monkeypatch.setattr("opendomainmcp.retrieval.search_unified", fake_unified)
    rc = cli.main(["search", "decorators"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "deco.py::wrap" in out
    assert "python decorators wrap functions" in out
    assert "[article]" not in out


def test_synthesize_command_prints_report(monkeypatch, capsys):
    from opendomainmcp.synthesis import SynthesisReport

    captured = {}

    def fake_synth(store, settings, *, graph=None, limit=None, dry_run=False):
        captured["limit"] = limit
        captured["dry_run"] = dry_run
        return SynthesisReport(topics_gated=2, articles_written=2, stored=1,
                               rejected=[{"topic": "x", "verdict": {}}])

    monkeypatch.setattr(cli, "build_context", lambda **kw: _FakeCtx())
    monkeypatch.setattr("opendomainmcp.synthesis.synthesize_articles", fake_synth)
    rc = cli.main(["synthesize", "--limit", "5"])
    out = capsys.readouterr().out
    assert rc == 0
    assert captured["limit"] == 5 and captured["dry_run"] is False
    assert "Stored 1" in out and "Rejected 1" in out


def test_cli_ingest_prints_evidence_counts(monkeypatch, capsys, tmp_path, store, fake_graph):
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline
    from opendomainmcp.models import KnowledgeUnit

    class EvidenceExtractor:
        def extract(self, text, kind, language=None):
            return KnowledgeUnit(
                summary="S", knowledge_type="Code", confidence=0.8,
                evidence=[{"claim": "real", "quote": text[:10]},
                          {"claim": "fake", "quote": "zz_not_in_text_zz"}])

    (tmp_path / "billing.py").write_text("def charge(amt):\n    return amt\n")
    settings = Settings(chunk_size=200, chunk_overlap=20)
    p = Pipeline(store, EvidenceExtractor(), settings, graph=fake_graph)
    ctx = Context(settings=settings, store=store, pipeline=p, graph=fake_graph)
    monkeypatch.setattr(cli, "build_context", lambda **_: ctx)
    assert cli.main(["ingest", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Evidence:" in out and "verified" in out and "unverified" in out


def test_ingest_cli_passes_filter_flags(monkeypatch, capsys, tmp_path, store, pipeline, fake_graph):
    (tmp_path / "billing.py").write_text("def charge():\n    return 1\n")
    (tmp_path / "test_billing.py").write_text("def test_c():\n    pass\n")

    ctx = Context(settings=pipeline._settings, store=store, pipeline=pipeline, graph=fake_graph)
    monkeypatch.setattr(cli, "build_context", lambda **_: ctx)

    parser = cli.build_parser()
    args = parser.parse_args(["ingest", str(tmp_path), "--exclude", "*.md"])
    assert args.exclude == ["*.md"]
    assert args.no_default_excludes is False
    rc = cli.main(["ingest", str(tmp_path), "--exclude", "*.md"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Filtered 1 file(s)" in out  # test_billing.py

    args2 = parser.parse_args(["ingest", str(tmp_path), "--no-default-excludes"])
    assert args2.no_default_excludes is True
    rc = cli.main(["ingest", str(tmp_path), "--no-default-excludes"])
    assert rc == 0
    assert "Filtered" not in capsys.readouterr().out


def test_codegraph_persist_with_null_graph_returns_1(tmp_path, capsys, monkeypatch):
    """--persist with NullGraphStore must return rc=1 and print to stderr
    (4A final-review fix 4)."""
    (tmp_path / "A.java").write_text(
        "public class A { public void run() {} }")

    from opendomainmcp.config import Settings
    from opendomainmcp.graph.store import NullGraphStore

    class _CtxNullGraph:
        settings = Settings()
        graph = NullGraphStore()

    import opendomainmcp.cli as cli
    monkeypatch.setattr(cli, "build_context", lambda **_: _CtxNullGraph())
    rc = cli.main(["codegraph", str(tmp_path), "--persist"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "graph store not configured" in err


def test_codegraph_persist_with_fake_graph_returns_0(tmp_path, capsys, monkeypatch, fake_graph):
    """--persist with a real graph store (FakeGraphStore) must succeed (rc=0)
    (4A final-review fix 4 — regression guard)."""
    (tmp_path / "A.java").write_text(
        "public class A { public void run() {} }")

    from opendomainmcp.config import Settings

    class _CtxFakeGraph:
        settings = Settings()
        graph = fake_graph

    import opendomainmcp.cli as cli
    monkeypatch.setattr(cli, "build_context", lambda **_: _CtxFakeGraph())
    rc = cli.main(["codegraph", str(tmp_path), "--persist"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "persisted" in out


def test_codegraph_cli_stats(tmp_path, capsys, monkeypatch, fake_graph):
    (tmp_path / "A.java").write_text(
        "public class A { public void run() { help(); } void help() {} }")

    from opendomainmcp.config import Settings

    class _FakeCtxCodegraph:
        settings = Settings()
        graph = fake_graph

    import opendomainmcp.cli as cli
    monkeypatch.setattr(cli, "build_context", lambda **_: _FakeCtxCodegraph())
    rc = cli.main(["codegraph", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "functions" in out and "entry" in out.lower()


def test_codegraph_analyze_cli(tmp_path, capsys, monkeypatch, store, fake_graph):
    (tmp_path / "A.java").write_text(
        "public class A { public void run() { help(); } void help() {} }")

    called = {}

    def fake_analyze(root, st, settings, graph, progress=None, analyzer=None,
                     extractor=None):
        called["root"] = str(root)
        return {"functions_analyzed": 2, "chains_stored": 1,
                "chunks_backfilled": 0, "fallback_extracted": 0,
                "coverage": 0.0, "errors": []}

    monkeypatch.setattr("opendomainmcp.codegraph.analyze.analyze_corpus",
                        fake_analyze)

    import types
    from opendomainmcp.config import Settings

    fake_ctx = types.SimpleNamespace(settings=Settings(), graph=fake_graph, store=store)

    import opendomainmcp.cli as cli
    monkeypatch.setattr(cli, "build_context", lambda **_: fake_ctx)
    rc = cli.main(["codegraph", str(tmp_path), "--analyze"])
    assert rc == 0 and called["root"] == str(tmp_path)
    assert "chains_stored" in capsys.readouterr().out
