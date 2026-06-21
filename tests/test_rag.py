import pytest

from opendomainmcp.config import Settings
from opendomainmcp.models import Article, Chunk
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


class _ScoredStore:
    """Minimal store returning fixed-score hits (articles disabled in settings,
    so search_unified returns store.search verbatim)."""

    def __init__(self, results):
        self._results = results

    def search(self, query, top_k=5, where=None, mode="vector", source_contains=None):
        return self._results


def _floor_settings(min_score):
    return Settings(retrieve_include_articles=False, retrieve_min_score=min_score)


def test_relevance_floor_refuses_when_best_below_threshold():
    from opendomainmcp.models import SearchResult

    store = _ScoredStore([SearchResult(id="a", text="x", score=0.50,
                                       metadata={"source": "f.py"})])
    called = []
    result = answer_question("out of corpus", store, _floor_settings(0.65),
                             synthesize=lambda *a: called.append(1) or "fabricated")
    assert result["citations"] == []
    assert "No indexed content matched" in result["answer"]
    assert not called  # model never invoked -> cannot hallucinate


def test_relevance_floor_allows_when_best_above_threshold():
    from opendomainmcp.models import SearchResult

    store = _ScoredStore([
        SearchResult(id="a", text="x", score=0.72, metadata={"source": "f.py", "symbol": "foo"}),
        SearchResult(id="b", text="y", score=0.60, metadata={"source": "g.py"}),  # mid-score kept
    ])
    result = answer_question("in corpus", store, _floor_settings(0.65),
                             synthesize=lambda m, s, u: "grounded [1][2]")
    assert "[1]" in result["answer"]
    assert {c["id"] for c in result["citations"]} == {"a", "b"}  # gate is on max, not per-result


def test_relevance_floor_disabled_by_default_keeps_low_scores():
    from opendomainmcp.models import SearchResult

    store = _ScoredStore([SearchResult(id="a", text="x", score=0.10,
                                       metadata={"source": "f.py"})])
    result = answer_question("q", store, _floor_settings(0.0),  # disabled
                             synthesize=lambda *a: "answer [1]")
    assert result["citations"]  # not refused -> prior behavior intact


def test_relevance_floor_applies_to_stream():
    from opendomainmcp.models import SearchResult

    store = _ScoredStore([SearchResult(id="a", text="x", score=0.40,
                                       metadata={"source": "f.py"})])
    called = []
    events = list(answer_question_stream(
        "out of corpus", store, _floor_settings(0.65),
        synthesize_stream=lambda *a: called.append(1) or iter(()),
    ))
    assert not called
    assert events[-1] == {"type": "citations", "citations": []}
    assert any("No indexed content matched" in e.get("text", "") for e in events)


def test_missing_key_fails_loud(monkeypatch):
    import anthropic

    import opendomainmcp.query.rag as rag

    # Simulate a missing key / SDK failure: the default backend must wrap it.
    def boom(*args, **kwargs):
        raise RuntimeError("could not resolve authentication method")

    monkeypatch.setattr(anthropic, "Anthropic", boom)
    with pytest.raises(rag.AnswerError):
        rag._claude_synthesize("m", "system", "user")


def _arts(store):
    return store.sibling(f"{store.stats()['collection']}__articles")


def test_ask_includes_article_body_and_marks_citation_type(store):
    store.upsert([Chunk(text="approval needs a manager", source="r.md", kind="text",
                        start_line=1, end_line=1)])
    _arts(store).upsert([Article(
        title="Order Approval Rule", topic="order approval",
        body="Orders above $10k require manager sign-off.",
        source_chunk_ids=["a"], sources=["r.md:1"])])

    captured = {}

    def fake_synth(model, system, user):
        captured["user"] = user
        return "Per the rule [1]."

    # signature is answer_question(query, store, settings, top_k=..., synthesize=...)
    out = answer_question("when is approval needed?", store, Settings(),
                          top_k=5, synthesize=fake_synth)
    # the article body reached the LLM prompt
    assert "manager sign-off" in captured["user"]
    types = {c["type"] for c in out["citations"]}
    assert "article" in types
    assert "chunk" in types
    art_cite = next(c for c in out["citations"] if c["type"] == "article")
    assert art_cite["source"] == "Order Approval Rule"


def test_chunk_citation_source_is_bare_path_not_symbol_qualified():
    """_citations must return bare source path for code chunks, not 'source::symbol'.

    cli.py:_cmd_ask appends '::symbol' itself when rendering, so if _citations
    already returned 'source::symbol' the symbol would be printed twice.
    """
    from opendomainmcp.models import SearchResult
    from opendomainmcp.query.rag import _citations

    r = SearchResult(
        id="abc",
        text="def rrf_fuse(): ...",
        score=0.9,
        metadata={"kind": "code", "source": "f.py", "symbol": "foo"},
    )
    cites = _citations([r])
    assert len(cites) == 1
    c = cites[0]
    assert c["source"] == "f.py", f"expected bare path, got {c['source']!r}"
    assert c["symbol"] == "foo"
    assert c["type"] == "chunk"


def test_format_sources_handles_article_metadata_without_source_key():
    from opendomainmcp.models import SearchResult
    from opendomainmcp.query.rag import _citations, _format_sources
    r = SearchResult(id="x", text="body", score=0.5,
                     metadata={"kind": "article", "title": "T", "topic": "tp",
                               "sources": "f.py:1 | g.md:2"})
    block = _format_sources([r])
    assert "T" in block and "body" in block          # title used as the label
    cites = _citations([r])
    assert cites[0]["type"] == "article" and cites[0]["source"] == "T"
