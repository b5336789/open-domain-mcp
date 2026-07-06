from opendomainmcp.models import KnowledgeUnit
from opendomainmcp.graph.normalize import normalize_name
from opendomainmcp.graph.builder import build_graph


def test_normalize_name_lowercases_and_collapses_whitespace():
    assert normalize_name("  Auth   Service ") == "auth service"


def test_build_graph_produces_entities_and_edges():
    k = KnowledgeUnit(
        entities=[{"name": "Auth Service", "type": "Service"}],
        typed_relations=[{"src": "Auth Service", "dst": "User DB", "type": "depends_on"}],
    )
    entities, edges = build_graph(k, chunk_id="c1")
    by_norm = {e.normalized_name: e for e in entities}
    # declared entity keeps its type; relation endpoint not declared -> Concept
    assert by_norm["auth service"].type == "Service"
    assert by_norm["auth service"].display_name == "Auth Service"
    assert by_norm["user db"].type == "Concept"
    assert len(edges) == 1
    assert (edges[0].src, edges[0].dst, edges[0].relation_type) == (
        "auth service", "user db", "depends_on")
    assert edges[0].chunk_id == "c1"


def test_build_graph_dedupes_entities_by_normalized_name():
    k = KnowledgeUnit(entities=[{"name": "Auth Service", "type": "Service"},
                                {"name": "auth service", "type": "Concept"}])
    entities, _ = build_graph(k, chunk_id="c1")
    assert len([e for e in entities if e.normalized_name == "auth service"]) == 1
    # first-seen wins: first entity "Auth Service" is retained
    retained = [e for e in entities if e.normalized_name == "auth service"][0]
    assert retained.display_name == "Auth Service"


def test_build_graph_propagates_confidence():
    k = KnowledgeUnit(
        entities=[{"name": "Service A", "type": "Service"}],
        typed_relations=[{"src": "Service A", "dst": "Service B", "type": "depends_on"}],
        confidence=0.7
    )
    entities, edges = build_graph(k, chunk_id="c1")
    assert len(entities) == 2
    assert all(e.confidence == 0.7 for e in entities)
    assert len(edges) == 1
    assert edges[0].confidence == 0.7


def test_build_graph_skips_relation_with_blank_endpoint():
    k = KnowledgeUnit(
        typed_relations=[{"src": "   ", "dst": "Service B", "type": "depends_on"}]
    )
    entities, edges = build_graph(k, chunk_id="c1")
    assert edges == []


def test_build_graph_empty_knowledge_yields_nothing():
    entities, edges = build_graph(KnowledgeUnit(), chunk_id="c1")
    assert entities == [] and edges == []


def test_build_graph_threads_matching_evidence():
    import json

    from opendomainmcp.graph.builder import build_graph
    from opendomainmcp.models import KnowledgeUnit

    k = KnowledgeUnit(
        summary="S", confidence=0.9,
        entities=[{"name": "BillingService", "type": "Service"}],
        typed_relations=[{"src": "BillingService", "dst": "OrderRepo",
                          "type": "uses"}],
        evidence=[{"claim": "BillingService validates amounts",
                   "quote": "q", "source": "A.java", "start_line": 3,
                   "end_line": 3, "verified": True},
                  {"claim": "unrelated", "quote": "x", "source": "A.java",
                   "start_line": 9, "end_line": 9, "verified": False}])
    entities, edges = build_graph(k, "c1")
    billing = next(e for e in entities if e.display_name == "BillingService")
    ev = json.loads(billing.evidence)
    assert len(ev) == 1 and ev[0]["start_line"] == 3   # verified + name-matched only
    assert edges and json.loads(edges[0].evidence)[0]["verified"]
