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


def test_parse_classification_fields():
    raw = (
        '{"summary": "login flow", "concepts": ["auth"], "relations": [],'
        ' "knowledge_type": "Workflow", "audience": ["operations", "engineering"],'
        ' "confidence": 0.9, "tags": ["login"], "permissions": ["user"],'
        ' "references": ["https://x/y"]}'
    )
    k = _parse(raw)
    assert k.knowledge_type == "Workflow"
    assert k.audience == ["operations", "engineering"]
    assert k.confidence == 0.9
    assert k.tags == ["login"]
    assert k.permissions == ["user"]
    assert k.references == ["https://x/y"]


def test_parse_normalises_invalid_classification():
    # Unknown type -> dropped; out-of-vocab audience filtered; confidence clamped;
    # audience accepted as a bare string and case-insensitively.
    raw = (
        '{"summary": "s", "concepts": [], "relations": [],'
        ' "knowledge_type": "Banana", "audience": "Support", "confidence": 5}'
    )
    k = _parse(raw)
    assert k.knowledge_type == ""
    assert k.audience == ["support"]
    assert k.confidence == 1.0


def test_parse_tolerates_missing_classification_keys():
    k = _parse('{"summary": "s", "concepts": ["a"], "relations": []}')
    assert k.knowledge_type == ""
    assert k.audience == []
    assert k.confidence == 0.0


def test_parse_version_field():
    # Present: coerced to a stripped string.
    k = _parse(
        '{"summary": "s", "concepts": [], "relations": [], "version": " v2.3 "}'
    )
    assert k.version == "v2.3"
    # Missing: defaults to empty string.
    assert _parse('{"summary": "s", "concepts": [], "relations": []}').version == ""


def test_null_extractor_disabled_via_settings():
    from opendomainmcp.config import Settings
    from opendomainmcp.extract import NullExtractor, get_extractor

    extractor = get_extractor(Settings(extract_knowledge=False))
    assert isinstance(extractor, NullExtractor)
    assert extractor.extract("x", "text").is_empty()


def test_parse_tolerates_control_characters_in_strings():
    # Local OpenAI-compatible models sometimes emit a literal newline/tab inside
    # a JSON string value, which strict json.loads rejects. Extraction should
    # still recover the content rather than dropping the whole chunk.
    raw = '{"summary": "line one\nline two", "concepts": ["a\tb"], "audience": []}'
    k = _parse(raw)
    assert "line one" in k.summary and "line two" in k.summary
    assert k.concepts == ["a\tb"]
