"""Pure structural quality metrics over an exported graph (offline, no DB)."""
from opendomainmcp.evals.graph_metrics import (
    canonical_key,
    compute_all,
    concept_recall,
    connectivity,
    duplication,
    extraction_coverage,
    orphan_ratio,
)


def _ent(norm, display=None, type_="Concept"):
    return {"normalized_name": norm, "display_name": display or norm, "type": type_}


# --- canonical_key ---------------------------------------------------------

def test_canonical_key_merges_case_separator_and_plural_variants():
    assert canonical_key("Sales Orders") == canonical_key("sales_order")
    assert canonical_key("tax-rule") == canonical_key("Tax Rule")


def test_canonical_key_is_token_order_insensitive():
    assert canonical_key("rate tax") == canonical_key("tax rate")


def test_canonical_key_keeps_short_tokens_verbatim():
    # "gas"/"gst" style short tokens must not be de-pluralized into collisions.
    assert canonical_key("gas") != canonical_key("ga")


def test_canonical_key_handles_non_ascii_names():
    # Two distinct CJK names must NOT collapse into the same key.
    assert canonical_key("銷售訂單") != canonical_key("稅務規則")
    assert canonical_key("銷售訂單") == canonical_key("銷售訂單")


# --- duplication -----------------------------------------------------------

def test_duplication_clusters_name_variants():
    entities = [_ent("sales order"), _ent("sales_orders"), _ent("tax rule")]
    d = duplication(entities)
    assert d["total_entities"] == 3
    assert d["duplicate_clusters"] == 1
    assert d["excess_entities"] == 1
    assert d["duplication_ratio"] == 1 / 3
    assert sorted(d["clusters"][0]) == ["sales order", "sales_orders"]
    assert d["clusters_truncated"] is False


def test_duplication_empty_graph_is_zero_not_crash():
    d = duplication([])
    assert d["total_entities"] == 0 and d["duplication_ratio"] == 0.0


def test_duplication_does_not_cluster_symbol_only_names():
    entities = [_ent("!!!"), _ent("???"), _ent("tax rule")]
    d = duplication(entities)
    assert d["duplicate_clusters"] == 0
    assert d["total_entities"] == 3


def test_duplication_is_deterministic_regardless_of_input_order():
    # Report order must be a property of the code (sorted clusters, sorted
    # names within a cluster), not of unordered SELECT/dict-insertion order.
    entities = [
        _ent("sales order"), _ent("sales_orders"), _ent("sales-order"),
        _ent("tax rule"), _ent("tax_rules"),
        _ent("grand total"),
    ]
    forward = duplication(entities)
    reversed_result = duplication(list(reversed(entities)))
    assert forward == reversed_result


# --- concept_recall --------------------------------------------------------

def test_concept_recall_matches_by_canonical_key():
    entities = [_ent("pricing rules"), _ent("grand total")]
    r = concept_recall(entities, ["Pricing Rule", "Tax Category"])
    assert r["golden_total"] == 2
    assert r["found"] == 1
    assert r["recall"] == 0.5
    assert r["missing_concepts"] == ["Tax Category"]


def test_concept_recall_empty_golden_returns_none():
    assert concept_recall([_ent("a b c")], [])["recall"] is None


def _edge(src, dst, rel="related_to", chunk="c1"):
    return {"src": src, "dst": dst, "relation_type": rel,
            "chunk_id": chunk, "confidence": 1.0}


# --- extraction_coverage ---------------------------------------------------

def test_coverage_reports_uncovered_chunks():
    cov = extraction_coverage(
        {"c1", "c2", "c3"},
        [{"normalized_name": "a", "chunk_id": "c1"},
         {"normalized_name": "b", "chunk_id": "c1"}])
    assert cov["chunks_total"] == 3
    assert cov["chunks_with_entities"] == 1
    assert cov["coverage"] == 1 / 3
    assert cov["uncovered_chunk_ids"] == ["c2", "c3"]


def test_coverage_ignores_stale_entity_chunks_outside_universe():
    cov = extraction_coverage({"c1"}, [{"normalized_name": "a", "chunk_id": "gone"}])
    assert cov["chunks_with_entities"] == 0


# --- orphan_ratio ----------------------------------------------------------

def test_orphans_are_entities_with_no_edges():
    entities = [_ent("a"), _ent("b"), _ent("lonely")]
    o = orphan_ratio(entities, [_edge("a", "b")])
    assert o["orphans"] == 1
    assert o["orphan_names"] == ["lonely"]
    assert o["orphan_ratio"] == 1 / 3


# --- connectivity ----------------------------------------------------------

def test_connectivity_counts_components_and_largest_share():
    entities = [_ent(n) for n in ("a", "b", "c", "d", "e")]
    edges = [_edge("a", "b"), _edge("b", "c"), _edge("d", "e")]
    c = connectivity(entities, edges)
    assert c["components"] == 2
    assert c["largest_component_share"] == 3 / 5
    assert c["component_sizes_top"] == [3, 2]
    assert c["singleton_components"] == 0


def test_connectivity_ignores_edges_to_unknown_entities():
    c = connectivity([_ent("a")], [_edge("a", "ghost")])
    assert c["components"] == 1 and c["component_sizes_top"] == [1]


def test_connectivity_empty_graph():
    c = connectivity([], [])
    assert c["components"] == 0 and c["largest_component_share"] == 0.0


# --- compute_all -----------------------------------------------------------

def test_compute_all_assembles_all_five_sections():
    export = {
        "entities": [_ent("sales order"), _ent("tax rule")],
        "edges": [_edge("sales order", "tax rule")],
        "entity_chunks": [{"normalized_name": "sales order", "chunk_id": "c1"}],
    }
    report = compute_all(export, {"c1", "c2"}, ["Sales Order", "Pricing Rule"])
    assert set(report) == {"coverage", "orphans", "connectivity",
                           "duplication", "concept_recall"}
    assert report["coverage"]["coverage"] == 0.5
    assert report["concept_recall"]["missing_concepts"] == ["Pricing Rule"]
