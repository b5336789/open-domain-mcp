import pytest

from opendomainmcp.publish.decisions import (
    PublishDecisionStore,
    PublishGateError,
    build_decision,
    require_publish_override,
)


def _evidence(status="ready", score=91):
    return {
        "collection": "default",
        "status": status,
        "score": score,
        "next_action": "Publish evidence is ready.",
        "evidence": [
            {
                "id": "review",
                "gate": "Review",
                "status": status,
                "score": score,
                "summary": "Review gate summary.",
                "details": ["detail"],
                "action": "action",
            }
        ],
    }


def test_decision_store_persists_latest_by_collection_and_view(tmp_path):
    store = PublishDecisionStore(tmp_path)
    decision = build_decision(
        collection="default",
        view="product",
        action="publish",
        endpoint_url="http://testserver/mcp/product",
        evidence=_evidence(),
    )

    store.append(decision)
    reloaded = PublishDecisionStore(tmp_path)

    assert reloaded.latest("default", "product")["id"] == decision["id"]
    assert reloaded.history("default", "product")[0]["action"] == "publish"


def test_ready_publish_does_not_require_override():
    require_publish_override(_evidence("ready"), "")


def test_non_ready_publish_requires_override_reason():
    with pytest.raises(PublishGateError, match="override reason"):
        require_publish_override(_evidence("needs_review"), "")


def test_non_ready_publish_accepts_override_reason():
    require_publish_override(
        _evidence("needs_review"), "Business owner accepted the risk."
    )


def test_build_decision_captures_gate_snapshot():
    decision = build_decision(
        collection="default",
        view="operations",
        action="publish",
        endpoint_url="http://testserver/mcp/operations",
        evidence=_evidence("validating", 58),
        override_reason="Temporary internal validation.",
    )

    assert decision["status"] == "published"
    assert decision["readiness_status"] == "validating"
    assert decision["readiness_score"] == 58
    assert decision["override_reason"] == "Temporary internal validation."
    assert decision["gates"][0]["gate"] == "Review"
