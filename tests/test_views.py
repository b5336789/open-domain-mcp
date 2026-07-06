from types import SimpleNamespace

from opendomainmcp.config import Settings
from opendomainmcp.models import Chunk, KnowledgeUnit
from opendomainmcp.server import build_view_server, get_server, mcp
from opendomainmcp.views import VIEWS, run_view_tool


def _ctx(store, **settings):
    return SimpleNamespace(store=store, settings=Settings(**settings))


def _seed(store):
    store.upsert([
        Chunk(text="users can export reports to PDF", source="a.md", kind="text",
              knowledge=KnowledgeUnit(summary="export", knowledge_type="Feature",
                                      audience=["product_manager"])),
        Chunk(text="restart the worker pool when the queue stalls", source="b.md",
              kind="text", knowledge=KnowledgeUnit(summary="restart",
                                                   knowledge_type="Runbook",
                                                   audience=["operations"])),
        Chunk(text="def export_pdf(report): return render(report)", source="c.py",
              kind="code", language="python", symbol="export_pdf",
              knowledge=KnowledgeUnit(summary="pdf", knowledge_type="Code",
                                      audience=["engineering"])),
    ])


def test_every_view_builds_with_expected_tools():
    for name, spec in VIEWS.items():
        server = build_view_server(name)
        names = {t.name for t in server._tool_manager.list_tools()}
        assert {tool.name for tool in spec.tools} <= names


def test_get_server_defaults_to_generic():
    assert get_server("generic") is mcp


def test_view_tool_filters_by_knowledge_type(store):
    _seed(store)
    ctx = _ctx(store)
    tool = next(t for t in VIEWS["product"].tools if t.name == "get_feature")
    res = run_view_tool(ctx, tool, "export restart pdf", top_k=5)
    assert res and all(r["metadata"].get("knowledge_type") == "Feature" for r in res)


def test_view_tool_filters_by_audience(store):
    _seed(store)
    ctx = _ctx(store)
    tool = next(t for t in VIEWS["product"].tools
                if t.name == "search_product_knowledge")
    res = run_view_tool(ctx, tool, "export restart pdf", top_k=5)
    assert res and all(
        "product_manager" in r["metadata"].get("audience", "") for r in res
    )


def test_view_tool_developer_returns_code(store):
    _seed(store)
    ctx = _ctx(store)
    tool = next(t for t in VIEWS["developer"].tools if t.name == "search_code")
    res = run_view_tool(ctx, tool, "export_pdf render", top_k=5)
    assert res and all(r["metadata"].get("kind") == "code" for r in res)


def test_view_tool_result_lifts_evidence(store):
    import json

    ev = [{"claim": "feature X is safe", "quote": "safe to use", "source": "a.md",
           "start_line": 1, "end_line": 1, "verified": True}]
    store.upsert([
        Chunk(text="users can export reports to PDF with evidence", source="a.md",
              kind="text",
              knowledge=KnowledgeUnit(
                  summary="export feature", knowledge_type="Feature",
                  audience=["product_manager"],
                  evidence=ev,
              )),
    ])
    ctx = _ctx(store)
    tool = next(t for t in VIEWS["product"].tools if t.name == "get_feature")
    res = run_view_tool(ctx, tool, "export PDF evidence", top_k=5)
    assert res
    result_with_ev = next((r for r in res if "evidence" in r), None)
    assert result_with_ev is not None, "no result had lifted evidence"
    assert result_with_ev["evidence"] == ev


def test_approved_only_policy_excludes_unreviewed(store):
    store.upsert([
        Chunk(text="approved feature text", source="a.md", kind="text",
              knowledge=KnowledgeUnit(summary="a", knowledge_type="Feature",
                                      review_status="approved")),
        Chunk(text="pending feature text", source="b.md", kind="text",
              knowledge=KnowledgeUnit(summary="b", knowledge_type="Feature",
                                      review_status="pending")),
    ])
    tool = next(t for t in VIEWS["product"].tools if t.name == "get_feature")
    ctx = _ctx(store, retrieve_approved_only=True)
    res = run_view_tool(ctx, tool, "feature text", top_k=5)
    assert res and all(r["metadata"].get("review_status") == "approved" for r in res)
