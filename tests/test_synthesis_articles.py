# tests/test_synthesis_articles.py — uses the conftest `store` fixture
from opendomainmcp.config import Settings
from opendomainmcp.models import Chunk, KnowledgeUnit
from opendomainmcp.synthesis import synthesize_articles


class _Writer:
    def write(self, topic, evidence):
        return {"title": f"About {topic}", "body": f"{topic} explained [1]",
                "business_relevance": 0.9}


class _Critic:
    def __init__(self, keep): self._keep = keep
    def judge(self, topic, body, evidence):
        return {"grounded": self._keep, "business_meaningful": self._keep, "note": ""}


def _seed(store):
    # One concept present in BOTH a code and a doc chunk → cross-validated topic.
    ku = KnowledgeUnit(summary="billing", concepts=["Billing Engine"],
                       knowledge_type="Feature")
    store.upsert([
        Chunk(text="def charge(): ...", source="billing.py", kind="code",
              start_line=1, end_line=2, knowledge=ku),
        Chunk(text="The billing engine charges orders.", source="billing.md",
              kind="text", start_line=1, end_line=1, knowledge=ku),
    ])


def _arts(store):
    return store.sibling(f"{store.stats()['collection']}__articles")


def test_synthesize_stores_only_critic_approved_articles(store):
    _seed(store)
    report = synthesize_articles(store, Settings(), writer=_Writer(),
                                 critic=_Critic(keep=True))
    assert report.topics_gated >= 1
    assert report.stored == report.articles_written >= 1
    assert _arts(store).stats()["count"] == report.stored


def test_synthesize_rejects_when_critic_fails(store):
    _seed(store)
    report = synthesize_articles(store, Settings(), writer=_Writer(),
                                 critic=_Critic(keep=False))
    assert report.stored == 0
    assert len(report.rejected) >= 1
    assert _arts(store).stats()["count"] == 0


def test_synthesize_is_idempotent(store):
    _seed(store)
    synthesize_articles(store, Settings(), writer=_Writer(), critic=_Critic(keep=True))
    synthesize_articles(store, Settings(), writer=_Writer(), critic=_Critic(keep=True))
    # Same topic + same member chunks → same Article id → no duplicate row.
    assert _arts(store).stats()["count"] == 1


def test_empty_evidence_is_recorded_not_dropped(store, monkeypatch):
    # Seed so topics are gated, then force search to return nothing.
    _seed(store)
    monkeypatch.setattr(store, "search", lambda *a, **k: [])
    report = synthesize_articles(store, Settings(), writer=_Writer(),
                                 critic=_Critic(keep=True))
    assert report.stored == 0
    assert any(
        entry.get("verdict", {}).get("note") == "no evidence retrieved"
        for entry in report.rejected
    ), "gated topic with no evidence must appear in report.rejected"


def test_dry_run_counts_stored_but_does_not_persist(store):
    _seed(store)
    report = synthesize_articles(store, Settings(), writer=_Writer(),
                                 critic=_Critic(keep=True), dry_run=True)
    assert report.stored >= 1
    assert _arts(store).stats()["count"] == 0, \
        "dry_run must not write to the sibling article collection"


def test_cross_validated_comes_from_gate_not_evidence(store, monkeypatch):
    # Seed so "billing engine" appears in BOTH a code and a doc chunk
    # → TopicCandidate.cross_validated is True (the gate truth).
    _seed(store)

    # Capture the real search so we can find the code chunk's id.
    real_search = store.search
    code_results = [r for r in real_search("Billing Engine", top_k=8, mode="hybrid")
                    if r.metadata.get("kind") == "code"]
    assert code_results, "precondition: at least one code result must exist"

    # Monkeypatch search to return ONLY the code-side result.
    # An evidence-derived cross_validated would be False (no doc hit).
    monkeypatch.setattr(store, "search", lambda *a, **k: code_results[:1])

    report = synthesize_articles(store, Settings(), writer=_Writer(),
                                 critic=_Critic(keep=True))
    assert report.stored >= 1, "article should have been stored"

    # Read back the stored article and check its cross_validated metadata.
    arts = _arts(store)
    stored_items = arts.get_items(limit=10)
    assert stored_items, "article store must have at least one item"
    cv_values = [item["metadata"].get("cross_validated") for item in stored_items]
    assert any(cv_values), \
        "cross_validated must be True (from the gate), not False (from code-only evidence)"
