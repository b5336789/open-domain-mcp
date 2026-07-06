"""Bottom-up level ordering for chain analysis.

Leaves (functions calling nothing internal) come first so their summaries
exist before any caller is analyzed. Cycles are condensed (iterative
Tarjan SCC) and their members share a level — within a cycle, analysis uses
whatever summaries are already available."""

from __future__ import annotations

from .models import CodeGraph


def bottom_up_levels(graph: CodeGraph) -> list[list[str]]:
    nodes = set(graph.functions)
    adj: dict[str, set[str]] = {n: set() for n in nodes}
    for e in graph.edges:
        if not e.external and e.src in nodes and e.dst in nodes and e.src != e.dst:
            adj[e.src].add(e.dst)

    scc_of, sccs = _tarjan(nodes, adj)
    # condensation: scc -> callee sccs
    cadj: dict[int, set[int]] = {i: set() for i in range(len(sccs))}
    for src, dsts in adj.items():
        for dst in dsts:
            if scc_of[src] != scc_of[dst]:
                cadj[scc_of[src]].add(scc_of[dst])

    # level = 1 + max(level of callee sccs); leaves = 0 (memoized DFS).
    # Tarjan emits SCCs in reverse topological order, so j < i for every
    # j in cadj[i]: driven by the ascending for-loop below, _level(j) is
    # always memoized before _level(i) runs — actual recursion depth is <= 1,
    # safe at any chain length. Do not call _level out of order.
    level: dict[int, int] = {}

    def _level(i: int) -> int:
        if i not in level:
            level[i] = 1 + max((_level(j) for j in cadj[i]), default=-1)
        return level[i]

    for i in range(len(sccs)):
        _level(i)

    depth = max(level.values(), default=-1) + 1
    out: list[list[str]] = [[] for _ in range(depth)]
    for i, members in enumerate(sccs):
        out[level[i]].extend(members)
    return [sorted(lvl) for lvl in out if lvl]


def _tarjan(nodes: set[str], adj: dict[str, set[str]]):
    """Iterative Tarjan; returns (scc index per node, list of SCC member lists)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    scc_of: dict[str, int] = {}
    sccs: list[list[str]] = []
    counter = 0

    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, iter(sorted(adj[root])))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(sorted(adj[nxt]))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                members = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    scc_of[member] = len(sccs)
                    members.append(member)
                    if member == node:
                        break
                sccs.append(sorted(members))
    return scc_of, sccs
