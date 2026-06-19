# tests/test_workflow_mcp.py
from opendomainmcp.config import Settings
from opendomainmcp.context import Context
from opendomainmcp.graph.models import WorkflowStep
from opendomainmcp.server import graph_tool_handlers


def _ctx(store, fake_graph):
    fake_graph.upsert_workflow("Deploy", "c1", 0, [WorkflowStep(1, "test")], ["perm"])
    return Context(settings=Settings(), store=store, pipeline=None, graph=fake_graph)


def test_get_workflow_steps_tool(store, fake_graph):
    h = graph_tool_handlers(_ctx(store, fake_graph))
    out = h["get_workflow_steps"](name="Deploy")
    assert out["workflow_name"] == "Deploy"
    assert out["steps"][0]["text"] == "test"


def test_list_workflows_tool(store, fake_graph):
    h = graph_tool_handlers(_ctx(store, fake_graph))
    assert h["list_workflows"]() == [{"name": "Deploy"}]


def test_get_workflow_steps_tool_missing(store, fake_graph):
    h = graph_tool_handlers(_ctx(store, fake_graph))
    out = h["get_workflow_steps"](name="missing")
    assert out == {"workflow_name": "missing", "prerequisites": [], "steps": []}
