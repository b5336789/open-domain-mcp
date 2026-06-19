import os
import pytest
from opendomainmcp.graph.models import WorkflowStep

pytestmark = pytest.mark.integration


@pytest.fixture
def maria_store():
    if not os.getenv("GRAPH_DB_HOST"):
        pytest.skip("MariaDB integration env not configured (set GRAPH_DB_HOST)")
    from opendomainmcp.graph.store import MariaGraphStore
    store = MariaGraphStore(
        host=os.environ["GRAPH_DB_HOST"], port=int(os.getenv("GRAPH_DB_PORT", "3306")),
        user=os.environ["GRAPH_DB_USER"], password=os.getenv("GRAPH_DB_PASSWORD", ""),
        database=os.environ["GRAPH_DB_NAME"], collection="wf-it")
    store.ensure_schema()
    store.delete_for_chunks(["wf-c1", "wf-c2"])
    return store


def test_mariadb_workflow_roundtrip(maria_store):
    maria_store.upsert_workflow("Deploy", "wf-c1", 0,
                                [WorkflowStep(1, "test"), WorkflowStep(2, "tag")], ["perm"])
    maria_store.upsert_workflow("deploy", "wf-c2", 1,
                                [WorkflowStep(1, "ship")], ["perm", "ci"])
    wf = maria_store.get_workflow("DEPLOY")
    assert [s["text"] for s in wf["steps"]] == ["test", "tag", "ship"]
    assert sorted(wf["prerequisites"]) == ["ci", "perm"]
    assert {w["name"] for w in maria_store.list_workflows()} >= {"Deploy"}
    maria_store.delete_for_chunks(["wf-c1", "wf-c2"])
    assert maria_store.get_workflow("Deploy") is None
