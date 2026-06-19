from opendomainmcp import cli
from opendomainmcp.context import Context


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
