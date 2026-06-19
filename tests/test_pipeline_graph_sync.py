# tests/test_pipeline_graph_sync.py
from pathlib import Path

from opendomainmcp.config import Settings
from opendomainmcp.ingest.pipeline import Pipeline


def _pipeline(store, fake_extractor, fake_graph):
    return Pipeline(store, fake_extractor, Settings(chunk_size=200, chunk_overlap=20),
                    graph=fake_graph)


def test_ingest_populates_graph(tmp_path, store, fake_extractor, fake_graph):
    f = tmp_path / "a.txt"
    f.write_text("Payments depends on Ledger and integrates with Billing.")
    _pipeline(store, fake_extractor, fake_graph).ingest_path(str(f))
    # FakeExtractor emits one Concept entity named after the first word ("Payments").
    assert fake_graph.get_entity("Payments") is not None


def test_reingest_prunes_stale_graph_nodes(tmp_path, store, fake_extractor, fake_graph):
    f = tmp_path / "a.txt"
    f.write_text("Payments service.")
    p = _pipeline(store, fake_extractor, fake_graph)
    p.ingest_path(str(f))
    assert fake_graph.get_entity("Payments") is not None
    # Rewrite so the chunk id changes; the old chunk's graph rows must be pruned.
    f.write_text("Refunds workflow now.")
    p.ingest_path(str(f))
    assert fake_graph.get_entity("Payments") is None
    assert fake_graph.get_entity("Refunds") is not None
