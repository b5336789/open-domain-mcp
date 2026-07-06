"""Java symbol extraction via tree-sitter (spec 4A, task 2)."""

from opendomainmcp.codegraph.java import extract_java

BILLING = """
package com.acme.billing;

import com.acme.repo.OrderRepo;

@RequestMapping("/api/billing")
public class BillingController {

    private OrderRepo repo;

    @PostMapping("/charge")
    public Receipt charge(Order order) {
        validate(order);
        return repo.save(order);
    }

    private void validate(Order order) {
        CallableStatement cs = conn.prepareCall("{call PKG_BILLING.VALIDATE_AMOUNT(?)}");
    }
}
"""


def test_functions_with_qualified_names_and_lines():
    syms = extract_java(BILLING, "Billing.java")
    names = {f.qualified_name: f for f in syms.functions}
    assert "com.acme.billing.BillingController.charge" in names
    assert "com.acme.billing.BillingController.validate" in names
    charge = names["com.acme.billing.BillingController.charge"]
    assert charge.file == "Billing.java" and charge.language == "java"
    assert charge.start_line > 1 and charge.end_line > charge.start_line


def test_route_annotation_makes_endpoint_with_class_prefix():
    syms = extract_java(BILLING, "Billing.java")
    charge = next(f for f in syms.functions if f.qualified_name.endswith(".charge"))
    assert charge.kind == "endpoint"
    assert charge.route == ("POST", "/api/billing/charge")


def test_call_sites_and_db_calls():
    syms = extract_java(BILLING, "Billing.java")
    plain = {(c.caller.rsplit(".", 1)[1], c.callee_text)
             for c in syms.calls if c.kind == "call"}
    assert ("charge", "validate") in plain
    assert ("charge", "repo.save") in plain
    db = [c for c in syms.calls if c.kind == "db_call"]
    assert db and db[0].detail == "pkg_billing.validate_amount"
    assert db[0].caller.endswith(".validate")


def test_imports_collected():
    syms = extract_java(BILLING, "Billing.java")
    assert "com.acme.repo.OrderRepo" in syms.imports


def test_comment_with_sql_keyword_produces_no_db_calls():
    """A comment containing SQL keywords must NOT generate db_call CallSites
    (4A final-review fix 2a)."""
    source = """
public class Scheduler {
    public void runNightly() {
        // execute nightly batch then call vendor api
        doWork();
    }
}
"""
    syms = extract_java(source, "Scheduler.java")
    db = [c for c in syms.calls if c.kind == "db_call"]
    assert db == [], f"phantom db_calls from comment: {db}"


def test_prepare_call_in_string_still_produces_db_call():
    """prepareCall with a stored-proc string must still yield a db_call
    (4A final-review fix 2b — regression guard)."""
    syms = extract_java(BILLING, "Billing.java")
    db = [c for c in syms.calls if c.kind == "db_call"]
    assert db, "expected at least one db_call from prepareCall fixture"
    assert db[0].detail == "pkg_billing.validate_amount"
    assert db[0].caller.endswith(".validate")


def test_no_package_and_plain_method():
    syms = extract_java(
        "public class Util { static int add(int a, int b) { return a + b; } }",
        "Util.java")
    assert [f.qualified_name for f in syms.functions] == ["Util.add"]
