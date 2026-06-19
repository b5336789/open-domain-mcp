import os

import pytest

from opendomainmcp.graph.models import Edge, Entity

pytestmark = pytest.mark.integration


@pytest.fixture
def maria_store():
    if not os.getenv("GRAPH_DB_HOST"):
        pytest.skip("MariaDB integration env not configured (set GRAPH_DB_HOST)")
    from opendomainmcp.graph.store import MariaGraphStore
    store = MariaGraphStore(
        host=os.environ["GRAPH_DB_HOST"], port=int(os.getenv("GRAPH_DB_PORT", "3306")),
        user=os.environ["GRAPH_DB_USER"], password=os.getenv("GRAPH_DB_PASSWORD", ""),
        database=os.environ["GRAPH_DB_NAME"])
    store.ensure_schema()
    store.delete_for_chunks(["it-c1"])  # clean slate for this chunk id
    return store


def test_mariadb_roundtrip(maria_store):
    maria_store.upsert_entities([
        Entity("auth service", "Auth Service", "Service", "it-c1"),
        Entity("user db", "User DB", "Resource", "it-c1")])
    maria_store.upsert_edges([Edge("auth service", "user db", "depends_on", "it-c1")])
    assert maria_store.get_entity("Auth Service")["type"] == "Service"
    nb = maria_store.neighbors("auth service")
    assert any(n["entity"]["normalized_name"] == "user db" for n in nb["neighbors"])
    maria_store.delete_for_chunks(["it-c1"])
    assert maria_store.get_entity("auth service") is None


def test_collection_isolation_and_delete(maria_store):
    """Two stores on the same DB with different collections must not see each other's data."""
    from opendomainmcp.graph.store import MariaGraphStore
    store_b = MariaGraphStore(
        host=os.environ["GRAPH_DB_HOST"], port=int(os.getenv("GRAPH_DB_PORT", "3306")),
        user=os.environ["GRAPH_DB_USER"], password=os.getenv("GRAPH_DB_PASSWORD", ""),
        database=os.environ["GRAPH_DB_NAME"],
        collection="it-collection-b")
    store_b.ensure_schema()
    store_b.delete_collection("it-collection-b")  # clean slate

    maria_store.upsert_entities([Entity("svc_a", "SvcA", "Service", "it-c2")])
    store_b.upsert_entities([Entity("svc_b", "SvcB", "Service", "it-c2")])

    assert maria_store.get_entity("svc_a") is not None
    assert maria_store.get_entity("svc_b") is None   # not visible across collections
    assert store_b.get_entity("svc_b") is not None
    assert store_b.get_entity("svc_a") is None       # not visible across collections

    maria_store.delete_collection(maria_store._collection)
    assert maria_store.get_entity("svc_a") is None
    assert store_b.get_entity("svc_b") is not None   # b untouched

    store_b.delete_collection("it-collection-b")  # clean up
