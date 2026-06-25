"""Per-function provider / model / base_url selection (Feature 3).

Embed, extract, and synthesize (article writer/critic) can each pick their own
provider (anthropic vs openai-compatible) and model independently of the global
``llm_backend``, with an optional per-function ``base_url`` override. When a
per-function override is unset it inherits the global default, so existing
single-backend setups keep working unchanged.
"""

import pytest

from opendomainmcp.config import EDITABLE_FIELDS, Settings


# --- config defaults + editability -----------------------------------------

def test_new_per_function_fields_default_to_empty():
    s = Settings()
    assert s.extract_provider == ""
    assert s.extract_base_url == ""
    assert s.synthesize_provider == ""
    assert s.synthesize_model == ""
    assert s.synthesize_base_url == ""
    assert s.embedder_base_url == ""


def test_per_function_fields_are_editable():
    for field in ("embedder_base_url", "extract_provider", "extract_base_url",
                  "synthesize_provider", "synthesize_model", "synthesize_base_url"):
        assert field in EDITABLE_FIELDS


def test_per_function_overrides_round_trip(tmp_path):
    s = Settings(data_dir=tmp_path)
    s2 = s.save_overrides({"extract_provider": "openai",
                           "synthesize_model": "claude-opus-4-8"})
    assert s2.extract_provider == "openai"
    assert s2.synthesize_model == "claude-opus-4-8"
    # persisted and re-loaded
    assert Settings(data_dir=tmp_path).apply_overrides().extract_provider == "openai"


# --- provider/model resolution ---------------------------------------------

def test_extract_provider_inherits_llm_backend_when_unset():
    assert Settings(llm_backend="openai").resolved_extract_provider() == "openai"
    assert Settings(llm_backend="anthropic").resolved_extract_provider() == "anthropic"


def test_extract_provider_overrides_llm_backend():
    s = Settings(llm_backend="anthropic", extract_provider="openai")
    assert s.resolved_extract_provider() == "openai"


def test_synthesize_provider_inherits_then_overrides():
    assert Settings(llm_backend="openai").resolved_synthesize_provider() == "openai"
    s = Settings(llm_backend="openai", synthesize_provider="anthropic")
    assert s.resolved_synthesize_provider() == "anthropic"


def test_synthesize_model_falls_back_to_extraction_model():
    s = Settings(extraction_model="claude-sonnet-4-6")
    assert s.resolved_synthesize_model() == "claude-sonnet-4-6"
    s2 = Settings(extraction_model="claude-sonnet-4-6", synthesize_model="gpt-4o")
    assert s2.resolved_synthesize_model() == "gpt-4o"


# --- extractor factory dispatch + base_url ---------------------------------

def test_get_extractor_dispatches_on_extract_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "lm-studio")
    from opendomainmcp.extract.knowledge import OpenAIExtractor, get_extractor

    # extract_provider overrides the anthropic-default llm_backend
    ext = get_extractor(Settings(extract_knowledge=True, extract_provider="openai"))
    assert isinstance(ext, OpenAIExtractor)


def test_get_extractor_inherits_llm_backend(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    from opendomainmcp.extract.knowledge import ClaudeExtractor, get_extractor

    ext = get_extractor(Settings(extract_knowledge=True, llm_backend="anthropic"))
    assert isinstance(ext, ClaudeExtractor)


def test_extract_base_url_reaches_openai_client(monkeypatch):
    import openai

    cap = {}
    monkeypatch.setattr(openai, "OpenAI",
                        lambda **kw: cap.update(kw) or object())
    from opendomainmcp.extract.knowledge import OpenAIExtractor

    OpenAIExtractor("m", base_url="http://localhost:1234/v1")
    assert cap["base_url"] == "http://localhost:1234/v1"


def test_extract_base_url_omitted_when_unset(monkeypatch):
    import openai

    cap = {}
    monkeypatch.setattr(openai, "OpenAI",
                        lambda **kw: cap.update(kw) or object())
    from opendomainmcp.extract.knowledge import OpenAIExtractor

    OpenAIExtractor("m")
    assert "base_url" not in cap  # falls back to the SDK / OPENAI_BASE_URL default


# --- article synthesis factory dispatch + base_url -------------------------

def test_get_article_llms_dispatches_on_synthesize_provider():
    from opendomainmcp.synthesis.llm import (_AnthropicCaller, _OpenAICaller,
                                             get_article_llms)

    monkey_settings = Settings(llm_backend="anthropic", synthesize_provider="openai",
                               synthesize_model="gpt-4o")
    # building the caller must not require a real client; inject via patching
    import openai
    import anthropic
    import unittest.mock as mock
    with mock.patch.object(openai, "OpenAI", lambda **kw: object()), \
         mock.patch.object(anthropic, "Anthropic", lambda **kw: object()):
        writer, critic = get_article_llms(monkey_settings)
    assert isinstance(writer._c, _OpenAICaller)
    assert isinstance(critic._c, _OpenAICaller)
    assert writer._c._model == "gpt-4o"


def test_get_article_llms_inherits_backend_and_model():
    from opendomainmcp.synthesis.llm import _AnthropicCaller, get_article_llms

    import anthropic
    import unittest.mock as mock
    s = Settings(llm_backend="anthropic", extraction_model="claude-sonnet-4-6")
    with mock.patch.object(anthropic, "Anthropic", lambda **kw: object()):
        writer, critic = get_article_llms(s)
    assert isinstance(writer._c, _AnthropicCaller)
    assert writer._c._model == "claude-sonnet-4-6"  # inherited extraction_model


def test_synthesize_base_url_reaches_caller_client():
    import openai
    import unittest.mock as mock

    cap = {}
    s = Settings(synthesize_provider="openai", synthesize_model="m",
                 synthesize_base_url="http://localhost:1234/v1")
    with mock.patch.object(openai, "OpenAI", lambda **kw: cap.update(kw) or object()):
        get_article_llms = __import__(
            "opendomainmcp.synthesis.llm", fromlist=["get_article_llms"]
        ).get_article_llms
        get_article_llms(s)
    assert cap["base_url"] == "http://localhost:1234/v1"
