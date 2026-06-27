from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from opendomainmcp.api import deps
from opendomainmcp.config import Settings


def test_get_ctx_serializes_first_context_build(monkeypatch):
    monkeypatch.setattr(
        deps,
        "get_settings",
        lambda: Settings(collection_name="domain_knowledge"),
    )
    calls = 0
    calls_lock = threading.Lock()
    start = threading.Barrier(8)

    def factory(collection=None):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.03)
        return SimpleNamespace(collection=collection)

    state = SimpleNamespace(
        context=None,
        contexts={},
        contexts_lock=threading.Lock(),
        context_factory=factory,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=state),
        query_params={},
        headers={},
    )

    def resolve():
        start.wait()
        return deps.get_ctx(request)

    with ThreadPoolExecutor(max_workers=8) as pool:
        contexts = list(pool.map(lambda _: resolve(), range(8)))

    assert calls == 1
    assert len({id(ctx) for ctx in contexts}) == 1
