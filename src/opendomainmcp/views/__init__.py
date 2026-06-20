"""Declarative MCP view definitions.

A "view" is a role-specific MCP server (Product / Operations / Developer /
Support / Architecture). Each view exposes a handful of typed tools, but every
tool is just a filtered search over the *same* knowledge store -- so we describe
them as data (tool name + filters) and let ``server.build_view_server`` turn each
entry into a real MCP tool. This keeps the 20+ tools as thin wrappers rather than
hand-written functions.

``filters`` keys map onto :func:`opendomainmcp.store.build_where` (``kind``,
``language``, ``knowledge_type`` ...). The special key ``audience`` is not a
Chroma filter (a chunk may serve several audiences and is stored as a joined
string); it is post-filtered after retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Tree-sitter names a declaration differently per language (e.g. Python
# ``function_definition`` vs JS ``function_declaration``), so a "functions" or
# "classes" lookup must accept every variant. These sets mirror the definition
# types produced by ``ingest.code_splitter`` and are matched against a chunk's
# ``node_type`` metadata.
_FUNCTION_NODE_TYPES = (
    "function_definition", "function_declaration", "function_item",
    "method_definition", "method_declaration", "constructor_declaration",
)
_CLASS_NODE_TYPES = (
    "class_definition", "class_declaration", "class_specifier",
    "struct_specifier", "struct_item", "enum_item", "enum_declaration",
    "interface_declaration", "trait_item", "type_alias_declaration",
)


@dataclass(frozen=True)
class ViewTool:
    name: str
    description: str
    filters: dict = field(default_factory=dict)
    default_top_k: int = 5
    # Acceptable ``node_type`` values for a code lookup. When set, results whose
    # ``node_type`` is outside the set are dropped, so e.g. ``get_function`` only
    # returns function/method definitions rather than any ``kind=code`` chunk.
    node_types: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class ViewSpec:
    name: str
    title: str
    purpose: str
    tools: tuple[ViewTool, ...]


VIEWS: dict[str, ViewSpec] = {
    "product": ViewSpec(
        name="product",
        title="Product MCP",
        purpose="Product usage understanding",
        tools=(
            ViewTool("get_feature", "Find product features matching the query.",
                     {"knowledge_type": "Feature"}),
            ViewTool("get_workflow", "Find product workflows or usage flows.",
                     {"knowledge_type": "Workflow"}),
            ViewTool("get_constraint", "Find product constraints and limitations.",
                     {"knowledge_type": "Constraint"}),
            ViewTool("search_product_knowledge",
                     "Search all product-manager-facing knowledge.",
                     {"audience": "product_manager"}),
        ),
    ),
    "operations": ViewSpec(
        name="operations",
        title="Operations MCP",
        purpose="Execution guidance",
        tools=(
            ViewTool("get_runbook", "Find runbooks for an operational task.",
                     {"knowledge_type": "Runbook"}),
            ViewTool("get_troubleshooting", "Find troubleshooting guidance.",
                     {"knowledge_type": "Troubleshooting"}),
            ViewTool("get_incident_response",
                     "Find incident-response procedures.",
                     {"knowledge_type": "Runbook", "audience": "operations"}),
            ViewTool("get_rollback_procedure",
                     "Find rollback or recovery procedures.",
                     {"knowledge_type": "Runbook"}),
        ),
    ),
    "developer": ViewSpec(
        name="developer",
        title="Developer MCP",
        purpose="Code understanding",
        tools=(
            ViewTool("search_code", "Search source code by intent or symbol.",
                     {"kind": "code"}),
            ViewTool("get_class", "Find a class or type definition.",
                     {"kind": "code"}, node_types=_CLASS_NODE_TYPES),
            ViewTool("get_function", "Find a function or method definition.",
                     {"kind": "code"}, node_types=_FUNCTION_NODE_TYPES),
            ViewTool("trace_dependency",
                     "Find code and relations describing dependencies.",
                     {"kind": "code"}),
            ViewTool("get_api_implementation",
                     "Find the code or spec behind an API.",
                     {"knowledge_type": "API"}),
        ),
    ),
    "support": ViewSpec(
        name="support",
        title="Support MCP",
        purpose="Customer support",
        tools=(
            ViewTool("get_known_issue", "Find known issues and errors.",
                     {"knowledge_type": "Error"}),
            ViewTool("get_error_explanation", "Explain an error or message.",
                     {"knowledge_type": "Error"}),
            ViewTool("get_resolution_steps",
                     "Find resolution and troubleshooting steps.",
                     {"knowledge_type": "Troubleshooting"}),
            ViewTool("search_faq", "Search frequently asked questions.",
                     {"knowledge_type": "FAQ"}),
        ),
    ),
    "architecture": ViewSpec(
        name="architecture",
        title="Architecture MCP",
        purpose="System understanding",
        tools=(
            ViewTool("get_component", "Find architectural components.",
                     {"knowledge_type": "Architecture"}),
            ViewTool("get_dependency",
                     "Find dependencies between components.",
                     {"knowledge_type": "Architecture"}),
            ViewTool("get_dataflow", "Find data flows through the system.",
                     {"knowledge_type": "Architecture"}),
            ViewTool("search_architecture",
                     "Search all architect-facing knowledge.",
                     {"audience": "solutions_architect"}),
        ),
    ),
}

VIEW_NAMES = tuple(VIEWS.keys())


def run_view_tool(ctx, tool: ViewTool, query: str, top_k: int) -> list[dict]:
    """Execute a view tool: a filtered search over the shared store.

    ``audience`` filters are applied client-side (see module docstring). When the
    ``retrieve_approved_only`` setting is on, results are restricted to approved
    knowledge; chunks predating the review fields have no ``review_status`` and
    are intentionally excluded only when that setting is enabled.
    """
    from ..store import build_where

    # trace_dependency prefers the code dependency graph (task 4.5): when the
    # query names a module/symbol with import edges, return its neighbors;
    # otherwise fall back to the plain kind=code search so behaviour never
    # regresses.
    if tool.name == "trace_dependency":
        graph_hits = _trace_dependency(ctx, query, top_k)
        if graph_hits:
            return graph_hits

    filters = dict(tool.filters)
    audience = filters.pop("audience", None)
    if getattr(ctx.settings, "retrieve_approved_only", False):
        filters["review_status"] = "approved"
    where = build_where(filters)
    # Over-fetch when we still have to post-filter (by audience or node_type).
    post_filtered = bool(audience) or bool(tool.node_types)
    fetch_k = top_k * 3 if post_filtered else top_k
    results = ctx.store.search(
        query, top_k=fetch_k, where=where, mode=ctx.settings.search_mode
    )
    if tool.node_types:
        allowed = set(tool.node_types)
        results = [r for r in results if r.metadata.get("node_type") in allowed]
    if audience:
        results = [
            r for r in results
            if audience in (r.metadata.get("audience") or "").split(", ")
        ]
    return [r.to_dict() for r in results[:top_k]]


def _trace_dependency(ctx, query: str, top_k: int) -> list[dict]:
    """Return import-dependency neighbors of ``query`` from the graph (task 4.5).

    Shapes each neighbor into the ``{id, text, score, metadata}`` envelope the
    other view tools return. Returns ``[]`` when the graph is unavailable or the
    query names nothing (caller then falls back to search)."""
    graph = getattr(ctx, "graph", None)
    if graph is None:
        return []
    from ..graph.deps import IMPORTS_RELATION

    result = graph.neighbors(query, relation_type=IMPORTS_RELATION, depth=1)
    neighbors = result.get("neighbors") if result else None
    if not neighbors:
        return []

    root = result.get("entity") or {}
    root_name = root.get("name") or query
    out: list[dict] = []
    for n in neighbors[:top_k]:
        ent = n.get("entity") or {}
        name = ent.get("name") or ent.get("normalized_name") or ""
        direction = n.get("direction", "out")
        verb = "imports" if direction == "out" else "imported by"
        out.append({
            "id": ent.get("normalized_name") or name,
            "text": f"{root_name} {verb} {name}",
            "score": float(ent.get("confidence") or 1.0),
            "metadata": {
                "name": name,
                "type": ent.get("type") or "module",
                "relation_type": n.get("relation_type", IMPORTS_RELATION),
                "direction": direction,
                "source_symbol": root_name,
                "chunk_ids": ent.get("chunk_ids", []),
            },
        })
    return out
