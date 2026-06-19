# tests/test_build_workflow.py
from opendomainmcp.models import KnowledgeUnit
from opendomainmcp.graph.workflow import build_workflow


def test_build_workflow_maps_and_sorts_steps():
    k = KnowledgeUnit(workflow={
        "name": "Deploy", "prerequisites": ["perm"],
        "steps": [{"order": 2, "text": "deploy", "precondition": "tests ok"},
                  {"order": 1, "text": "test", "precondition": ""}]})
    steps, prereqs, name = build_workflow(k)
    assert name == "Deploy"
    assert prereqs == ["perm"]
    assert [(s.step_order, s.text) for s in steps] == [(1, "test"), (2, "deploy")]
    assert steps[1].precondition == "tests ok"


def test_build_workflow_empty_when_no_workflow():
    assert build_workflow(KnowledgeUnit()) == ([], [], "")
