from opendomainmcp.models import KnowledgeUnit, Chunk
from opendomainmcp.graph.models import WorkflowStep


def test_knowledge_unit_workflow_defaults_empty():
    k = KnowledgeUnit()
    assert k.workflow == {}
    assert k.is_empty() is True  # workflow must not change emptiness semantics


def test_chunk_index_defaults_none_and_not_in_hash():
    a = Chunk(text="x", source="s", start_line=1, end_line=2)
    b = Chunk(text="x", source="s", start_line=1, end_line=2)
    a.chunk_index = 0
    b.chunk_index = 9
    assert a.chunk_index == 0
    assert a.id == b.id  # chunk_index must NOT affect content hash / id


def test_workflow_step_dataclass():
    s = WorkflowStep(step_order=1, text="do it")
    assert s.precondition == ""
