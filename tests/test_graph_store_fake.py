from opendomainmcp.graph.models import Edge, Entity


def test_fake_graph_upsert_get_and_neighbors(fake_graph):
    fake_graph.upsert_entities([
        Entity("auth service", "Auth Service", "Service", "c1"),
        Entity("user db", "User DB", "Resource", "c1"),
    ])
    fake_graph.upsert_edges([Edge("auth service", "user db", "depends_on", "c1")])

    ent = fake_graph.get_entity("Auth Service")  # lookup is case-insensitive
    assert ent["type"] == "Service" and "c1" in ent["chunk_ids"]

    nb = fake_graph.neighbors("auth service")
    names = {(n["entity"]["normalized_name"], n["relation_type"], n["direction"])
             for n in nb["neighbors"]}
    assert ("user db", "depends_on", "out") == next(iter(names))


def test_fake_graph_delete_for_chunks_removes_nodes_and_edges(fake_graph):
    fake_graph.upsert_entities([Entity("a", "A", "Concept", "c1")])
    fake_graph.upsert_edges([Edge("a", "b", "uses", "c1")])
    fake_graph.delete_for_chunks(["c1"])
    assert fake_graph.get_entity("a") is None
    assert fake_graph.neighbors("a")["neighbors"] == []


def test_fake_graph_get_missing_entity_returns_none(fake_graph):
    assert fake_graph.get_entity("nope") is None


def test_fake_graph_dedupes_repeated_edge(fake_graph):
    fake_graph.upsert_entities([
        Entity("a", "A", "Concept", "c1"),
        Entity("b", "B", "Concept", "c1"),
    ])
    edge = Edge("a", "b", "uses", "c1")
    fake_graph.upsert_edges([edge])
    fake_graph.upsert_edges([edge])
    result = fake_graph.neighbors("a")
    assert len(result["neighbors"]) == 1


def test_fake_store_roundtrips_edge_evidence(fake_graph):
    import json

    ev = json.dumps([{"claim": "c", "quote": "q", "source": "A.java",
                      "start_line": 1, "end_line": 1, "verified": True}])
    fake_graph.upsert_entities([Entity("a", "A", "Concept", "c1"),
                                Entity("b", "B", "Concept", "c1")])
    fake_graph.upsert_edges([Edge("a", "b", "uses", "c1", confidence=0.5, evidence=ev)])
    # Re-upsert the same key with higher confidence but empty evidence: the
    # confidence must rise AND the original evidence must survive (mirrors
    # Maria's GREATEST(confidence) + IF(VALUES(evidence_json)='') semantics).
    fake_graph.upsert_edges([Edge("a", "b", "uses", "c1", confidence=0.9)])
    edges = fake_graph._slot()["edges"]
    assert len(edges) == 1
    assert edges[0].confidence == 0.9
    assert json.loads(edges[0].evidence) == json.loads(ev)


def test_fake_store_roundtrips_entity_evidence(fake_graph):
    import json

    from opendomainmcp.graph.models import Entity

    ev = json.dumps([{"claim": "c", "quote": "q", "source": "A.java",
                      "start_line": 1, "end_line": 1, "verified": True}])
    fake_graph.upsert_entities([Entity(normalized_name="x", display_name="X",
                                       type="Concept", chunk_id="c1",
                                       evidence=ev)])
    got = fake_graph.get_entity("x")
    assert got["evidence"] == json.loads(ev)


def test_neighbors_payload_carries_edge_evidence(fake_graph):
    import json

    ev = json.dumps([{"claim": "auth calls db", "quote": "db.query()", "source": "auth.py",
                      "start_line": 5, "end_line": 5, "verified": True}])
    fake_graph.upsert_entities([
        Entity("auth", "Auth", "Service", "c1"),
        Entity("db", "DB", "Resource", "c1"),
    ])
    fake_graph.upsert_edges([Edge("auth", "db", "depends_on", "c1", evidence=ev)])

    nb = fake_graph.neighbors("auth")
    assert len(nb["neighbors"]) == 1
    neighbor = nb["neighbors"][0]
    assert "edge_evidence" in neighbor
    assert neighbor["edge_evidence"] == json.loads(ev)


def test_neighbors_payload_edge_evidence_empty_when_none(fake_graph):
    fake_graph.upsert_entities([
        Entity("x", "X", "Concept", "c1"),
        Entity("y", "Y", "Concept", "c1"),
    ])
    fake_graph.upsert_edges([Edge("x", "y", "uses", "c1")])  # no evidence
    nb = fake_graph.neighbors("x")
    neighbor = nb["neighbors"][0]
    assert neighbor["edge_evidence"] == []
