"""Pipeline wiring of code dependency edges (task 4.4).

Mirrors test_pipeline_graph_sync.py: ingest a tiny code file through the real
Pipeline with the offline FakeGraphStore and assert ``imports`` edges land.
"""

from opendomainmcp.config import Settings
from opendomainmcp.graph.deps import IMPORTS_RELATION
from opendomainmcp.ingest.pipeline import Pipeline


def _pipeline(store, fake_extractor, fake_graph):
    return Pipeline(store, fake_extractor, Settings(chunk_size=200, chunk_overlap=20),
                    graph=fake_graph)


def _edges(fake_graph):
    return fake_graph._slot()["edges"]


def test_ingest_code_populates_import_edges(tmp_path, store, fake_extractor, fake_graph):
    f = tmp_path / "mod.py"
    f.write_text("import os\nfrom pkg import thing\n\n\ndef run():\n    return os.getcwd()\n")
    _pipeline(store, fake_extractor, fake_graph).ingest_path(str(f))

    import_edges = [e for e in _edges(fake_graph) if e.relation_type == IMPORTS_RELATION]
    assert import_edges, "expected imports edges to be upserted"
    assert {"os", "pkg"} <= {e.dst for e in import_edges}
    # A module entity exists for the imported modules.
    assert fake_graph.get_entity("os") is not None
    assert fake_graph.get_entity("os")["type"] == "module"


def test_ingest_code_without_imports_adds_no_import_edges(
    tmp_path, store, fake_extractor, fake_graph
):
    f = tmp_path / "plain.py"
    f.write_text("def add(a, b):\n    return a + b\n")
    _pipeline(store, fake_extractor, fake_graph).ingest_path(str(f))

    import_edges = [e for e in _edges(fake_graph) if e.relation_type == IMPORTS_RELATION]
    assert import_edges == []


def test_text_file_produces_no_import_edges(tmp_path, store, fake_extractor, fake_graph):
    f = tmp_path / "notes.txt"
    f.write_text("import os is just prose here, not code.")
    _pipeline(store, fake_extractor, fake_graph).ingest_path(str(f))

    import_edges = [e for e in _edges(fake_graph) if e.relation_type == IMPORTS_RELATION]
    assert import_edges == []
