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


def test_backfill_merges_summaries_for_shared_chunk(tmp_path, pipeline, store,
                                                    fake_graph):
    """Two functions whose line ranges overlap the SAME stored chunk must merge
    their knowledge into one upsert, not have the last write win."""
    # A small package: both procedures land in one line-fallback chunk
    # (PL/SQL has no bundled grammar; the whole file fits one line window).
    (tmp_path / "pkg_two.pkb").write_text(
        "CREATE OR REPLACE PACKAGE BODY pkg_two AS\n"
        "  PROCEDURE alpha IS\n"
        "  BEGIN\n"
        "    NULL;\n"
        "  END alpha;\n"
        "  PROCEDURE beta IS\n"
        "  BEGIN\n"
        "    NULL;\n"
        "  END beta;\n"
        "END pkg_two;\n"
    )
    pipeline.ingest_path(tmp_path)

    def per_fn_complete(system, user):
        if "call chain" in system:
            return json.dumps({"title": "T", "body": "B", "rules": []})
        name = user.split("Function: ")[1].split(" ")[0]
        return json.dumps({"summary": f"Does {name}.",
                           "rules": [f"rule of {name}"], "confidence": 0.5})

    # Observe upserts so we can assert the shared chunk is written exactly once.
    upserted_ids = []
    orig_upsert = store.upsert

    def counting_upsert(chunks):
        upserted_ids.extend(c.id for c in chunks)
        return orig_upsert(chunks)

    store.upsert = counting_upsert

    settings = Settings(codegraph_extract=True)
    result = analyze_corpus(tmp_path, store, settings, fake_graph,
                            analyzer=ChainAnalyzer(settings,
                                                   complete=per_fn_complete))
    assert result["errors"] == []
    assert result["chunks_backfilled"] == 1  # one shared chunk, counted once

    items = store.get_items(limit=50, where={"language": "plsql"})
    merged = [i for i in items
              if "Does pkg_two.alpha." in i["metadata"].get("summary", "")]
    assert merged, "shared chunk missing alpha's summary"
    assert "Does pkg_two.beta." in merged[0]["metadata"]["summary"]
    # both functions' rules merged into concepts
    assert "rule of pkg_two.alpha" in merged[0]["metadata"]["concepts"]
    assert "rule of pkg_two.beta" in merged[0]["metadata"]["concepts"]
    # the shared chunk was upserted exactly once during backfill
    assert upserted_ids.count(merged[0]["id"]) == 1


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
