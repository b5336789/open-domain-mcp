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
