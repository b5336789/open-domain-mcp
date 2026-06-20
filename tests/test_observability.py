"""Tests for the API observability helpers."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from opendomainmcp.api.observability import (
    RequestLoggingMiddleware,
    health_payload,
    setup_logging,
)


# -- setup_logging ------------------------------------------------------------


def test_setup_logging_is_idempotent():
    # Arrange: reset the configured sentinel and handler set for a clean baseline.
    root = logging.getLogger()
    if hasattr(root, "_odm_logging_configured"):
        delattr(root, "_odm_logging_configured")
    original = list(root.handlers)
    for h in original:
        root.removeHandler(h)

    try:
        # Act: configure twice.
        setup_logging("INFO")
        after_first = len(root.handlers)
        setup_logging("DEBUG")
        after_second = len(root.handlers)

        # Assert: no duplicate handler, level still adjustable.
        assert after_first == 1
        assert after_second == 1
        assert root.level == logging.DEBUG
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in original:
            root.addHandler(h)
        if hasattr(root, "_odm_logging_configured"):
            delattr(root, "_odm_logging_configured")


def test_setup_logging_honors_env_override(monkeypatch):
    # Arrange
    root = logging.getLogger()
    if hasattr(root, "_odm_logging_configured"):
        delattr(root, "_odm_logging_configured")
    original = list(root.handlers)
    for h in original:
        root.removeHandler(h)
    monkeypatch.setenv("ODM_LOG_LEVEL", "WARNING")

    try:
        # Act
        setup_logging("INFO")
        # Assert: env override wins over the argument.
        assert root.level == logging.WARNING
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in original:
            root.addHandler(h)
        if hasattr(root, "_odm_logging_configured"):
            delattr(root, "_odm_logging_configured")


# -- health_payload -----------------------------------------------------------


def _make_ctx(*, graph_raises: bool = False):
    """Build a minimal stub context for health_payload."""
    settings = SimpleNamespace(collection_name="kb")
    store = SimpleNamespace(
        stats=lambda: {"count": 42, "embedder": "bge-small", "dim": 384}
    )

    def list_workflows(limit=50, q=None):
        if graph_raises:
            raise RuntimeError("graph down")
        return []

    graph = SimpleNamespace(list_workflows=list_workflows)
    return SimpleNamespace(settings=settings, store=store, graph=graph)


def test_health_payload_returns_expected_keys_and_ok_graph():
    # Arrange
    ctx = _make_ctx()

    # Act
    payload = health_payload(ctx)

    # Assert
    assert set(payload) == {
        "status",
        "collection",
        "documents",
        "embedder",
        "graph",
        "version",
    }
    assert payload["status"] == "ok"
    assert payload["collection"] == "kb"
    assert payload["documents"] == 42
    assert payload["embedder"] == "bge-small"
    assert payload["graph"] == "ok"
    assert isinstance(payload["version"], str)


def test_health_payload_degraded_graph_stays_ok():
    # Arrange: graph probe raises.
    ctx = _make_ctx(graph_raises=True)

    # Act
    payload = health_payload(ctx)

    # Assert: status unaffected, graph flagged unavailable.
    assert payload["status"] == "ok"
    assert payload["graph"] == "unavailable"


# -- RequestLoggingMiddleware -------------------------------------------------


def test_request_logging_middleware_logs_and_passes_through(caplog):
    # Arrange
    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/ping")
    def ping():
        return {"pong": True}

    client = TestClient(app)

    # Act
    with caplog.at_level(logging.INFO, logger="opendomainmcp.api"):
        response = client.get("/ping")

    # Assert: response untouched and a request log was emitted.
    assert response.status_code == 200
    assert response.json() == {"pong": True}
    messages = [r.getMessage() for r in caplog.records]
    assert any("GET" in m and "/ping" in m and "200" in m for m in messages)
