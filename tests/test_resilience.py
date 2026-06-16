"""Timeout and retry resilience for external calls (Anthropic) and the store."""

from types import SimpleNamespace

import pytest

from opendomainmcp.config import Settings
from opendomainmcp.models import Chunk


def _fake_anthropic(captured, raw):
    """Return a fake anthropic.Anthropic that records constructor kwargs and
    yields a single text block of ``raw``."""

    class FakeMessages:
        def create(self, **kw):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=raw)])

    class FakeClient:
        def __init__(self, **kw):
            captured.update(kw)
            self.messages = FakeMessages()

    return FakeClient


def test_extractor_passes_timeout_and_retries(monkeypatch):
    import anthropic

    captured: dict = {}
    monkeypatch.setattr(
        anthropic, "Anthropic",
        _fake_anthropic(captured, '{"summary": "s", "concepts": ["c"], "relations": []}'),
    )

    from opendomainmcp.extract.knowledge import get_extractor

    ext = get_extractor(Settings(request_timeout=12.5, max_retries=4))
    unit = ext.extract("hello world", "text")
    assert captured["timeout"] == 12.5
    assert captured["max_retries"] == 4
    assert unit.summary == "s"


def test_rag_passes_timeout_and_retries(monkeypatch, store):
    import anthropic

    captured: dict = {}
    monkeypatch.setattr(anthropic, "Anthropic", _fake_anthropic(captured, "Answer [1]."))

    store.upsert([Chunk(text="vector database stores embeddings", source="a.md", kind="text")])

    from opendomainmcp.query import answer_question

    out = answer_question(
        "embeddings", store, Settings(request_timeout=7.0, max_retries=3), top_k=1
    )
    assert captured["timeout"] == 7.0
    assert captured["max_retries"] == 3
    assert "[1]" in out["answer"]


def test_store_retries_transient_then_succeeds(store, monkeypatch):
    import opendomainmcp.store.chroma_store as cs

    monkeypatch.setattr(cs.time, "sleep", lambda *_: None)
    store._max_retries = 2
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return "ok"

    assert store._retry("test", flaky) == "ok"
    assert calls["n"] == 2


def test_store_retry_exhausts_and_raises(store, monkeypatch):
    import opendomainmcp.store.chroma_store as cs

    monkeypatch.setattr(cs.time, "sleep", lambda *_: None)
    store._max_retries = 1

    def always_fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        store._retry("test", always_fail)


def test_store_no_retry_by_default(store):
    # The fixture store uses the default (max_retries=0): a failure is immediate.
    calls = {"n": 0}

    def fail_once():
        calls["n"] += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        store._retry("test", fail_once)
    assert calls["n"] == 1
