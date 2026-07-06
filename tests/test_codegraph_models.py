"""Codegraph data model + shared DB-call scanner (spec 4A, task 1)."""

from opendomainmcp.codegraph.models import (
    CallSite, CodeGraph, FunctionDef, RawSymbols, ResolvedEdge, scan_db_calls,
)


def test_functiondef_defaults():
    f = FunctionDef(qualified_name="pkg.Cls.m", file="A.java",
                    start_line=3, end_line=9, language="java")
    assert f.kind == "function" and f.route is None and not f.exported


def test_dataclasses_compose_into_codegraph():
    f = FunctionDef("a.B.c", "B.java", 1, 5, "java")
    e = ResolvedEdge(src="a.B.c", dst="x.Y.z", relation="calls",
                     confidence=0.9, file="B.java", line=2)
    g = CodeGraph(functions={f.qualified_name: f}, edges=[e])
    assert g.functions["a.B.c"].end_line == 5
    assert g.edges[0].relation == "calls"
    assert RawSymbols().functions == [] and CallSite(
        caller="a.B.c", callee_text="x", file="B.java", line=2).kind == "call"


def test_scan_db_calls_jdbc_forms():
    src = '''
    CallableStatement cs = conn.prepareCall("{call PKG_BILLING.VALIDATE_AMOUNT(?, ?)}");
    var s2 = conn.prepareCall("{?= call pkg_util.compute(?)}");
    '''
    assert scan_db_calls(src) == ["pkg_billing.validate_amount", "pkg_util.compute"]


def test_scan_db_calls_exec_and_begin_forms():
    src = '''
    cmd.CommandText = "BEGIN pkg_orders.close_order(:id); END;"
    other.CommandText = "exec billing_report"
    '''
    assert scan_db_calls(src) == ["pkg_orders.close_order", "billing_report"]


def test_scan_db_calls_dedup_and_no_false_positives():
    src = '''
    a = conn.prepareCall("{call P.X}");
    b = conn.prepareCall("{call P.X}");
    plain = "select id from orders";
    '''
    assert scan_db_calls(src) == ["p.x"]


def test_scan_db_calls_document_order_across_forms():
    src = 'cmd.CommandText = "exec proc_a"\ncs = conn.prepareCall("{call PROC_B}");'
    assert scan_db_calls(src) == ["proc_a", "proc_b"]
