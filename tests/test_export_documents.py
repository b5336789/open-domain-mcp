import pytest

from opendomainmcp.export import ExportError, export_documents
from tests.test_export_collect import FakeGraph, FakeStore, _article_item, _rule_item


class FakeCtx:
    def __init__(self, store, graph, data_dir):
        self.store, self.graph = store, graph
        self.settings = type("S", (), {"data_dir": str(data_dir)})()


def test_empty_corpus_fails_loud(tmp_path):
    ctx = FakeCtx(FakeStore([]), FakeGraph({}), tmp_path)
    with pytest.raises(ExportError, match="synthesize"):
        export_documents(ctx, tmp_path / "out", use_llm=False)


def test_end_to_end_no_llm(tmp_path):
    ctx = FakeCtx(FakeStore([_article_item(1), _rule_item(1)]),
                  FakeGraph({}), tmp_path)
    report = export_documents(ctx, tmp_path / "out", use_llm=False)
    assert (tmp_path / "out" / "index.md").exists()
    assert (tmp_path / "out" / "handbook.md").exists()
    assert report.counts["articles"] == 1


def test_end_to_end_with_fake_llms_and_zip(tmp_path):
    ctx = FakeCtx(FakeStore([_article_item(1), _rule_item(1)]),
                  FakeGraph({}), tmp_path)
    report = export_documents(
        ctx, tmp_path / "out", zip_output=True,
        translator=lambda t: f"譯{t}",
        organizer=lambda p: '{"domains": [{"name": "領域", "flows": [], '
                            '"articles": ["topic-1"], "rules": ["r1"]}]}')
    assert report.zip_path.endswith(".zip")
    from pathlib import Path
    assert Path(report.zip_path).exists()
    assert (tmp_path / "out" / "domains").is_dir()
    # translation cache persisted under data_dir
    assert (tmp_path / "translation_cache.json").exists()


def test_cli_export_no_llm(tmp_path, monkeypatch, capsys):
    from opendomainmcp import cli

    ctx = FakeCtx(FakeStore([_article_item(1), _rule_item(1)]),
                  FakeGraph({}), tmp_path)
    monkeypatch.setattr(cli, "build_context", lambda **kw: ctx)
    rc = cli.main(["export", "--out", str(tmp_path / "out"), "--no-llm"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Exported 1 article(s)" in out
