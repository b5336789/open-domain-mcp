import json

import pytest
from fastapi.testclient import TestClient

from opendomainmcp.api.app import create_app
from opendomainmcp.config import Settings
from opendomainmcp.context import Context


@pytest.fixture
def client(store, pipeline, tmp_path):
    settings = Settings(data_dir=tmp_path)
    ctx = Context(settings=settings, store=store, pipeline=pipeline)
    app = create_app(context=ctx, context_factory=lambda: ctx)
    return TestClient(app), ctx, tmp_path


def _corpus(root):
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "notes.md").write_text("Vector databases store embeddings for search.\n")


def test_stats_empty(client):
    tc, _, _ = client
    data = tc.get("/api/stats").json()
    assert data["count"] == 0
    assert data["embedder"] == "fake"


def test_ingest_stream_then_search_and_items(client):
    tc, ctx, tmp_path = client
    src = tmp_path / "src"
    src.mkdir()
    _corpus(src)

    resp = tc.get("/api/ingest/stream", params={"path": str(src)})
    assert resp.status_code == 200
    reports = [
        json.loads(line[len("data:"):].strip())
        for line in resp.text.splitlines()
        if line.startswith("data:") and '"stage": "report"' in line
    ]
    assert reports and reports[0]["files_indexed"] == 2

    results = tc.post("/api/search", json={"query": "add numbers", "kind": "code"}).json()
    assert any(r["metadata"].get("symbol") == "add" for r in results)

    items = tc.get("/api/items").json()
    assert len(items) >= 1
    item_id = items[0]["id"]

    # edit metadata
    patched = tc.patch(
        f"/api/items/{item_id}",
        json={"metadata": {**items[0]["metadata"], "note": "reviewed"}},
    ).json()
    assert patched["metadata"]["note"] == "reviewed"

    # delete
    assert tc.delete(f"/api/items/{item_id}").json()["deleted"] == item_id
    assert tc.get(f"/api/items/{item_id}").status_code == 404


def test_upload(client):
    tc, ctx, _ = client
    resp = tc.post(
        "/api/upload",
        files=[("files", ("a.txt", b"hello world", "text/plain"))],
    ).json()
    assert resp["files"] == ["a.txt"]
    assert (resp["path"]).endswith(resp["path"].split("/")[-1])


def test_collections_endpoint(client):
    tc, _, _ = client
    data = tc.get("/api/collections").json()
    assert "active" in data and isinstance(data["collections"], list)

    name = "apiproj_xyz"
    tc.post("/api/collections", json={"name": name})
    names = {c["name"] for c in tc.get("/api/collections").json()["collections"]}
    assert name in names

    assert tc.delete(f"/api/collections/{name}").json()["deleted"] == name


def test_settings_roundtrip_and_validation(client):
    tc, _, _ = client
    assert tc.get("/api/settings").json()["editable"]["chunk_size"] == 1200
    ok = tc.patch("/api/settings", json={"values": {"chunk_size": 777}})
    assert ok.json()["updated"] == ["chunk_size"]
    bad = tc.patch("/api/settings", json={"values": {"data_dir": "/etc"}})
    assert bad.status_code == 400
