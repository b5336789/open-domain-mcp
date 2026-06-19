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
