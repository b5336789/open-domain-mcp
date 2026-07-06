"""Oracle PL/SQL lightweight parser (spec 4A, task 5)."""

from opendomainmcp.codegraph.plsql import extract_plsql

PKG = """
CREATE OR REPLACE PACKAGE BODY pkg_billing AS

  PROCEDURE validate_amount(p_amt IN NUMBER) IS
  BEGIN
    IF p_amt < 0 THEN
      RAISE_APPLICATION_ERROR(-20001, 'negative amount');
    END IF;
    log_util.write('validated');
  END validate_amount;

  FUNCTION compute_total(p_id IN NUMBER) RETURN NUMBER IS
    v_total NUMBER;
  BEGIN
    validate_amount(v_total);
    RETURN v_total;
  END compute_total;

END pkg_billing;
"""


def test_package_procedures_qualified_lowercase():
    syms = extract_plsql(PKG, "pkg_billing.pkb")
    names = {f.qualified_name: f for f in syms.functions}
    assert set(names) == {"pkg_billing.validate_amount", "pkg_billing.compute_total"}
    v = names["pkg_billing.validate_amount"]
    assert v.kind == "procedure" and v.language == "plsql"
    assert v.start_line == 3 and v.end_line >= 9


def test_call_sites_within_bodies():
    syms = extract_plsql(PKG, "pkg_billing.pkb")
    calls = {(c.caller, c.callee_text) for c in syms.calls}
    assert ("pkg_billing.compute_total", "validate_amount") in calls
    assert ("pkg_billing.validate_amount", "log_util.write") in calls
    # keywords are not calls
    assert not any(c.callee_text.lower() in ("if", "raise_application_error")
                   for c in syms.calls)


def test_standalone_procedure():
    src = "CREATE OR REPLACE PROCEDURE billing_report AS\nBEGIN\n  pkg_billing.compute_total(1);\nEND;\n"
    syms = extract_plsql(src, "report.sql")
    assert [f.qualified_name for f in syms.functions] == ["billing_report"]
    assert ("billing_report", "pkg_billing.compute_total") in {
        (c.caller, c.callee_text) for c in syms.calls}
