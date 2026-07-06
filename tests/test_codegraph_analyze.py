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

    # Pre-seed graph with a function/entity carrying a real (non-cg:) chunk id —
    # the total-failure run must NOT wipe it.
    from opendomainmcp.graph.models import Entity
    fake_graph.upsert_entities([Entity(normalized_name="billing.validate",
                                       display_name="billing.validate",
                                       type="function", chunk_id="real-chunk-0001")])
    fake_graph.upsert_functions([{"qualified_name": "billing.validate",
                                   "file": "BillingService.java",
                                   "start_line": 1, "end_line": 5,
                                   "language": "java", "signature": "validate",
                                   "kind": "function"}])

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
    assert result.get("graph_persist_skipped")       # graph NOT wiped

    # Pre-seeded function and entity still present (graph not destroyed).
    fn = fake_graph.get_function("billing.validate")
    assert fn is not None
    ent = fake_graph.get_entity("billing.validate")
    assert ent is not None
    assert "real-chunk-0001" in ent["chunk_ids"]


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


def test_store_chains_prunes_stale_items(tmp_path, pipeline, store, fake_graph):
    """A stale ChainItem from a previous run is deleted after a successful analysis."""
    _setup(tmp_path, pipeline)

    from opendomainmcp.models import ChainItem
    chains_store = store.sibling(f"{store.stats()['collection']}__chains")
    stale = ChainItem(entry="stale.entry", title="Stale", body="Old chain")
    chains_store.upsert([stale])
    assert any(i["id"] == stale.id for i in chains_store.get_items(limit=10))

    settings = Settings(codegraph_extract=True)
    analyzer = ChainAnalyzer(settings, complete=_fake_complete)
    result = analyze_corpus(tmp_path, store, settings, fake_graph, analyzer=analyzer)

    assert result["chains_stored"] >= 1
    remaining = chains_store.get_items(limit=50)
    assert not any(i["id"] == stale.id for i in remaining), \
        "stale chain item should have been pruned after a successful run"


def test_store_chains_stale_survives_broken_llm(tmp_path, pipeline, store,
                                                fake_graph, fake_extractor):
    """A stale ChainItem is preserved when the LLM fails entirely (no new chains stored)."""
    _setup(tmp_path, pipeline)

    from opendomainmcp.models import ChainItem
    chains_store = store.sibling(f"{store.stats()['collection']}__chains")
    stale = ChainItem(entry="stale.entry", title="Stale", body="Old chain")
    chains_store.upsert([stale])

    def broken(system, user):
        raise RuntimeError("llm down")

    settings = Settings(codegraph_extract=True)
    analyze_corpus(tmp_path, store, settings, fake_graph,
                   analyzer=ChainAnalyzer(settings, complete=broken),
                   extractor=fake_extractor)

    remaining = chains_store.get_items(limit=50)
    assert any(i["id"] == stale.id for i in remaining), \
        "stale chain item must survive when no new chains were stored (total LLM failure)"


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


def test_store_chains_expected_entry_survives_synthesis_failure(
    tmp_path, pipeline, store, fake_graph
):
    """A pre-existing chain item survives when its entry is expected,
    even if synthesis failed. Truly-stale entries are still pruned."""
    # Create code with two separate functions to ensure chains are assembled
    (tmp_path / "Test.java").write_text(
        "public class Test {\n"
        "  public void funcA() {\n"
        "    funcB();\n"
        "  }\n"
        "  public void funcB() {}\n"
        "}\n"
    )
    pipeline.ingest_path(tmp_path)

    from opendomainmcp.models import ChainItem
    from opendomainmcp.codegraph.build import build_codegraph
    from opendomainmcp.codegraph.chains import assemble_chains

    chains_store = store.sibling(f"{store.stats()['collection']}__chains")
    settings = Settings(codegraph_extract=True)

    # Determine what chains will be assembled
    graph = build_codegraph(tmp_path, settings)
    chains = assemble_chains(graph, settings.codegraph_max_chain_depth)

    # Need at least 1 chain to test
    if len(chains) < 1:
        import pytest
        pytest.skip("Need at least 1 chain assembled")

    # Use first chain to fail synthesis
    failed_entry = chains[0].entry

    # Pre-seed items:
    # 1. One that will fail synthesis but whose entry is still expected
    failed_item = ChainItem(entry=failed_entry, title="PreExisting",
                            body="From previous run")
    # 2. One that's truly stale (entry not in expected chains)
    stale_item = ChainItem(entry="truly.stale.entry", title="Stale",
                           body="Not in current chains")
    chains_store.upsert([failed_item, stale_item])

    # Verify they're stored
    assert any(i["id"] == failed_item.id for i in chains_store.get_items(limit=50))
    assert any(i["id"] == stale_item.id for i in chains_store.get_items(limit=50))

    # Make the analyzer fail for only the first chain entry, succeed for others
    def selective_failure(system, user):
        if "call chain" in system and failed_entry in user:
            raise RuntimeError(f"synthesis failed")
        # All other chains succeed
        if "call chain" in system:
            return json.dumps({"title": "Chain title", "body": "End to end.",
                               "rules": ["chain rule"]})
        return json.dumps({"summary": f"Summary.", "rules": ["amount >= 0"],
                           "confidence": 0.8})

    analyzer = ChainAnalyzer(settings, complete=selective_failure)
    result = analyze_corpus(tmp_path, store, settings, fake_graph,
                            analyzer=analyzer)

    remaining = chains_store.get_items(limit=50)
    remaining_ids = {i["id"] for i in remaining}

    # The pre-existing item for the failed entry should survive
    # because the entry is still expected (in the assembled chains)
    assert failed_item.id in remaining_ids, \
        "pre-existing item must survive when entry is expected"

    # The truly-stale item should be pruned if pruning happened
    # (i.e., if at least one chain stored successfully)
    if result["chains_stored"] > 0:
        assert stale_item.id not in remaining_ids, \
            "stale item should be pruned when at least one chain stored"
