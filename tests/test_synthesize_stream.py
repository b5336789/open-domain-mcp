"""Web synthesize trigger (Feature 2): on_event progress + SSE endpoint.

The article-synthesis core emits a progress event per stage so the web UI can
stream a live log; the /api/synthesize/stream endpoint mirrors ingest_stream and
serializes runs with a process lock (concurrent requests get 409).
"""

import json

import pytest
from fastapi.testclient import TestClient

from opendomainmcp.api.app import create_app
from opendomainmcp.config import Settings
from opendomainmcp.context import Context
from opendomainmcp.models import Chunk, KnowledgeUnit
from opendomainmcp.synthesis import synthesize_articles
from opendomainmcp.synthesis.articles import SynthesisReport


class _Writer:
    def write(self, topic, evidence):
        return {"title": f"About {topic}", "body": f"{topic} explained [1]",
                "business_relevance": 0.9}


class _Critic:
    def __init__(self, keep=True):
        self._keep = keep

    def judge(self, topic, body, evidence):
        return {"grounded": self._keep, "business_meaningful": self._keep, "note": ""}


def _seed(store):
    ku = KnowledgeUnit(summary="billing", concepts=["Billing Engine"],
                       knowledge_type="Feature")
    store.upsert([
        Chunk(text="def charge(): ...", source="billing.py", kind="code",
              start_line=1, end_line=2, knowledge=ku),
        Chunk(text="The billing engine charges orders.", source="billing.md",
              kind="text", start_line=1, end_line=1, knowledge=ku),
    ])


# --- report serialization --------------------------------------------------

def test_report_to_dict_is_json_serializable():
    rep = SynthesisReport(topics_gated=2, stored=1,
                          rejected=[{"topic": "x"}], errors=[])
    d = rep.to_dict()
    assert d["topics_gated"] == 2 and d["stored"] == 1
    json.dumps(d)  # must not raise


# --- on_event progress emission --------------------------------------------

def test_synthesize_emits_progress_events(store):
    _seed(store)
    events = []
    synthesize_articles(store, Settings(), writer=_Writer(), critic=_Critic(True),
                        on_event=events.append)
    stages = [e["stage"] for e in events]
    assert "start" in stages
    assert "stored" in stages
    start = next(e for e in events if e["stage"] == "start")
    assert start["total"] >= 1
    stored = next(e for e in events if e["stage"] == "stored")
    assert "topic" in stored and "title" in stored


def test_synthesize_emits_rejected_on_critic_fail(store):
    _seed(store)
    events = []
    synthesize_articles(store, Settings(), writer=_Writer(), critic=_Critic(False),
                        on_event=events.append)
    assert any(e["stage"] == "rejected" for e in events)


def test_synthesize_without_on_event_is_unaffected(store):
    _seed(store)
    report = synthesize_articles(store, Settings(), writer=_Writer(),
                                 critic=_Critic(True))  # no on_event
    assert report.stored >= 1


# --- SSE endpoint ----------------------------------------------------------

@pytest.fixture
def client(store, pipeline, fake_graph, tmp_path):
    settings = Settings(data_dir=tmp_path)
    ctx = Context(settings=settings, store=store, pipeline=pipeline, graph=fake_graph)
    app = create_app(context=ctx, context_factory=lambda: ctx)
    return TestClient(app), ctx


def _events(resp):
    return [json.loads(line[len("data:"):].strip())
            for line in resp.text.splitlines() if line.startswith("data:")]


def test_synthesize_stream_endpoint(client, monkeypatch):
    tc, ctx = client
    _seed(ctx.store)
    import opendomainmcp.synthesis.articles as art
    monkeypatch.setattr(art, "get_article_llms",
                        lambda s: (_Writer(), _Critic(True)))

    resp = tc.get("/api/synthesize/stream")
    assert resp.status_code == 200
    events = _events(resp)
    stages = [e["stage"] for e in events]
    assert "start" in stages
    report = next(e for e in events if e["stage"] == "report")
    assert report["stored"] >= 1


def test_synthesize_stream_rejects_concurrent_run(client):
    tc, _ = client
    from opendomainmcp.api import app as appmod

    assert appmod._synthesize_lock.acquire(blocking=False)
    try:
        resp = tc.get("/api/synthesize/stream")
        assert resp.status_code == 409
    finally:
        appmod._synthesize_lock.release()
