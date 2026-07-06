"""Candidate pairing: embedding + chain + entity signals (enhancement #5)."""

from opendomainmcp.consensus.pairing import find_candidates
from opendomainmcp.consensus.units import RuleUnit


class TwoBucketEmbedder:
    """'negative' claims -> [1,0]; everything else -> [0,1]."""

    def embed(self, texts):
        return [[1.0, 0.0] if "negative" in t else [0.0, 1.0] for t in texts]

    @property
    def dim(self):
        return 2


def _unit(key, claim, chunk_ids, layer="service", origin="chunk"):
    return RuleUnit(key=key, claim=claim, origin=origin, origin_id=key,
                    layer=layer, source="s", chunk_ids=chunk_ids, evidence=[])


def test_embedding_signal_pairs_similar_claims():
    units = [_unit("chunk:a:0", "amount must not be negative", ["ca"]),
             _unit("chunk:b:0", "order amount cannot be negative", ["cb"]),
             _unit("chunk:c:0", "orders ship within two days", ["cc"])]
    pairs = find_candidates(units, TwoBucketEmbedder(), graph=None, threshold=0.9)
    keys = {(p.a.key, p.b.key) for p in pairs}
    assert ("chunk:a:0", "chunk:b:0") in keys
    assert not any("chunk:c:0" in k for pair in keys for k in pair)
    p = pairs[0]
    assert p.signal == "embedding" and p.similarity >= 0.9


def test_chain_signal_pairs_chunk_with_chain_unit():
    chain_unit = _unit("chain:x:0", "totally different wording", ["ca", "cb"],
                       layer="chain", origin="chain")
    chunk_unit = _unit("chunk:a:0", "amount rule", ["ca"])
    pairs = find_candidates([chain_unit, chunk_unit], TwoBucketEmbedder(),
                            graph=None, threshold=0.99)
    assert len(pairs) == 1 and pairs[0].signal == "chain"


def test_entity_signal_via_fake_graph(fake_graph):
    from opendomainmcp.graph.models import Entity

    fake_graph.upsert_entities([
        Entity(normalized_name="billing", display_name="Billing",
               type="Concept", chunk_id="ca"),
        Entity(normalized_name="billing", display_name="Billing",
               type="Concept", chunk_id="cb"),
    ])
    units = [_unit("chunk:a:0", "first wording", ["ca"]),
             _unit("chunk:b:0", "second phrasing", ["cb"])]
    pairs = find_candidates(units, TwoBucketEmbedder(), graph=fake_graph,
                            threshold=0.99)
    assert len(pairs) == 1 and pairs[0].signal == "entity"


def test_dedup_and_determinism():
    a = _unit("chunk:a:0", "no negative amounts", ["ca"])
    b = _unit("chunk:b:0", "negative amounts forbidden", ["ca"])  # also same chunk? no — chain-less
    pairs1 = find_candidates([a, b], TwoBucketEmbedder(), threshold=0.9)
    pairs2 = find_candidates([b, a], TwoBucketEmbedder(), threshold=0.9)
    assert len(pairs1) == len(pairs2) == 1
    assert (pairs1[0].a.key, pairs1[0].b.key) == (pairs2[0].a.key, pairs2[0].b.key)
