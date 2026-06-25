from pathlib import Path

from opendomainmcp.config import Settings
from opendomainmcp.context import Context
from opendomainmcp.models import Chunk, KnowledgeUnit
from opendomainmcp.tasks.runners import run_ingest, run_synthesize, run_extract
from opendomainmcp.tasks.store import TaskStore


def _never_cancel():
    return False


def test_pipeline_list_files(pipeline, tmp_path):
    d = tmp_path / "src"
    d.mkdir()
    (d / "a.py").write_text("def a():\n    return 1\n")
    (d / "b.md").write_text("Beta.\n")
    files = pipeline.list_files(str(d))
    assert sorted(Path(f).name for f in files) == ["a.py", "b.md"]


def test_run_ingest_enumerates_children_and_reports(store, pipeline, fake_graph, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def a():\n    return 1\n")
    (src / "b.md").write_text("Beta billing.\n")
    ctx = Context(settings=Settings(data_dir=tmp_path), store=store,
                  pipeline=pipeline, graph=fake_graph)
    ts = TaskStore(tmp_path)
    task = ts.create("ingest", "Ingest", "c", {"path": str(src), "sync": False})

    run_ingest(ctx, ts, task, _never_cancel)

    t = ts.get(task.id)
    assert t.total == 2 and t.done == 2
    assert t.result["files_indexed"] == 2
    page = ts.read_children(task.id, 0, 10)
    assert {c["name"] for c in page["children"]} == {str(src / "a.py"), str(src / "b.md")}


def test_run_extract_reextracts_without_reembedding(store, fake_graph, tmp_path, monkeypatch):
    # Seed one code chunk, then re-extract should update metadata via update_metadata.
    ku = KnowledgeUnit(summary="old", concepts=["x"], knowledge_type="Code")
    store.upsert([Chunk(text="def f(): pass", source="m.py", kind="code",
                        start_line=1, end_line=1, knowledge=ku)])
    ctx = Context(settings=Settings(data_dir=tmp_path), store=store,
                  pipeline=None, graph=fake_graph)

    class _Ext:
        def extract(self, text, kind, language=None):
            return KnowledgeUnit(summary="new summary", concepts=["y"],
                                 knowledge_type="Code")
    monkeypatch.setattr("opendomainmcp.tasks.runners.get_extractor", lambda s: _Ext())

    ts = TaskStore(tmp_path)
    task = ts.create("extract", "Re-extract", "c", {"source": "m.py"})
    run_extract(ctx, ts, task, _never_cancel)

    t = ts.get(task.id)
    assert t.done == 1 and t.total == 1
    item = next(i for i in store.get_items(limit=10) if i["metadata"]["source"] == "m.py")
    assert item["metadata"]["summary"] == "new summary"
