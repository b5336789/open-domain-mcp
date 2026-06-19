# tests/test_pipeline_workflow_sync.py
from opendomainmcp.config import Settings
from opendomainmcp.ingest.pipeline import Pipeline
from opendomainmcp.models import KnowledgeUnit


class _WorkflowExtractor:
    """Splits a 'runbook' file into a 2-chunk workflow keyed by step markers.
    Each chunk's text is a step line like 'S1 test' / 'S2 deploy'."""
    def extract(self, text, kind, language=None):
        order = int(text.split()[0][1:])           # 'S1 test' -> 1
        return KnowledgeUnit(
            summary=text, knowledge_type="Runbook", audience=["operations"],
            confidence=1.0,
            workflow={"name": "Deploy", "prerequisites": ["perm"],
                      "steps": [{"order": order, "text": text}]})


def test_ingest_populates_and_orders_workflow(tmp_path, store, fake_graph):
    # one file, small chunk_size so it splits into two chunks in document order
    f = tmp_path / "runbook.txt"
    f.write_text("S1 test\n\nS2 deploy")
    p = Pipeline(store, _WorkflowExtractor(),
                 Settings(chunk_size=9, chunk_overlap=0), graph=fake_graph)
    p.ingest_path(str(f))
    wf = fake_graph.get_workflow("Deploy")
    assert wf is not None
    assert [s["text"] for s in wf["steps"]] == ["S1 test", "S2 deploy"]  # document order
    assert wf["prerequisites"] == ["perm"]


def test_reingest_prunes_stale_workflow(tmp_path, store, fake_graph):
    f = tmp_path / "runbook.txt"
    f.write_text("S1 test")
    p = Pipeline(store, _WorkflowExtractor(),
                 Settings(chunk_size=200, chunk_overlap=0), graph=fake_graph)
    p.ingest_path(str(f))
    assert fake_graph.get_workflow("Deploy") is not None
    f.write_text("S1 different")            # same chunk id changes -> old pruned
    p.ingest_path(str(f))
    wf = fake_graph.get_workflow("Deploy")
    assert [s["text"] for s in wf["steps"]] == ["S1 different"]
