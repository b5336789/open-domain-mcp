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
