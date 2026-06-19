"""MCP server exposing the domain knowledge base over stdio.

Two shapes share the same runtime context (pipeline/store) as the CLI and web:

* the default **generic** server (``ingest_path``, ``search_knowledge``, ``ask``,
  ``get_stats``, ``list_collections``); and
* role-specific **views** (Product / Operations / Developer / Support /
  Architecture) whose typed tools are generated from :data:`views.VIEWS`.

Select with ``--view NAME`` or ``ODM_MCP_VIEW=NAME`` (default ``generic``).
"""

from __future__ import annotations

import argparse
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .context import build_context
from .views import VIEW_NAMES, VIEWS, run_view_tool

mcp = FastMCP("opendomainmcp")
_contexts: dict = {}


def _context(collection: Optional[str] = None):
    key = collection or "__default__"
    if key not in _contexts:
        _contexts[key] = build_context(collection=collection)
    return _contexts[key]


# -- generic server -----------------------------------------------------------
@mcp.tool()
def ingest_path(path: str, sync: bool = False, collection: Optional[str] = None) -> dict:
    """Ingest a file or directory: extract domain knowledge and index it.

    With ``sync=True`` on a directory, chunks for files deleted under it are
    pruned. ``collection`` selects the knowledge base. Returns indexed/pruned
    counts plus any skipped files or errors.
    """
    return _context(collection).pipeline.ingest_path(path, sync=sync).to_dict()


@mcp.tool()
def search_knowledge(
    query: str,
    top_k: int = 5,
    kind: Optional[str] = None,
    language: Optional[str] = None,
    symbol: Optional[str] = None,
    collection: Optional[str] = None,
) -> list[dict]:
    """Search the knowledge base (hybrid dense + BM25 by default).

    Optional filters: ``kind`` ('code'/'text'), ``language``, exact ``symbol``.
    ``collection`` selects the knowledge base.
    """
    from .store import build_where

    ctx = _context(collection)
    where = build_where({"kind": kind, "language": language, "symbol": symbol})
    results = ctx.store.search(query, top_k=top_k, where=where, mode=ctx.settings.search_mode)
    return [r.to_dict() for r in results]


@mcp.tool()
def ask(query: str, top_k: int = 6, collection: Optional[str] = None) -> dict:
    """Answer a question from the indexed knowledge, with inline [n] citations.

    Requires an Anthropic API key; returns ``{"error": ...}`` if unavailable.
    """
    from .query import AnswerError, answer_question

    ctx = _context(collection)
    try:
        return answer_question(query, ctx.store, ctx.settings, top_k=top_k)
    except AnswerError as exc:
        return {"error": str(exc)}


@mcp.tool()
def get_stats(collection: Optional[str] = None) -> dict:
    """Return collection statistics (document count, embedder, dimension)."""
    return _context(collection).store.stats()


@mcp.tool()
def list_collections() -> list[dict]:
    """List available knowledge bases (collections) with their chunk counts."""
    return _context().store.list_collections()


# -- graph query tools (pure reads over the graph store; no LLM) -------------
def graph_tool_handlers(ctx):
    """Return name -> callable for the graph MCP tools. Pure reads over the
    graph store; no retrieval / LLM."""

    def get_entity(name: str):
        return ctx.graph.neighbors(name)

    def list_related_entities(name: str, relation_type: str | None = None, depth: int = 1):
        return ctx.graph.neighbors(name, relation_type=relation_type, depth=depth)

    def get_workflow_steps(name: str):
        result = ctx.graph.get_workflow(name)
        return result if result is not None else {"workflow_name": name,
                                                  "prerequisites": [], "steps": []}

    def list_workflows(q: str | None = None, limit: int = 50):
        return ctx.graph.list_workflows(q=q, limit=limit)

    return {"get_entity": get_entity, "list_related_entities": list_related_entities,
            "get_workflow_steps": get_workflow_steps, "list_workflows": list_workflows}


# -- role-specific views ------------------------------------------------------
def build_view_server(view_name: str) -> FastMCP:
    """Build an MCP server exposing one view's typed tools.

    Each tool is generated from its :class:`views.ViewTool` spec and runs a
    filtered search over the shared store via :func:`views.run_view_tool`.
    """
    spec = VIEWS[view_name]
    server = FastMCP(f"opendomainmcp-{view_name}")

    def _make(tool):
        def fn(query: str, top_k: int = tool.default_top_k,
               collection: Optional[str] = None) -> list[dict]:
            return run_view_tool(_context(collection), tool, query, top_k)
        fn.__name__ = tool.name
        fn.__doc__ = tool.description
        return fn

    for tool in spec.tools:
        server.add_tool(_make(tool), name=tool.name, description=tool.description)

    # Register graph query tools on Developer and Architecture views.
    if view_name in ("developer", "architecture"):
        def _get_entity(name: str, collection: Optional[str] = None) -> dict:
            """Fetch an entity and its direct neighbors from the knowledge graph."""
            return graph_tool_handlers(_context(collection))["get_entity"](name=name)

        def _list_related_entities(name: str, relation_type: Optional[str] = None,
                                   depth: int = 1,
                                   collection: Optional[str] = None) -> dict:
            """List entities related to a given entity (depth clamped to 1–2)."""
            return graph_tool_handlers(_context(collection))["list_related_entities"](
                name=name, relation_type=relation_type, depth=depth)

        server.add_tool(_get_entity, name="get_entity",
                        description="Fetch an entity and its direct neighbors from the knowledge graph.")
        server.add_tool(_list_related_entities, name="list_related_entities",
                        description="List entities related to a given entity (depth clamped to 1–2).")

    # Register workflow tools on Operations view (list + get_steps) and
    # Product view (get_steps only).
    if view_name in ("operations", "product"):
        def _get_workflow_steps(name: str, collection: Optional[str] = None) -> dict:
            """Fetch a workflow's ordered steps and prerequisites. Returns an empty
            shape when the workflow is not found."""
            return graph_tool_handlers(_context(collection))["get_workflow_steps"](name=name)

        server.add_tool(_get_workflow_steps, name="get_workflow_steps",
                        description="Fetch a workflow's ordered steps and prerequisites.")

    if view_name in ("operations",):
        def _list_workflows(q: Optional[str] = None, limit: int = 50,
                            collection: Optional[str] = None) -> list:
            """List available workflows, optionally filtered by a search string."""
            return graph_tool_handlers(_context(collection))["list_workflows"](q=q, limit=limit)

        server.add_tool(_list_workflows, name="list_workflows",
                        description="List available workflows, optionally filtered by a search string.")

    return server


def get_server(view: str) -> FastMCP:
    """Return the generic server or a named view server."""
    if view in ("generic", "", None):
        return mcp
    if view not in VIEWS:
        raise SystemExit(
            f"unknown view {view!r}; choose from: generic, " + ", ".join(VIEW_NAMES)
        )
    return build_view_server(view)


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenDomainMCP MCP server")
    parser.add_argument(
        "--view", default=os.environ.get("ODM_MCP_VIEW", "generic"),
        choices=("generic", *VIEW_NAMES),
        help="Which MCP surface to serve (default: generic).",
    )
    args = parser.parse_args()
    get_server(args.view).run()


if __name__ == "__main__":
    main()
