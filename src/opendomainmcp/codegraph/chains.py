"""Entry-point detection and call-chain assembly.

An entry point is an endpoint (has a route) or an internal function nobody
internal calls (in-degree 0). Cycle-only components would otherwise be
unreachable, so each such component is surfaced through its
lexicographically-first member. Chains follow internal edges depth-first;
external boundary edges (db procs we don't have source for, unmatched http
calls) are recorded on the chain but never traversed."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import CodeGraph, ResolvedEdge


@dataclass
class Chain:
    entry: str
    members: list[str] = field(default_factory=list)
    edges: list[ResolvedEdge] = field(default_factory=list)
    truncated: bool = False


def detect_entry_points(graph: CodeGraph) -> list[str]:
    internal = {q for q in graph.functions}
    indegree = {q: 0 for q in internal}
    for e in graph.edges:
        if not e.external and e.dst in indegree and e.src in internal:
            indegree[e.dst] += 1
    entries = {q for q, f in graph.functions.items() if f.route is not None}
    entries |= {q for q, d in indegree.items() if d == 0}
    # cycle-only components: nodes not reachable from any current entry
    out = _out_edges(graph)
    reachable = _reachable(graph, entries, out)
    leftover = sorted(internal - reachable)
    while leftover:
        entries.add(leftover[0])
        reachable = _reachable(graph, entries, out)
        leftover = sorted(internal - reachable)
    return sorted(entries)


def _out_edges(graph: CodeGraph) -> dict[str, list[ResolvedEdge]]:
    out: dict[str, list[ResolvedEdge]] = {}
    for e in graph.edges:
        out.setdefault(e.src, []).append(e)
    return out


def _reachable(graph: CodeGraph, roots: set[str],
               out: dict[str, list[ResolvedEdge]]) -> set[str]:
    seen = set(roots)
    stack = list(roots)
    while stack:
        node = stack.pop()
        for e in out.get(node, []):
            if not e.external and e.dst in graph.functions and e.dst not in seen:
                seen.add(e.dst)
                stack.append(e.dst)
    return seen


def assemble_chains(graph: CodeGraph, max_depth: int = 12) -> list[Chain]:
    out = _out_edges(graph)
    chains = []
    for entry in detect_entry_points(graph):
        chain = Chain(entry=entry, members=[entry])
        _dfs(entry, out, graph, chain, visited={entry},
             path=[entry], depth=0, max_depth=max_depth)
        chains.append(chain)
    return chains


def _dfs(node: str, out, graph: CodeGraph, chain: Chain,
         visited: set[str], path: list[str], depth: int, max_depth: int):
    for e in sorted(out.get(node, []), key=lambda e: (e.dst, e.relation)):
        if e.external or e.dst not in graph.functions:
            chain.edges.append(e)          # boundary edge, not traversed
            continue
        if e.dst in path:
            chain.truncated = True         # cycle back-edge
            continue
        if depth + 1 > max_depth:
            chain.truncated = True
            continue
        chain.edges.append(e)
        if e.dst in visited:
            continue  # distinct edge recorded; subtree already walked
        visited.add(e.dst)
        chain.members.append(e.dst)        # DFS preorder, entry first
        _dfs(e.dst, out, graph, chain, visited,
             path + [e.dst], depth + 1, max_depth)
