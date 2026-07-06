"""End-to-end codegraph build over a mixed-language corpus (spec 4A, task 7)."""

from opendomainmcp.codegraph.build import build_codegraph, persist_codegraph
from opendomainmcp.config import Settings

JAVA = """
package com.acme.billing;

public class BillingService {
    public void charge(Order order) {
        validate(order);
    }
    private void validate(Order order) {
        CallableStatement cs = conn.prepareCall("{call PKG_BILLING.VALIDATE_AMOUNT(?)}");
    }
}
"""
PLSQL = """
CREATE OR REPLACE PACKAGE BODY pkg_billing AS
  PROCEDURE validate_amount(p_amt IN NUMBER) IS
  BEGIN
    NULL;
  END validate_amount;
END pkg_billing;
"""


def _corpus(root):
    (root / "BillingService.java").write_text(JAVA)
    (root / "pkg_billing.pkb").write_text(PLSQL)
    (root / "test_ignored.java").write_text("x")           # excluded name? no — java
    (root / "vendor").mkdir()
    (root / "vendor" / "Skip.java").write_text(JAVA)       # excluded by filter


def test_build_walks_extracts_and_resolves(tmp_path):
    _corpus(tmp_path)
    graph = build_codegraph(tmp_path, Settings())
    assert "com.acme.billing.BillingService.charge" in graph.functions
    assert "pkg_billing.validate_amount" in graph.functions
    # vendor/ excluded by the shared ingest filter
    assert not any(f.file.endswith("Skip.java") for f in graph.functions.values())
    rels = {(e.src, e.dst, e.relation) for e in graph.edges}
    assert ("com.acme.billing.BillingService.charge",
            "com.acme.billing.BillingService.validate", "calls") in rels
    assert ("com.acme.billing.BillingService.validate",
            "pkg_billing.validate_amount", "executes_sql") in rels


def test_persist_writes_entities_edges_and_provenance(tmp_path, fake_graph):
    _corpus(tmp_path)
    graph = build_codegraph(tmp_path, Settings())
    stats = persist_codegraph(graph, fake_graph)
    assert stats["functions"] == len(graph.functions)
    assert stats["edges"] == len(graph.edges)

    ent = fake_graph.get_entity("com.acme.billing.billingservice.charge")
    assert ent and ent["type"] == "function"

    fn = fake_graph.get_function("com.acme.billing.BillingService.validate")
    assert fn["file"].endswith("BillingService.java")
    assert fn["start_line"] > 0 and fn["end_line"] >= fn["start_line"]
    assert fn["language"] == "java"

    nb = fake_graph.neighbors("com.acme.billing.billingservice.validate")
    rels = {(n["relation_type"], n["direction"]) for n in nb["neighbors"]}
    assert ("executes_sql", "out") in rels


def test_get_function_missing_returns_none(fake_graph):
    assert fake_graph.get_function("nope.nothing") is None
