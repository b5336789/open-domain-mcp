from __future__ import annotations

import json

from ..config import Settings

_WRITER_SYSTEM = (
    "You write a short, conversational knowledge article about ONE topic, for a "
    "mixed audience of product and engineering readers, using ONLY the numbered "
    "evidence snippets provided. Structure the body as plain prose: (1) what this "
    "is and what it does, (2) what the docs say versus what the code actually does "
    "— call out any gap explicitly, (3) cite evidence inline as [n]. Do not invent "
    "facts not supported by the evidence. Respond with ONLY a JSON object: "
    '{"title": short title, "body": the article text with [n] citations, '
    '"business_relevance": a number 0-1 for how business-meaningful (vs pure '
    "implementation trivia) this topic is}. No prose outside the JSON."
)

_CRITIC_SYSTEM = (
    "You are a strict reviewer of a draft knowledge article. You are given the "
    "article and the numbered evidence it was built from. Judge two things and "
    "DEFAULT TO false when uncertain: is every substantive claim grounded in the "
    "evidence (no hallucination)? is the topic genuinely business/domain knowledge "
    "rather than implementation trivia? Respond with ONLY a JSON object: "
    '{"grounded": bool, "business_meaningful": bool, "note": a short reason}. '
    "No prose outside the JSON."
)


class SynthesisError(Exception):
    pass


def _json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        data = json.loads(text[start:end + 1], strict=False)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def parse_article(raw: str) -> dict:
    data = _json_object(raw)
    body = str(data.get("body", "")).strip()
    if not body:
        raise SynthesisError(f"No article body in model output: {raw[:120]!r}")
    try:
        rel = max(0.0, min(1.0, float(data.get("business_relevance", 0.0))))
    except (TypeError, ValueError):
        rel = 0.0
    return {"title": str(data.get("title", "")).strip() or "Untitled",
            "body": body, "business_relevance": rel}


def parse_verdict(raw: str) -> dict:
    data = _json_object(raw)
    return {"grounded": data.get("grounded") is True,
            "business_meaningful": data.get("business_meaningful") is True,
            "note": str(data.get("note", "")).strip()}


def keep_article(verdict: dict) -> bool:
    return bool(verdict.get("grounded")) and bool(verdict.get("business_meaningful"))


class _AnthropicCaller:
    def __init__(self, model, system, max_tokens, timeout, max_retries,
                 client=None, base_url=None):
        if client is None:
            import anthropic
            kwargs = {"timeout": timeout, "max_retries": max_retries}
            if base_url:
                kwargs["base_url"] = base_url
            client = anthropic.Anthropic(**kwargs)
        self._client, self._model = client, model
        self._system, self._max_tokens = system, max_tokens

    def _call(self, user: str) -> str:
        msg = self._client.messages.create(
            model=self._model, max_tokens=self._max_tokens, system=self._system,
            messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in msg.content if b.type == "text")


class _OpenAICaller:
    def __init__(self, model, system, max_tokens, timeout, max_retries,
                 client=None, base_url=None):
        if client is None:
            from openai import OpenAI
            kwargs = {"timeout": timeout, "max_retries": max_retries}
            if base_url:
                kwargs["base_url"] = base_url
            client = OpenAI(**kwargs)
        self._client, self._model = client, model
        self._system, self._max_tokens = system, max_tokens

    def _call(self, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self._model, max_tokens=self._max_tokens,
            messages=[{"role": "system", "content": self._system},
                      {"role": "user", "content": user}])
        return resp.choices[0].message.content or ""


def _caller(backend, **kw):
    return _OpenAICaller(**kw) if str(backend).lower() == "openai" else _AnthropicCaller(**kw)


class ArticleWriter:
    def __init__(self, model, max_tokens=1200, timeout=60.0, max_retries=2,
                 client=None, backend="anthropic", base_url=None):
        self._c = _caller(backend, model=model, system=_WRITER_SYSTEM,
                          max_tokens=max_tokens, timeout=timeout,
                          max_retries=max_retries, client=client, base_url=base_url)

    def write(self, topic: str, evidence: str) -> dict:
        return parse_article(self._c._call(f"Topic: {topic}\n\nEvidence:\n{evidence}"))


class ArticleCritic:
    def __init__(self, model, max_tokens=400, timeout=60.0, max_retries=2,
                 client=None, backend="anthropic", base_url=None):
        self._c = _caller(backend, model=model, system=_CRITIC_SYSTEM,
                          max_tokens=max_tokens, timeout=timeout,
                          max_retries=max_retries, client=client, base_url=base_url)

    def judge(self, topic: str, body: str, evidence: str) -> dict:
        return parse_verdict(self._c._call(
            f"Topic: {topic}\n\nArticle:\n{body}\n\nEvidence:\n{evidence}"))


def get_article_llms(settings: Settings) -> tuple[ArticleWriter, ArticleCritic]:
    # Article synthesis pins its own provider/model when set, else inherits the
    # global llm_backend / extraction_model (today's behavior).
    kw = dict(model=settings.resolved_synthesize_model(),
              timeout=settings.request_timeout,
              max_retries=settings.max_retries,
              backend=settings.resolved_synthesize_provider(),
              base_url=settings.synthesize_base_url or None)
    return ArticleWriter(**kw), ArticleCritic(**kw)
