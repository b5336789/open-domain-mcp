"""Business-logic tests for the offline grounding eval harness.

We feed the harness two synthetic systems over the same eval set: a *grounded*
one that retrieves the expected sources and answers with their substrings, and a
*hallucinating* one that retrieves the wrong files and fabricates an answer. The
metrics must rank the grounded system strictly higher.
"""

from pathlib import Path

import pytest

from opendomainmcp.evals import EvalCase, load_evalset, run_evals
from opendomainmcp.models import SearchResult

EVALSET = Path(__file__).resolve().parents[1] / "src" / "opendomainmcp" / "evals" / "sample_evalset.jsonl"


# --- fake systems -----------------------------------------------------------

# Ground truth keyed by query: the source the grounded retriever returns and the
# answer the grounded synthesizer produces.
_TRUTH = {
    "how is hybrid search fused": (
        SearchResult(id="1", text="...", score=0.9,
                     metadata={"source": "retrieval/lexical.py", "symbol": "rrf_fuse"}),
        "Hybrid search fuses dense and BM25 rankings with RRF [1].",
    ),
    "what does answer_question cite": (
        SearchResult(id="2", text="...", score=0.8,
                     metadata={"source": "query/rag.py", "symbol": "answer_question"}),
        "answer_question cites the numbered sources inline as [n].",
    ),
    "how is a chunk id derived": (
        SearchResult(id="3", text="...", score=0.7,
                     metadata={"source": "models.py", "symbol": "content_hash"}),
        "The chunk id is a sha256 digest of source, lines, and text.",
    ),
}


def good_retrieve(query):
    return [_TRUTH[query][0]]


def good_ask(query):
    src = _TRUTH[query][0]
    return {
        "answer": _TRUTH[query][1],
        "citations": [{"n": 1, "source": src.metadata["source"], "symbol": src.metadata["symbol"]}],
    }


def hallucinating_retrieve(query):
    # Always retrieves an irrelevant source -> no expected substring present.
    return [SearchResult(id="x", text="...", score=0.1,
                         metadata={"source": "notes/coffee.md"})]


def hallucinating_ask(query):
    return {
        "answer": "I'm confident the answer is unrelated trivia about coffee brewing.",
        "citations": [{"n": 1, "source": "notes/coffee.md", "symbol": None}],
    }


# --- tests ------------------------------------------------------------------

def test_loader_parses_sample_evalset():
    cases = load_evalset(EVALSET)
    assert len(cases) == 3
    assert all(isinstance(c, EvalCase) for c in cases)
    first = cases[0]
    assert first.id == "hybrid-fusion"
    assert "retrieval/lexical.py::rrf_fuse" in first.expected_sources


def test_grounded_system_scores_perfectly():
    cases = load_evalset(EVALSET)
    report = run_evals(cases, retrieve=good_retrieve, ask=good_ask)
    assert report.retrieval_hit_rate == 1.0
    assert report.answer_grounding_rate == 1.0
    assert all(c.retrieval_hit for c in report.cases)
    assert all(c.answer_grounded for c in report.cases)
    assert all(not c.missing_sources and not c.missing_answer for c in report.cases)


def test_hallucinating_system_scores_zero():
    cases = load_evalset(EVALSET)
    report = run_evals(cases, retrieve=hallucinating_retrieve, ask=hallucinating_ask)
    assert report.retrieval_hit_rate == 0.0
    assert report.answer_grounding_rate == 0.0


def test_metrics_rank_grounded_above_hallucinating():
    cases = load_evalset(EVALSET)
    good = run_evals(cases, retrieve=good_retrieve, ask=good_ask)
    bad = run_evals(cases, retrieve=hallucinating_retrieve, ask=hallucinating_ask)
    assert good.retrieval_hit_rate > bad.retrieval_hit_rate
    assert good.answer_grounding_rate > bad.answer_grounding_rate


def test_source_identifier_matches_path_or_symbol():
    # A case can target the bare path even when retrieval returns path::symbol.
    case = EvalCase(id="path-only", query="q", expected_sources=["query/rag.py"])
    report = run_evals([case], retrieve=lambda q: [
        SearchResult(id="2", text="...", score=0.8,
                     metadata={"source": "query/rag.py", "symbol": "answer_question"})
    ])
    assert report.retrieval_hit_rate == 1.0


def test_ask_citations_used_when_no_retriever():
    cases = load_evalset(EVALSET)
    # ask-only harness: retrieval scored from the answer's citations.
    report = run_evals(cases, ask=good_ask)
    assert report.retrieval_hit_rate == 1.0
    assert report.answer_grounding_rate == 1.0


def test_partial_grounding_is_not_a_pass():
    case = EvalCase(id="partial", query="q", expected_answer=["RRF", "BM25"])
    # Answer mentions only one of the two required substrings.
    report = run_evals([case], ask=lambda q: {"answer": "uses RRF only", "citations": []})
    assert report.answer_grounding_rate == 0.0
    assert report.cases[0].missing_answer == ["BM25"]


def test_requires_a_callable():
    with pytest.raises(ValueError):
        run_evals([EvalCase(id="x", query="q")])
