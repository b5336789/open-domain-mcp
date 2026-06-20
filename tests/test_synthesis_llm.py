import pytest
from opendomainmcp.synthesis.llm import (
    ArticleCritic, ArticleWriter, SynthesisError, keep_article,
    parse_article, parse_verdict,
)


def test_parse_article_clamps_and_requires_body():
    out = parse_article('{"title": "T", "body": "B [1]", "business_relevance": 2}')
    assert out == {"title": "T", "body": "B [1]", "business_relevance": 1.0}
    with pytest.raises(SynthesisError):
        parse_article('{"title": "T", "business_relevance": 0.5}')  # no body


def test_parse_verdict_defaults_missing_flags_to_false():
    assert parse_verdict('{"grounded": true, "business_meaningful": false, "note": "x"}') \
        == {"grounded": True, "business_meaningful": False, "note": "x"}
    assert parse_verdict("not json at all") == {
        "grounded": False, "business_meaningful": False, "note": ""}


def test_keep_article_requires_both_flags_true():
    assert keep_article({"grounded": True, "business_meaningful": True}) is True
    assert keep_article({"grounded": True, "business_meaningful": False}) is False
    assert keep_article({}) is False  # reject when uncertain


class _FakeAnthropic:
    """Minimal stand-in for anthropic.Anthropic returning a canned text block."""
    def __init__(self, text):
        self._text = text
        self.messages = self  # .messages.create(...)

    def create(self, **kw):
        block = type("B", (), {"type": "text", "text": self._text})()
        return type("M", (), {"content": [block]})()


def test_writer_and_critic_parse_injected_client_output():
    writer = ArticleWriter(model="m", client=_FakeAnthropic(
        '{"title": "Billing", "body": "Body [1]", "business_relevance": 0.7}'))
    assert writer.write("billing", "evidence")["title"] == "Billing"
    critic = ArticleCritic(model="m", client=_FakeAnthropic(
        '{"grounded": true, "business_meaningful": true, "note": "ok"}'))
    assert keep_article(critic.judge("billing", "Body [1]", "evidence")) is True
