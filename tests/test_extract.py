import pytest

from opendomainmcp.extract.knowledge import ExtractionError, _parse


def test_parse_plain_json():
    k = _parse('{"summary": "s", "concepts": ["a", "b"], "relations": ["a -> b"]}')
    assert k.summary == "s"
    assert k.concepts == ["a", "b"]
    assert k.relations == ["a -> b"]


def test_parse_strips_markdown_fence():
    raw = '```json\n{"summary": "hi", "concepts": [], "relations": []}\n```'
    k = _parse(raw)
    assert k.summary == "hi"


def test_parse_rejects_non_json():
    with pytest.raises(ExtractionError):
        _parse("I could not produce JSON, sorry.")


def test_null_extractor_disabled_via_settings():
    from opendomainmcp.config import Settings
    from opendomainmcp.extract import NullExtractor, get_extractor

    extractor = get_extractor(Settings(extract_knowledge=False))
    assert isinstance(extractor, NullExtractor)
    assert extractor.extract("x", "text").is_empty()
