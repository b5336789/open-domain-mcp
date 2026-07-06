"""Call-site resolution: RawSymbols -> CodeGraph.

Name-based with scope precedence (same scope > same package > import >
globally-unique basename). Unresolvable calls are never dropped — they become
low-confidence edges to an ``external`` node so coverage gaps stay visible.
Cross-language edges: db_call sites match PL/SQL defs (executes_sql);
http_call sites match endpoint route templates (http_call)."""

from __future__ import annotations

from .models import CallSite, CodeGraph, FunctionDef, RawSymbols, ResolvedEdge

CONF_SAME_SCOPE = 1.0
CONF_SAME_PACKAGE = 0.9
CONF_IMPORT = 0.8
CONF_UNIQUE = 0.6
CONF_EXTERNAL = 0.3
CONF_DB_KNOWN = 0.9
CONF_DB_UNKNOWN = 0.5
CONF_HTTP = 0.7


def resolve(per_file: list[RawSymbols]) -> CodeGraph:
    graph = CodeGraph()
    imports_by_file: dict[str, list[str]] = {}
    for syms in per_file:
        for fn in syms.functions:
            graph.functions[fn.qualified_name] = fn
        for fn in syms.functions:
            imports_by_file.setdefault(fn.file, syms.imports)

    by_basename: dict[str, list[str]] = {}
    for qname in graph.functions:
        base = qname.rsplit(".", 1)[-1].rsplit(":", 1)[-1].lower()
        by_basename.setdefault(base, []).append(qname)
    plsql_by_name = {q.lower(): q for q, f in graph.functions.items()
                     if f.language == "plsql"}
    endpoints = [f for f in graph.functions.values() if f.route is not None]

    edges: dict[tuple[str, str, str], ResolvedEdge] = {}

    def add(edge: ResolvedEdge):
        if edge.src == edge.dst:
            return
        key = (edge.src, edge.dst, edge.relation)
        if key not in edges or edges[key].confidence < edge.confidence:
            edges[key] = edge

    for syms in per_file:
        for call in syms.calls:
            if call.kind == "db_call":
                add(_resolve_db(call, plsql_by_name))
            elif call.kind == "http_call":
                add(_resolve_http(call, endpoints))
            else:
                add(_resolve_call(call, graph, by_basename,
                                  imports_by_file.get(call.file, [])))

    graph.edges = list(edges.values())
    return graph


def _prefix_of(qualified: str) -> str:
    if ":" in qualified:
        return qualified.split(":", 1)[0]
    return qualified.rsplit(".", 1)[0] if "." in qualified else ""


def _resolve_call(call: CallSite, graph: CodeGraph,
                  by_basename: dict[str, list[str]],
                  imports: list[str]) -> ResolvedEdge:
    callee = call.callee_text
    base = callee.rsplit(".", 1)[-1].lower()
    caller_prefix = _prefix_of(call.caller)
    sep = ":" if ":" in call.caller else "."

    # 1. same scope
    sibling = f"{caller_prefix}{sep}{callee}" if caller_prefix else callee
    if sibling in graph.functions:
        return _edge(call, sibling, CONF_SAME_SCOPE)
    sib_base = f"{caller_prefix}{sep}{base}" if caller_prefix else base
    if sib_base in graph.functions:
        return _edge(call, sib_base, CONF_SAME_SCOPE)

    # 2. same package (java/plsql): parent of the caller prefix
    if "." in caller_prefix:
        pkg = caller_prefix.rsplit(".", 1)[0]
        for candidate in by_basename.get(base, []):
            if candidate.startswith(pkg + "."):
                return _edge(call, candidate, CONF_SAME_PACKAGE)

    # 3. import-based: callee head matches an import's last segment
    if "." in callee:
        head = callee.split(".")[0]
        for imp in imports:
            if imp.rsplit(".", 1)[-1] == head:
                candidate = f"{imp}.{callee.split('.', 1)[1]}"
                if candidate in graph.functions:
                    return _edge(call, candidate, CONF_IMPORT)

    # 4. globally unique basename
    candidates = by_basename.get(base, [])
    if len(candidates) == 1:
        return _edge(call, candidates[0], CONF_UNIQUE)

    # 5. external
    return _edge(call, callee.lower(), CONF_EXTERNAL, external=True)


def _resolve_db(call: CallSite, plsql_by_name: dict[str, str]) -> ResolvedEdge:
    proc = call.detail.lower()
    target = plsql_by_name.get(proc)
    if target is None and "." not in proc:
        # standalone procedure referenced without a package prefix
        matches = [q for k, q in plsql_by_name.items() if k == proc]
        target = matches[0] if matches else None
    if target is not None:
        return _edge(call, target, CONF_DB_KNOWN, relation="executes_sql")
    return _edge(call, proc, CONF_DB_UNKNOWN, relation="executes_sql", external=True)


def _resolve_http(call: CallSite, endpoints: list[FunctionDef]) -> ResolvedEdge:
    method, _, path = call.detail.partition(" ")
    for ep in endpoints:
        ep_method, ep_path = ep.route
        if ep_method not in ("ANY", method.upper()):
            continue
        if _paths_match(path, ep_path):
            return _edge(call, ep.qualified_name, CONF_HTTP, relation="http_call")
    return _edge(call, f"http:{call.detail.lower()}", CONF_EXTERNAL,
                 relation="http_call", external=True)


def _paths_match(actual: str, template: str) -> bool:
    a = [s for s in actual.split("/") if s]
    t = [s for s in template.split("/") if s]
    if len(a) != len(t):
        return False
    for sa, st in zip(a, t):
        wild = (st.startswith("{") and st.endswith("}")) or \
               (sa.startswith("{") and sa.endswith("}"))
        if not wild and sa != st:
            return False
    return True


def _edge(call: CallSite, dst: str, confidence: float,
          relation: str = "calls", external: bool = False) -> ResolvedEdge:
    return ResolvedEdge(src=call.caller, dst=dst, relation=relation,
                        confidence=confidence, file=call.file, line=call.line,
                        external=external)
