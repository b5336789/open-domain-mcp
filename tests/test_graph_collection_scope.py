# tests/test_graph_collection_scope.py
from opendomainmcp.graph.models import Entity
from tests.conftest import FakeGraphStore


def test_collection_isolation_via_shared_backing():
    backing = {}
    a = FakeGraphStore(collection="a", backing=backing)
    b = FakeGraphStore(collection="b", backing=backing)
    a.upsert_entities([Entity("auth", "Auth", "Service", "c1")])
    assert a.get_entity("auth") is not None
    assert b.get_entity("auth") is None          # isolated by collection
    assert b.list_entities() == []


def test_delete_collection_removes_only_that_collection():
    backing = {}
    a = FakeGraphStore(collection="a", backing=backing)
    b = FakeGraphStore(collection="b", backing=backing)
    a.upsert_entities([Entity("x", "X", "Concept", "c1")])
    b.upsert_entities([Entity("y", "Y", "Concept", "c2")])
    a.delete_collection("a")
    assert a.get_entity("x") is None
    assert b.get_entity("y") is not None
