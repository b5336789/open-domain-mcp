"""trace_dependency uses the dependency graph, falling back to search (task 4.5)."""

from types import SimpleNamespace

from opendomainmcp.config import Settings
from opendomainmcp.graph.deps import extract_dependencies
from opendomainmcp.models import Chunk, KnowledgeUnit
from opendomainmcp.views import VIEWS, run_view_tool


def _trace_tool():
    return next(t for t in VIEWS["developer"].tools if t.name == "trace_dependency")


def _ctx(store, graph=None, **settings):
    return SimpleNamespace(store=store, graph=graph, settings=Settings(**settings))


def _seed_graph(fake_graph, symbol="myapp", chunk_id="c1"):
    src = "import os\nfrom pkg import thing\n"
    entities, edges = extract_dependencies("python", src, symbol=symbol, chunk_id=chunk_id)
    fake_graph.upsert_entities(entities)
    fake_graph.upsert_edges(edges)


def test_returns_graph_neighbors_when_present(store, fake_graph):
    _seed_graph(fake_graph, symbol="myapp")
    ctx = _ctx(store, graph=fake_graph)

    res = run_view_tool(ctx, _trace_tool(), "myapp", top_k=5)

    assert res
    names = {r["metadata"]["name"] for r in res}
    assert {"os", "pkg"} <= names
    assert all(r["metadata"]["relation_type"] == "imports" for r in res)
    # Shape: JSON-serializable envelope consistent with other tools.
    for r in res:
        assert set(r) >= {"id", "text", "score", "metadata"}
        assert isinstance(r["score"], float)


def test_falls_back_to_search_when_symbol_absent(store, fake_graph):
    # Graph has no node named "ghost"; search must still find the seeded code.
    store.upsert([
        Chunk(text="import os\n\ndef ghost():\n    return os", source="g.py",
              kind="code", language="python", node_type="function_definition",
              symbol="ghost",
              knowledge=KnowledgeUnit(summary="ghost fn", knowledge_type="Code")),
    ])
    ctx = _ctx(store, graph=fake_graph)

    res = run_view_tool(ctx, _trace_tool(), "ghost", top_k=5)

    assert res
    # Fallback returns SearchResult envelopes whose metadata carries code fields.
    assert any(r["metadata"].get("kind") == "code" for r in res)


def test_falls_back_when_no_graph_on_ctx(store):
    store.upsert([
        Chunk(text="import os\n\ndef ghost():\n    return os", source="g.py",
              kind="code", language="python", node_type="function_definition",
              symbol="ghost",
              knowledge=KnowledgeUnit(summary="ghost fn", knowledge_type="Code")),
    ])
    ctx = _ctx(store, graph=None)

    res = run_view_tool(ctx, _trace_tool(), "ghost", top_k=5)

    assert res
    assert any(r["metadata"].get("kind") == "code" for r in res)
