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


class _FakeOpenAI:
    """Minimal stand-in for OpenAI returning a chat-completion response shape."""
    def __init__(self, text):
        self._text = text
        self.chat = type("Chat", (), {})()
        self.chat.completions = self  # .chat.completions.create(...)

    def create(self, **kw):
        message = type("Msg", (), {"content": self._text})()
        choice = type("Choice", (), {"message": message})()
        return type("Resp", (), {"choices": [choice]})()


def test_writer_and_critic_parse_injected_client_output():
    writer = ArticleWriter(model="m", client=_FakeAnthropic(
        '{"title": "Billing", "body": "Body [1]", "business_relevance": 0.7}'))
    assert writer.write("billing", "evidence")["title"] == "Billing"
    critic = ArticleCritic(model="m", client=_FakeAnthropic(
        '{"grounded": true, "business_meaningful": true, "note": "ok"}'))
    assert keep_article(critic.judge("billing", "Body [1]", "evidence")) is True


def test_writer_and_critic_with_openai_backend():
    """Verify OpenAI caller branch wiring works end-to-end."""
    writer = ArticleWriter(model="m", backend="openai", client=_FakeOpenAI(
        '{"title": "Cache", "body": "Caching pattern [1]", "business_relevance": 0.8}'))
    result = writer.write("caching", "evidence")
    assert result["title"] == "Cache"
    assert result["body"] == "Caching pattern [1]"
    assert result["business_relevance"] == 0.8

    critic = ArticleCritic(model="m", backend="openai", client=_FakeOpenAI(
        '{"grounded": true, "business_meaningful": true, "note": "solid"}'))
    verdict = critic.judge("caching", "Caching pattern [1]", "evidence")
    assert keep_article(verdict) is True


def test_parse_article_raises_on_non_json():
    """Verify parse_article rejects input with no extractable body."""
    with pytest.raises(SynthesisError):
        parse_article("not json at all")
