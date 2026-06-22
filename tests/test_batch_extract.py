from opendomainmcp.ingest.batch_extract import _text_hash, BatchItem, CachedExtractor, BatchExtractor
from opendomainmcp.models import KnowledgeUnit


def test_text_hash_is_deterministic_64_hex():
    h = _text_hash("hello world")
    assert h == _text_hash("hello world")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
    assert _text_hash("other") != h


def test_cached_extractor_returns_hit():
    ku = KnowledgeUnit(summary="cached")
    cache = {_text_hash("abc"): ku}

    class BoomFallback:
        def extract(self, *a, **k):
            raise AssertionError("fallback should not be called on a hit")

    ext = CachedExtractor(cache, BoomFallback())
    assert ext.extract("abc", "text") is ku


def test_cached_extractor_falls_back_on_miss():
    calls = []

    class Fallback:
        def extract(self, text, kind, language=None):
            calls.append((text, kind))
            return KnowledgeUnit(summary="live")

    ext = CachedExtractor({}, Fallback())
    out = ext.extract("missing", "code", "python")
    assert out.summary == "live"
    assert calls == [("missing", "code")]


def _msg(text):
    block = type("B", (), {"type": "text", "text": text})
    return type("M", (), {"content": [block]})


class _FakeBatches:
    """Mimics client.messages.batches: create / retrieve / results."""

    def __init__(self, results, ends_after=1):
        self._results = results          # list of (custom_id, type, text)
        self._ends_after = ends_after
        self._retrieves = 0
        self.created_requests = None

    def create(self, requests):
        self.created_requests = requests
        return type("Batch", (), {"id": "batch_1", "processing_status": "in_progress"})

    def retrieve(self, _id):
        self._retrieves += 1
        status = "ended" if self._retrieves >= self._ends_after else "in_progress"
        counts = type("C", (), {"processing": 0, "succeeded": len(self._results), "errored": 0})
        return type("Batch", (), {"processing_status": status, "request_counts": counts})

    def results(self, _id):
        for cid, rtype, text in self._results:
            if rtype == "succeeded":
                inner = type("R", (), {"type": "succeeded", "message": _msg(text)})
            else:
                inner = type("R", (), {"type": rtype})
            yield type("Res", (), {"custom_id": cid, "result": inner})


def _fake_client(fake_batches):
    messages = type("Messages", (), {"batches": fake_batches})
    return type("Client", (), {"messages": messages})()


def test_extract_many_builds_requests_and_parses_succeeded():
    good = '{"summary":"S","concepts":["c"],"relations":[],"knowledge_type":"Feature","audience":[],"confidence":1}'
    items = [BatchItem(text_hash="h1", text="alpha", kind="text"),
             BatchItem(text_hash="h2", text="beta", kind="code", language="python")]
    fake = _FakeBatches([("h1", "succeeded", good), ("h2", "errored", "")])
    ext = BatchExtractor(_fake_client(fake), "claude-haiku-4-5", poll_interval=0)

    out = ext.extract_many(items)

    # request assembly
    assert len(fake.created_requests) == 2
    r0 = fake.created_requests[0]
    cid = r0["custom_id"] if isinstance(r0, dict) else r0.custom_id
    assert cid == "h1"
    # succeeded parsed, errored omitted
    assert out["h1"].summary == "S"
    assert "h2" not in out


def test_extract_many_polls_until_ended():
    good = '{"summary":"S","concepts":[],"relations":[],"knowledge_type":"Feature","audience":[],"confidence":1}'
    fake = _FakeBatches([("h1", "succeeded", good)], ends_after=3)
    ext = BatchExtractor(_fake_client(fake), "claude-haiku-4-5", poll_interval=0)
    out = ext.extract_many([BatchItem(text_hash="h1", text="x", kind="text")])
    assert fake._retrieves == 3
    assert out["h1"].summary == "S"
