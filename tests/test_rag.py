import pytest

from opendomainmcp.config import Settings
from opendomainmcp.models import Chunk
from opendomainmcp.query import AnswerError, answer_question, answer_question_stream


def _seed(store):
    store.upsert([
        Chunk(text="Reciprocal Rank Fusion combines vector and BM25 rankings.",
              source="retrieval/lexical.py", kind="code", language="python", symbol="rrf_fuse"),
        Chunk(text="unrelated note about coffee brewing", source="notes.md", kind="text"),
    ])


def test_answer_uses_retrieved_sources(store):
    _seed(store)
    captured = {}

    def fake_synth(model, system, user):
        captured["model"] = model
        captured["user"] = user
        return "Hybrid search fuses dense and BM25 results with RRF [1]."

    result = answer_question(
        "how is hybrid search fused", store, Settings(), top_k=3, synthesize=fake_synth
    )
    assert "[1]" in result["answer"]
    assert captured["model"] == Settings().answer_model
    assert "rrf_fuse" in captured["user"]  # sources passed to the model
    assert result["citations"][0]["symbol"] == "rrf_fuse"
    assert result["citations"][0]["n"] == 1


def test_no_results_short_circuits(store):
    called = []
    result = answer_question(
        "anything", store, Settings(), synthesize=lambda *a: called.append(1) or "x"
    )
    assert result["citations"] == []
    assert not called  # synthesizer not invoked when nothing was retrieved


def test_answer_stream_yields_deltas_then_citations(store):
    _seed(store)

    def fake_stream(model, system, user):
        assert "rrf_fuse" in user  # retrieved sources passed to the model
        yield "Hybrid "
        yield "search [1]."

    events = list(answer_question_stream(
        "how is hybrid search fused", store, Settings(), top_k=3,
        synthesize_stream=fake_stream,
    ))
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == "Hybrid search [1]."
    # citations arrive once, as the final event
    assert events[-1]["type"] == "citations"
    cites = events[-1]["citations"]
    assert any(c["symbol"] == "rrf_fuse" for c in cites)


def test_answer_stream_no_results_short_circuits(store):
    called = []
    events = list(answer_question_stream(
        "anything", store, Settings(),
        synthesize_stream=lambda *a: called.append(1) or iter(()),
    ))
    assert not called  # streamer not invoked when nothing retrieved
    assert events[-1] == {"type": "citations", "citations": []}
    assert any(e["type"] == "delta" for e in events)


def test_missing_key_fails_loud(monkeypatch):
    import anthropic

    import opendomainmcp.query.rag as rag

    # Simulate a missing key / SDK failure: the default backend must wrap it.
    def boom(*args, **kwargs):
        raise RuntimeError("could not resolve authentication method")

    monkeypatch.setattr(anthropic, "Anthropic", boom)
    with pytest.raises(rag.AnswerError):
        rag._claude_synthesize("m", "system", "user")
