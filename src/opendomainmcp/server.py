"""MCP server exposing the domain knowledge base over stdio.

Tools: ingest_path, search_knowledge, get_stats. All share the same runtime
context (pipeline/store) as the CLI and web API.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from .context import build_context

mcp = FastMCP("opendomainmcp")
_contexts: dict = {}


def _context(collection: Optional[str] = None):
    key = collection or "__default__"
    if key not in _contexts:
        _contexts[key] = build_context(collection=collection)
    return _contexts[key]


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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
