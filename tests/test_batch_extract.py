from opendomainmcp.ingest.batch_extract import _text_hash, BatchItem, CachedExtractor
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
