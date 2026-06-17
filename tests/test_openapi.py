import json

from opendomainmcp.ingest.openapi import looks_like_openapi, parse_spec, split_openapi

_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Orders API"},
    "paths": {
        "/orders": {
            "get": {
                "operationId": "listOrders",
                "summary": "List orders",
                "tags": ["orders"],
                "responses": {"200": {"description": "ok"}},
            },
            "post": {
                "operationId": "createOrder",
                "summary": "Create an order",
                "parameters": [{"name": "idempotencyKey", "in": "header"}],
                "responses": {"201": {"description": "created"}},
            },
        },
        "/health": {"get": {"summary": "Health check", "responses": {}}},
    },
}


def test_detects_openapi_json_and_yaml():
    assert looks_like_openapi(parse_spec(json.dumps(_SPEC)))
    import yaml

    assert looks_like_openapi(parse_spec(yaml.safe_dump(_SPEC)))
    assert not looks_like_openapi(parse_spec('{"just": "data"}'))
    assert parse_spec("not valid json or yaml: : :") is None or not looks_like_openapi(
        parse_spec("not valid json or yaml: : :")
    )


def test_split_openapi_one_chunk_per_operation():
    chunks = split_openapi(json.dumps(_SPEC), "orders.json")
    assert len(chunks) == 3  # GET+POST /orders, GET /health
    assert all(c.knowledge.knowledge_type == "API" for c in chunks)

    by_symbol = {c.symbol: c for c in chunks}
    assert "listOrders" in by_symbol
    assert "createOrder" in by_symbol
    # operations without an operationId fall back to "METHOD path"
    assert "GET /health" in by_symbol

    create = by_symbol["createOrder"]
    assert "POST /orders" in create.text
    assert "idempotencyKey" in create.text
    assert "orders" in by_symbol["listOrders"].knowledge.tags


def test_split_openapi_ignores_non_spec():
    assert split_openapi('{"paths": "not a dict"}', "x.json") == []


def test_ingest_openapi_file_classifies_as_api(pipeline, store, tmp_path):
    spec_file = tmp_path / "api.json"
    spec_file.write_text(json.dumps(_SPEC))
    report = pipeline.ingest_path(str(spec_file))
    assert report.files_indexed == 1

    items = store.get_items(limit=100)
    assert items and all(i["metadata"].get("knowledge_type") == "API" for i in items)
    # API operations are pre-classified, so the LLM extractor must not run.
    assert pipeline._extractor.calls == 0
