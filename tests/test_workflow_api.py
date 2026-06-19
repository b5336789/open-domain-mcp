# tests/test_workflow_api.py
from fastapi.testclient import TestClient
from opendomainmcp.api.app import create_app
from opendomainmcp.config import Settings
from opendomainmcp.context import Context
from opendomainmcp.graph.models import WorkflowStep


def _client(store, fake_graph):
    fake_graph.upsert_workflow("Deploy", "c1", 0,
                               [WorkflowStep(1, "test"), WorkflowStep(2, "ship")], ["perm"])
    ctx = Context(settings=Settings(), store=store, pipeline=None, graph=fake_graph)
    return TestClient(create_app(context=ctx))


def test_get_workflow_endpoint(store, fake_graph):
    resp = _client(store, fake_graph).get("/api/graph/workflow/Deploy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow_name"] == "Deploy"
    assert [s["text"] for s in body["steps"]] == ["test", "ship"]
    assert body["prerequisites"] == ["perm"]


def test_get_workflow_404(store, fake_graph):
    resp = _client(store, fake_graph).get("/api/graph/workflow/nope")
    assert resp.status_code == 404
    assert "error" in resp.json()


def test_list_workflows_endpoint(store, fake_graph):
    resp = _client(store, fake_graph).get("/api/graph/workflows")
    assert resp.status_code == 200
    assert resp.json()["items"] == [{"name": "Deploy"}]
