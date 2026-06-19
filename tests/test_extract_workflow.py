from opendomainmcp.extract.knowledge import _parse_workflow


def test_parse_workflow_happy_path():
    out = _parse_workflow({
        "name": "Deploy to prod",
        "prerequisites": ["deploy permission", "CI green"],
        "steps": [{"order": 1, "text": "run tests"},
                  {"order": 2, "text": "deploy", "precondition": "tests passed"}],
    })
    assert out["name"] == "Deploy to prod"
    assert out["prerequisites"] == ["deploy permission", "CI green"]
    assert out["steps"] == [
        {"order": 1, "text": "run tests", "precondition": ""},
        {"order": 2, "text": "deploy", "precondition": "tests passed"}]


def test_parse_workflow_requires_name_and_steps():
    assert _parse_workflow({"steps": [{"order": 1, "text": "x"}]}) == {}   # no name
    assert _parse_workflow({"name": "X", "steps": []}) == {}               # no steps
    assert _parse_workflow("junk") == {}


def test_parse_workflow_drops_empty_steps_and_defaults_order():
    out = _parse_workflow({"name": "W", "steps": [
        {"text": "first"}, {"text": ""}, {"order": "bad", "text": "third"}]})
    # empty-text step dropped; missing/invalid order falls back to enumeration index
    assert [s["order"] for s in out["steps"]] == [1, 3]
    assert [s["text"] for s in out["steps"]] == ["first", "third"]
