"""Developer view symbol/node_type precision (task 3.4).

The developer code-lookup tools must distinguish a function from a class with the
same name region, returning only the chunk whose ``node_type`` matches the tool's
intent -- not any ``kind=code`` chunk.
"""

from types import SimpleNamespace

from opendomainmcp.config import Settings
from opendomainmcp.models import Chunk, KnowledgeUnit
from opendomainmcp.store import build_where
from opendomainmcp.views import VIEWS, run_view_tool


def _ctx(store, **settings):
    return SimpleNamespace(store=store, settings=Settings(**settings))


def _dev_tool(name):
    return next(t for t in VIEWS["developer"].tools if t.name == name)


def _seed_code(store):
    store.upsert([
        Chunk(text="class Exporter:\n    pass", source="exp.py", kind="code",
              language="python", node_type="class_definition", symbol="Exporter",
              knowledge=KnowledgeUnit(summary="exporter class",
                                      knowledge_type="Code")),
        Chunk(text="def Exporter():\n    return build()", source="fn.py",
              kind="code", language="python", node_type="function_definition",
              symbol="Exporter",
              knowledge=KnowledgeUnit(summary="exporter function",
                                      knowledge_type="Code")),
    ])


def test_build_where_supports_node_type():
    where = build_where({"node_type": "function_definition"})
    assert where == {"node_type": "function_definition"}


def test_build_where_combines_symbol_and_node_type():
    where = build_where({"symbol": "Exporter", "node_type": "class_definition"})
    assert where == {"$and": [{"symbol": "Exporter"},
                              {"node_type": "class_definition"}]}


def test_get_function_returns_only_function_node(store):
    _seed_code(store)
    ctx = _ctx(store)
    res = run_view_tool(ctx, _dev_tool("get_function"), "Exporter", top_k=5)
    assert res
    assert all(r["metadata"]["node_type"] == "function_definition" for r in res)
    assert all(r["metadata"]["symbol"] == "Exporter" for r in res)


def test_get_class_returns_only_class_node(store):
    _seed_code(store)
    ctx = _ctx(store)
    res = run_view_tool(ctx, _dev_tool("get_class"), "Exporter", top_k=5)
    assert res
    assert all(r["metadata"]["node_type"] == "class_definition" for r in res)
    assert all(r["metadata"]["symbol"] == "Exporter" for r in res)


def test_search_code_still_returns_any_code(store):
    """The unconstrained search_code tool keeps returning both node types."""
    _seed_code(store)
    ctx = _ctx(store)
    res = run_view_tool(ctx, _dev_tool("search_code"), "Exporter", top_k=5)
    node_types = {r["metadata"]["node_type"] for r in res}
    assert {"class_definition", "function_definition"} <= node_types
