"""End-to-end chain analysis pass over a mixed corpus with fake LLM (plan 4B)."""

import json

from opendomainmcp.codegraph.analyze import analyze_corpus
from opendomainmcp.codegraph.analyze_llm import ChainAnalyzer
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
PLSQL = """CREATE OR REPLACE PACKAGE BODY pkg_billing AS
  PROCEDURE validate_amount(p_amt IN NUMBER) IS
  BEGIN
    NULL;
  END validate_amount;
END pkg_billing;
"""


def _fake_complete(system, user):
    if "call chain" in system:
        return json.dumps({"title": "Chain title", "body": "End to end.",
                           "rules": ["chain rule"]})
    return json.dumps({"summary": f"Summary.", "rules": ["amount >= 0"],
                       "confidence": 0.8})


def _setup(tmp_path, pipeline):
    (tmp_path / "BillingService.java").write_text(JAVA)
    (tmp_path / "pkg_billing.pkb").write_text(PLSQL)
    pipeline.ingest_path(tmp_path)


def test_analyze_backfills_chunks_and_stores_chains(tmp_path, pipeline, store,
                                                    fake_graph):
    _setup(tmp_path, pipeline)
    settings = Settings(codegraph_extract=True)
    analyzer = ChainAnalyzer(settings, complete=_fake_complete)
    result = analyze_corpus(tmp_path, store, settings, fake_graph,
                            analyzer=analyzer)

    assert result["functions_analyzed"] >= 3   # charge, validate, validate_amount
    assert result["chains_stored"] >= 1
    assert result["chunks_backfilled"] >= 1
    assert result["errors"] == []

    # backfilled chunk carries the summary in metadata (searchable enrichment)
    items = store.get_items(limit=50, where={"language": "java"})
    assert any(i["metadata"].get("summary") == "Summary." for i in items)

    # chain item retrievable from the sibling collection
    chains = store.sibling(f"{store.stats()['collection']}__chains")
    got = chains.get_items(limit=10)
    assert got and got[0]["metadata"]["kind"] == "chain"
    assert got[0]["metadata"]["title"] == "Chain title"

    # graph persisted with REAL chunk ids for backfilled functions
    ent = fake_graph.get_entity("com.acme.billing.billingservice.validate")
    assert ent and any(not c.startswith("cg:") for c in ent["chunk_ids"])


def test_analyze_reports_llm_failures_and_falls_back(tmp_path, pipeline, store,
                                                     fake_graph, fake_extractor):
    _setup(tmp_path, pipeline)

    def broken(system, user):
        raise RuntimeError("llm down")

    settings = Settings(codegraph_extract=True)
    result = analyze_corpus(tmp_path, store, settings, fake_graph,
                            analyzer=ChainAnalyzer(settings, complete=broken),
                            extractor=fake_extractor)
    assert result["functions_analyzed"] == 0
    assert result["errors"]                          # failures recorded
    assert result["fallback_extracted"] >= 1          # uncovered code chunks extracted
    assert result["chains_stored"] == 0


def test_delete_codegraph_clears_synthetic_rows(fake_graph):
    from opendomainmcp.graph.models import Edge, Entity

    fake_graph.upsert_entities([Entity(normalized_name="f", display_name="F",
                                       type="function", chunk_id="cg:abc")])
    fake_graph.upsert_edges([Edge(src="f", dst="g", relation_type="calls",
                                  chunk_id="cg:abc")])
    fake_graph.upsert_functions([{"qualified_name": "F", "file": "F.java",
                                  "start_line": 1, "end_line": 2,
                                  "language": "java", "signature": "F",
                                  "kind": "function"}])
    fake_graph.delete_codegraph()
    assert fake_graph.get_function("F") is None
    ent = fake_graph.get_entity("f")
    assert not ent or not any(c.startswith("cg:") for c in ent["chunk_ids"])
