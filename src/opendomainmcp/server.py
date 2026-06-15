"""MCP server exposing the domain knowledge base over stdio.

Tools: ingest_path, search_knowledge, get_stats. All share the same runtime
context (pipeline/store) as the CLI and web API.
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from .context import build_context

mcp = FastMCP("opendomainmcp")
_ctx = None


def _context():
    global _ctx
    if _ctx is None:
        _ctx = build_context()
    return _ctx


@mcp.tool()
def ingest_path(path: str) -> dict:
    """Ingest a file or directory: extract domain knowledge and index it.

    Returns counts of indexed files/chunks plus any skipped files or errors.
    """
    return _context().pipeline.ingest_path(path).to_dict()


@mcp.tool()
def search_knowledge(query: str, top_k: int = 5, kind: Optional[str] = None) -> list[dict]:
    """Search the knowledge base. ``kind`` may be 'code' or 'text' to filter."""
    where = {"kind": kind} if kind else None
    results = _context().store.search(query, top_k=top_k, where=where)
    return [r.to_dict() for r in results]


@mcp.tool()
def get_stats() -> dict:
    """Return collection statistics (document count, embedder, dimension)."""
    return _context().store.stats()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
