"""OpenAI-compatible LLM backend (e.g. a local LM Studio server).

The app's default LLM backend speaks the Anthropic Messages API. These tests
cover the alternative ``llm_backend="openai"`` path, which talks the OpenAI
chat-completions API so any OpenAI-compatible endpoint (LM Studio, vLLM, etc.)
can drive extraction and RAG answering. The HTTP boundary is faked.
"""

import pytest

from opendomainmcp.config import Settings
from opendomainmcp.models import Chunk


# --- fake OpenAI client ----------------------------------------------------

def _fake_openai(content="", cap=None, stream_parts=None):
    cap = cap if cap is not None else {}

    class Completions:
        def create(self, **kwargs):
            cap.update(kwargs)
            if kwargs.get("stream"):
                def gen():
                    for p in stream_parts or []:
                        delta = type("D", (), {"content": p})
                        choice = type("C", (), {"delta": delta})
                        yield type("Chunk", (), {"choices": [choice]})
                return gen()
            msg = type("M", (), {"content": content})
            choice = type("Ch", (), {"message": msg})
            return type("Resp", (), {"choices": [choice]})

    class Chat:
        completions = Completions()

    return type("Client", (), {"chat": Chat()})()


# --- config ----------------------------------------------------------------

def test_llm_backend_defaults_to_anthropic():
    assert Settings().llm_backend == "anthropic"


def test_llm_backend_is_settable():
    assert Settings(llm_backend="openai").llm_backend == "openai"


# --- extraction ------------------------------------------------------------

def test_openai_extractor_parses_chat_completion():
    from opendomainmcp.extract.knowledge import OpenAIExtractor

    cap = {}
    raw = ('{"summary": "does a thing", "concepts": ["alpha"], "audience": [],'
           ' "knowledge_type": "API"}')
    ext = OpenAIExtractor("qwen3-coder", client=_fake_openai(raw, cap))
    k = ext.extract("def f(): pass", kind="code", language="python")

    assert k.summary == "does a thing"
    assert k.concepts == ["alpha"]
    assert cap["model"] == "qwen3-coder"
    roles = [m["role"] for m in cap["messages"]]
    assert roles == ["system", "user"]
    assert "python" in cap["messages"][1]["content"]  # snippet label reaches the model


def test_openai_extractor_requests_structured_output_when_enabled():
    from opendomainmcp.extract.knowledge import (
        KNOWLEDGE_RESPONSE_FORMAT, OpenAIExtractor)

    cap = {}
    ext = OpenAIExtractor("qwen3-coder", client=_fake_openai('{"summary": "s"}', cap),
                          structured=True)
    ext.extract("x", kind="text")
    # structured output constrains generation to valid JSON at the source
    assert cap["response_format"] == KNOWLEDGE_RESPONSE_FORMAT
    assert cap["response_format"]["type"] == "json_schema"


def test_openai_extractor_no_structured_output_by_default():
    from opendomainmcp.extract.knowledge import OpenAIExtractor

    cap = {}
    ext = OpenAIExtractor("qwen3-coder", client=_fake_openai('{"summary": "s"}', cap))
    ext.extract("x", kind="text")
    assert "response_format" not in cap  # default off (constrained decoding is slow locally)


def test_openai_extractor_falls_back_when_structured_unsupported():
    from opendomainmcp.extract.knowledge import OpenAIExtractor

    calls = []

    class _BadRequest(Exception):
        status_code = 400

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if "response_format" in kwargs:
                raise _BadRequest("'response_format' unsupported")
            msg = type("M", (), {"content": '{"summary": "ok", "concepts": []}'})
            return type("Resp", (), {"choices": [type("Ch", (), {"message": msg})]})

    client = type("Client", (), {"chat": type("Chat", (), {"completions": Completions()})()})()
    ext = OpenAIExtractor("m", client=client, structured=True)

    k = ext.extract("x", kind="text")
    assert k.summary == "ok"
    assert len(calls) == 2  # structured attempt, then plain fallback
    assert "response_format" in calls[0] and "response_format" not in calls[1]

    # subsequent calls skip the doomed structured attempt (flag latched off)
    calls.clear()
    ext.extract("y", kind="text")
    assert len(calls) == 1 and "response_format" not in calls[0]


def test_get_extractor_selects_openai_backend(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "lm-studio")
    from opendomainmcp.extract.knowledge import OpenAIExtractor, get_extractor

    ext = get_extractor(Settings(extract_knowledge=True, llm_backend="openai"))
    assert isinstance(ext, OpenAIExtractor)


def test_get_extractor_selects_anthropic_backend(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    from opendomainmcp.extract.knowledge import ClaudeExtractor, get_extractor

    ext = get_extractor(Settings(extract_knowledge=True))  # default backend
    assert isinstance(ext, ClaudeExtractor)


def test_get_extractor_disabled_is_null_regardless_of_backend():
    from opendomainmcp.extract.knowledge import NullExtractor, get_extractor

    ext = get_extractor(Settings(extract_knowledge=False, llm_backend="openai"))
    assert isinstance(ext, NullExtractor)


# --- RAG answering ---------------------------------------------------------

def test_openai_synthesize_returns_text(monkeypatch):
    import openai

    import opendomainmcp.query.rag as rag

    cap = {}
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: _fake_openai("Answer [1].", cap))
    out = rag._openai_synthesize("qwen3-coder", "system", "user with rrf_fuse")

    assert out == "Answer [1]."
    assert cap["model"] == "qwen3-coder"
    roles = [m["role"] for m in cap["messages"]]
    assert roles == ["system", "user"]


def test_openai_synthesize_fails_loud(monkeypatch):
    import openai

    import opendomainmcp.query.rag as rag

    def boom(**kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(openai, "OpenAI", boom)
    with pytest.raises(rag.AnswerError):
        rag._openai_synthesize("qwen3-coder", "system", "user")


def test_openai_synthesize_stream_yields_deltas(monkeypatch):
    import openai

    import opendomainmcp.query.rag as rag

    monkeypatch.setattr(
        openai, "OpenAI",
        lambda **kw: _fake_openai(stream_parts=["Hy", "brid ", "[1]"]),
    )
    out = "".join(rag._openai_synthesize_stream("qwen3-coder", "system", "user"))
    assert out == "Hybrid [1]"


def _seed(store):
    store.upsert([
        Chunk(text="Reciprocal Rank Fusion combines vector and BM25 rankings.",
              source="retrieval/lexical.py", kind="code", language="python",
              symbol="rrf_fuse"),
    ])


def test_answer_question_routes_to_openai_when_backend_openai(monkeypatch, store):
    import opendomainmcp.query.rag as rag
    from opendomainmcp.query import answer_question

    _seed(store)
    monkeypatch.setattr(rag, "_openai_synthesize", lambda *a, **k: "FROM_OPENAI [1]")
    monkeypatch.setattr(rag, "_claude_synthesize", lambda *a, **k: "FROM_CLAUDE [1]")

    res = answer_question("how is hybrid search fused", store,
                          Settings(llm_backend="openai"), top_k=2)
    assert res["answer"] == "FROM_OPENAI [1]"


def test_answer_question_routes_to_anthropic_by_default(monkeypatch, store):
    import opendomainmcp.query.rag as rag
    from opendomainmcp.query import answer_question

    _seed(store)
    monkeypatch.setattr(rag, "_openai_synthesize", lambda *a, **k: "FROM_OPENAI [1]")
    monkeypatch.setattr(rag, "_claude_synthesize", lambda *a, **k: "FROM_CLAUDE [1]")

    res = answer_question("how is hybrid search fused", store, Settings(), top_k=2)
    assert res["answer"] == "FROM_CLAUDE [1]"


def test_answer_question_stream_routes_to_openai(monkeypatch, store):
    import opendomainmcp.query.rag as rag
    from opendomainmcp.query import answer_question_stream

    _seed(store)
    monkeypatch.setattr(rag, "_openai_synthesize_stream",
                        lambda *a, **k: iter(["FROM ", "OPENAI ", "[1]"]))
    monkeypatch.setattr(rag, "_claude_synthesize_stream",
                        lambda *a, **k: iter(["FROM_CLAUDE"]))

    events = list(answer_question_stream("how is hybrid search fused", store,
                                         Settings(llm_backend="openai"), top_k=2))
    deltas = "".join(e["text"] for e in events if e["type"] == "delta")
    assert deltas == "FROM OPENAI [1]"
