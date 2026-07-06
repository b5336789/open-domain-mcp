"""Bottom-up (leaves-first) level ordering for chain analysis (plan 4B)."""

from opendomainmcp.codegraph.models import CodeGraph, FunctionDef, ResolvedEdge
from opendomainmcp.codegraph.order import bottom_up_levels


def _fd(q):
    return FunctionDef(qualified_name=q, file="F", start_line=1, end_line=2,
                       language="java")


def _edge(src, dst, external=False):
    return ResolvedEdge(src=src, dst=dst, relation="calls", confidence=1.0,
                        file="F", line=1, external=external)


def _graph(names, edges):
    return CodeGraph(functions={n: _fd(n) for n in names}, edges=edges)


def test_linear_chain_is_leaves_first():
    g = _graph(["a", "b", "c"], [_edge("a", "b"), _edge("b", "c")])
    assert bottom_up_levels(g) == [["c"], ["b"], ["a"]]


def test_diamond_shares_levels():
    g = _graph(["a", "b", "c", "d"],
               [_edge("a", "b"), _edge("a", "c"), _edge("b", "d"), _edge("c", "d")])
    assert bottom_up_levels(g) == [["d"], ["b", "c"], ["a"]]


def test_cycle_members_share_a_level():
    g = _graph(["a", "b", "c"],
               [_edge("a", "b"), _edge("b", "a"), _edge("a", "c")])
    levels = bottom_up_levels(g)
    assert levels[0] == ["c"]
    assert sorted(levels[1]) == ["a", "b"]


def test_external_edges_ignored():
    g = _graph(["a"], [_edge("a", "ext.proc", external=True)])
    assert bottom_up_levels(g) == [["a"]]
