from __future__ import annotations

import pytest

from opendomainmcp import context as context_module
from opendomainmcp.config import Settings
from opendomainmcp.graph.store import NullGraphStore


class _DummyStore:
    pass


class _DummyExtractor:
    pass


def _patch_context_dependencies(monkeypatch):
    monkeypatch.setattr(context_module, "get_embedder", lambda settings: object())
    monkeypatch.setattr(context_module, "get_extractor", lambda settings: _DummyExtractor())
    monkeypatch.setattr(context_module, "get_reranker", lambda settings: None)
    monkeypatch.setattr(
        context_module,
        "ChromaStore",
        lambda *args, **kwargs: _DummyStore(),
    )


def test_build_context_can_disable_graph_store_for_local_demo(monkeypatch, tmp_path):
    _patch_context_dependencies(monkeypatch)

    class BoomMariaGraphStore:
        def __init__(self, *args, **kwargs):
            raise AssertionError("MariaDB should not be initialized")

    monkeypatch.setattr(context_module, "MariaGraphStore", BoomMariaGraphStore)

    ctx = context_module.build_context(
        Settings(data_dir=tmp_path, graph_store_backend="null")
    )

    assert isinstance(ctx.graph, NullGraphStore)


def test_build_context_rejects_unknown_graph_store_backend(monkeypatch, tmp_path):
    _patch_context_dependencies(monkeypatch)

    with pytest.raises(ValueError, match="graph_store_backend"):
        context_module.build_context(
            Settings(data_dir=tmp_path, graph_store_backend="sqlite")
        )
