# tests/test_graph_api.py
from fastapi.testclient import TestClient

from opendomainmcp.api.app import create_app
from opendomainmcp.config import Settings
from opendomainmcp.context import Context
from opendomainmcp.graph.models import Edge, Entity


def _client(store, fake_graph):
    fake_graph.upsert_entities([
        Entity("auth service", "Auth Service", "Service", "c1"),
        Entity("user db", "User DB", "Resource", "c1")])
    fake_graph.upsert_edges([Edge("auth service", "user db", "depends_on", "c1")])
    ctx = Context(settings=Settings(), store=store, pipeline=None, graph=fake_graph)
    return TestClient(create_app(context=ctx))


def test_get_entity_endpoint_returns_entity_and_neighbors(store, fake_graph):
    resp = _client(store, fake_graph).get("/api/graph/entity/Auth Service")
    assert resp.status_code == 200
    body = resp.json()
    assert body["entity"]["type"] == "Service"
    assert body["neighbors"][0]["entity"]["normalized_name"] == "user db"


def test_get_entity_endpoint_404_for_missing(store, fake_graph):
    resp = _client(store, fake_graph).get("/api/graph/entity/nope")
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body
    assert "nope" in body["error"]


def test_list_entities_endpoint_returns_items(store, fake_graph):
    resp = _client(store, fake_graph).get("/api/graph/entities")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert len(body["items"]) == 2


def test_list_entities_endpoint_filters_by_type(store, fake_graph):
    resp = _client(store, fake_graph).get("/api/graph/entities?type=Service")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["type"] == "Service"


def test_list_entities_endpoint_filters_by_q(store, fake_graph):
    resp = _client(store, fake_graph).get("/api/graph/entities?q=auth")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["normalized_name"] == "auth service"


def test_list_entities_endpoint_respects_limit(store, fake_graph):
    resp = _client(store, fake_graph).get("/api/graph/entities?limit=1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
