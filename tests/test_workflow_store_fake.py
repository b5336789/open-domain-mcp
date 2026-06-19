from opendomainmcp.graph.models import WorkflowStep


def _steps(*pairs):
    return [WorkflowStep(step_order=o, text=t) for o, t in pairs]


def test_get_workflow_merges_chunks_in_order(fake_graph):
    # two chunks of the same workflow; chunk_index drives cross-chunk ordering
    fake_graph.upsert_workflow("Deploy", "c1", 0, _steps((1, "test"), (2, "tag")), ["perm"])
    fake_graph.upsert_workflow("deploy", "c2", 1, _steps((1, "ship"), (2, "watch")), ["perm", "ci"])
    wf = fake_graph.get_workflow("DEPLOY")  # lookup is case-insensitive (normalized key)
    assert [s["text"] for s in wf["steps"]] == ["test", "tag", "ship", "watch"]
    assert sorted(wf["prerequisites"]) == ["ci", "perm"]  # deduped across chunks


def test_get_workflow_missing_returns_none(fake_graph):
    assert fake_graph.get_workflow("nope") is None


def test_delete_for_chunks_prunes_workflow(fake_graph):
    fake_graph.upsert_workflow("Deploy", "c1", 0, _steps((1, "test")), ["perm"])
    fake_graph.delete_for_chunks(["c1"])
    assert fake_graph.get_workflow("Deploy") is None


def test_list_workflows(fake_graph):
    fake_graph.upsert_workflow("Deploy", "c1", 0, _steps((1, "x")), [])
    fake_graph.upsert_workflow("Rollback", "c2", 0, _steps((1, "y")), [])
    names = {w["name"] for w in fake_graph.list_workflows()}
    assert names == {"Deploy", "Rollback"}
    assert [w["name"] for w in fake_graph.list_workflows(q="roll")] == ["Rollback"]


def test_get_workflow_precondition_defaults_to_empty_string(fake_graph):
    """Verify precondition coalesces None to '' to match MariaGraphStore behavior."""
    # Create a step with no precondition (defaults to None in WorkflowStep)
    fake_graph.upsert_workflow("NoPrereq", "c1", 0, _steps((1, "x")), [])
    wf = fake_graph.get_workflow("NoPrereq")
    assert wf is not None
    assert len(wf["steps"]) == 1
    assert wf["steps"][0]["precondition"] == ""
