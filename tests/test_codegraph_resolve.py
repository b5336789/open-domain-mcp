"""Call resolution and cross-language edges (spec 4A, task 6).
Uses synthetic RawSymbols — independent of the language extractors."""

from opendomainmcp.codegraph.models import CallSite, FunctionDef, RawSymbols
from opendomainmcp.codegraph.resolve import (
    CONF_DB_KNOWN, CONF_EXTERNAL, CONF_HTTP, CONF_SAME_SCOPE, CONF_UNIQUE, resolve,
)


def _fd(qname, language="java", kind="function", route=None, file="F"):
    return FunctionDef(qualified_name=qname, file=file, start_line=1,
                       end_line=2, language=language, kind=kind, route=route)


def test_same_scope_resolution_wins():
    syms = RawSymbols(
        functions=[_fd("a.B.charge"), _fd("a.B.validate")],
        calls=[CallSite(caller="a.B.charge", callee_text="validate",
                        file="F", line=5)])
    g = resolve([syms])
    e = g.edges[0]
    assert (e.src, e.dst, e.relation) == ("a.B.charge", "a.B.validate", "calls")
    assert e.confidence == CONF_SAME_SCOPE and not e.external


def test_unique_global_basename():
    syms1 = RawSymbols(functions=[_fd("a.B.charge")],
                       calls=[CallSite(caller="a.B.charge",
                                       callee_text="write_audit", file="F", line=2)])
    syms2 = RawSymbols(functions=[_fd("x.Y.write_audit")])
    g = resolve([syms1, syms2])
    e = next(e for e in g.edges if e.dst == "x.Y.write_audit")
    assert e.confidence == CONF_UNIQUE


def test_unresolved_becomes_low_confidence_external():
    syms = RawSymbols(functions=[_fd("a.B.charge")],
                      calls=[CallSite(caller="a.B.charge",
                                      callee_text="Mystery.thing", file="F", line=2)])
    g = resolve([syms])
    e = g.edges[0]
    assert e.external and e.confidence == CONF_EXTERNAL
    assert e.dst == "mystery.thing"


def test_db_call_links_to_plsql_procedure():
    java = RawSymbols(
        functions=[_fd("a.B.validate")],
        calls=[CallSite(caller="a.B.validate", callee_text="pkg_billing.validate_amount",
                        file="F", line=3, kind="db_call",
                        detail="pkg_billing.validate_amount")])
    plsql = RawSymbols(functions=[_fd("pkg_billing.validate_amount",
                                      language="plsql", kind="procedure")])
    g = resolve([java, plsql])
    e = next(e for e in g.edges if e.relation == "executes_sql")
    assert e.dst == "pkg_billing.validate_amount"
    assert e.confidence == CONF_DB_KNOWN and not e.external


def test_http_call_matches_route_template():
    js = RawSymbols(
        functions=[_fd("src/client.js:fetchOrders", language="javascript")],
        calls=[CallSite(caller="src/client.js:fetchOrders", callee_text="fetch",
                        file="src/client.js", line=4, kind="http_call",
                        detail="GET /api/billing/orders/{param}")])
    java = RawSymbols(functions=[_fd("a.B.getOrders", kind="endpoint",
                                     route=("GET", "/api/billing/orders/{customerId}"))])
    g = resolve([js, java])
    e = next(e for e in g.edges if e.relation == "http_call")
    assert e.dst == "a.B.getOrders" and e.confidence == CONF_HTTP


def test_unmatched_http_call_is_external_and_self_calls_dropped():
    js = RawSymbols(
        functions=[_fd("c.js:f", language="javascript")],
        calls=[
            CallSite(caller="c.js:f", callee_text="fetch", file="c.js", line=1,
                     kind="http_call", detail="POST /nope"),
            CallSite(caller="c.js:f", callee_text="f", file="c.js", line=2),
        ])
    g = resolve([js])
    assert all(e.src != e.dst for e in g.edges)
    http = next(e for e in g.edges if e.relation == "http_call")
    assert http.external and http.dst == "http:post /nope"
