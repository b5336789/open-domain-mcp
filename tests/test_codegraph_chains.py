"""Entry-point detection and chain assembly (spec 4A, task 8)."""

from opendomainmcp.codegraph.chains import assemble_chains, detect_entry_points
from opendomainmcp.codegraph.models import CodeGraph, FunctionDef, ResolvedEdge


def _fd(q, kind="function", route=None):
    return FunctionDef(qualified_name=q, file="F", start_line=1, end_line=2,
                       language="java", kind=kind, route=route)


def _edge(src, dst, relation="calls", external=False):
    return ResolvedEdge(src=src, dst=dst, relation=relation, confidence=1.0,
                        file="F", line=1, external=external)


def _graph():
    # endpoint -> a -> b -> (external db proc);  orphan has no callers
    fns = [_fd("api.Ctl.post", kind="endpoint", route=("POST", "/x")),
           _fd("svc.A.a"), _fd("svc.B.b"), _fd("svc.Orphan.run")]
    edges = [_edge("api.Ctl.post", "svc.A.a"),
             _edge("svc.A.a", "svc.B.b"),
             _edge("svc.B.b", "pkg.proc", relation="executes_sql", external=True)]
    return CodeGraph(functions={f.qualified_name: f for f in fns}, edges=edges)


def test_entry_points_endpoints_and_indegree_zero():
    eps = detect_entry_points(_graph())
    assert eps == ["api.Ctl.post", "svc.Orphan.run"]


def test_chain_members_edges_and_external_boundary():
    chains = {c.entry: c for c in assemble_chains(_graph())}
    main = chains["api.Ctl.post"]
    assert main.members == ["api.Ctl.post", "svc.A.a", "svc.B.b"]
    assert any(e.relation == "executes_sql" for e in main.edges)
    assert not main.truncated
    assert chains["svc.Orphan.run"].members == ["svc.Orphan.run"]


def test_cycle_truncates_but_terminates():
    fns = {q: _fd(q) for q in ("x.A.a", "x.B.b")}
    fns["x.A.a"] = _fd("x.A.a")
    g = CodeGraph(functions=fns,
                  edges=[_edge("x.A.a", "x.B.b"), _edge("x.B.b", "x.A.a")])
    chains = assemble_chains(g)
    entry = [c for c in chains if c.entry == "x.A.a"]
    # in-degree of both nodes is 1, so neither is an entry unless cycle-only
    # components are surfaced via their lexicographically-first node
    assert entry and entry[0].truncated
    assert entry[0].members == ["x.A.a", "x.B.b"]


def test_depth_limit_truncates():
    fns = {f"m.C{i}.f": _fd(f"m.C{i}.f") for i in range(5)}
    edges = [_edge(f"m.C{i}.f", f"m.C{i+1}.f") for i in range(4)]
    g = CodeGraph(functions=fns, edges=edges)
    chains = {c.entry: c for c in assemble_chains(g, max_depth=2)}
    c = chains["m.C0.f"]
    assert c.truncated and len(c.members) == 3  # entry + 2 levels
