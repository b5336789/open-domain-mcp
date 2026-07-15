"""Pure structural quality metrics over an exported graph (offline, no DB)."""
from opendomainmcp.evals.graph_metrics import (
    canonical_key,
    concept_recall,
    duplication,
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
