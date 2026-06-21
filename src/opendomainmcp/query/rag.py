"""Retrieval-augmented answering.

Retrieve the most relevant chunks, then have Claude compose an answer that cites
them inline as ``[n]``. The LLM call is isolated behind a ``synthesize`` callable
so the logic is unit-testable offline; the default backend uses the Anthropic SDK
(``ANTHROPIC_API_KEY`` / ``ANTHROPIC_BASE_URL``) and fails loudly without a key.
"""

from __future__ import annotations

from ..models import SearchResult

_SYSTEM = (
    "You answer questions strictly from the provided numbered sources. Cite the "
    "sources you use inline as [n] matching their numbers. If the sources do not "
    "contain the answer, say so plainly. Be concise and precise."
)


class AnswerError(Exception):
    pass


def _source_label(r) -> str:
    meta = r.metadata
    if meta.get("kind") == "article":
        return meta.get("title") or meta.get("topic") or r.id
    loc = meta.get("source", "?")
    if meta.get("symbol"):
        loc += f"::{meta['symbol']}"
    return loc


def _format_sources(results: list[SearchResult]) -> str:
    blocks = []
    for i, r in enumerate(results, 1):
        blocks.append(f"[{i}] {_source_label(r)}\n{r.text}")
    return "\n\n".join(blocks)


def _apply_relevance_floor(results: list[SearchResult], settings) -> list[SearchResult]:
    """Drop all results when even the best one is below ``retrieve_min_score``.

    Gates on the *max* score (not per-result) so an in-corpus question keeps its
    relevant mid-score chunks, while a question with no genuinely relevant source
    falls through to the existing "no content matched" refusal instead of being
    answered (and hallucinated) from weak sources. ``retrieve_min_score`` <= 0
    disables this entirely, preserving prior behavior.
    """
    floor = getattr(settings, "retrieve_min_score", 0.0) or 0.0
    if floor > 0 and results and max(r.score for r in results) < floor:
        return []
    return results


def _citations(results: list[SearchResult]) -> list[dict]:
    cites = []
    for i, r in enumerate(results, 1):
        is_article = r.metadata.get("kind") == "article"
        if is_article:
            source = _source_label(r)   # title / topic / id
            symbol = None
            type_ = "article"
        else:
            source = r.metadata.get("source", "?")  # bare path — CLI appends ::symbol itself
            symbol = r.metadata.get("symbol")
            type_ = "chunk"
        cites.append({
            "n": i,
            "id": r.id,
            "source": source,
            "symbol": symbol,
            "score": r.score,
            "type": type_,
        })
    return cites


def _claude_synthesize(model: str, system: str, user: str,
                       timeout: float = 60.0, max_retries: int = 2) -> str:
    try:
        import anthropic

        # timeout bounds the call; max_retries backs off on transient errors.
        client = anthropic.Anthropic(timeout=timeout, max_retries=max_retries)
        message = client.messages.create(
            model=model, max_tokens=800, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in message.content if b.type == "text")
    except Exception as exc:  # no key, no SDK, or API error -> fail loud
        raise AnswerError(
            f"answer synthesis failed ({exc!r}); set ANTHROPIC_API_KEY to enable 'ask'"
        ) from exc


def _claude_synthesize_stream(model: str, system: str, user: str,
                              timeout: float = 60.0, max_retries: int = 2):
    """Yield answer text deltas as they arrive from the model."""
    try:
        import anthropic

        client = anthropic.Anthropic(timeout=timeout, max_retries=max_retries)
        with client.messages.stream(
            model=model, max_tokens=800, system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            yield from stream.text_stream
    except Exception as exc:  # no key, no SDK, or API error -> fail loud
        raise AnswerError(
            f"answer synthesis failed ({exc!r}); set ANTHROPIC_API_KEY to enable 'ask'"
        ) from exc


def _openai_synthesize(model: str, system: str, user: str,
                       timeout: float = 60.0, max_retries: int = 2) -> str:
    """Synthesize via the OpenAI chat-completions API (works against any
    OpenAI-compatible endpoint set through ``OPENAI_BASE_URL``)."""
    try:
        from openai import OpenAI

        client = OpenAI(timeout=timeout, max_retries=max_retries)
        resp = client.chat.completions.create(
            model=model, max_tokens=800,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:  # no key, no SDK, or API error -> fail loud
        raise AnswerError(
            f"answer synthesis failed ({exc!r}); check OPENAI_API_KEY / "
            "OPENAI_BASE_URL for the openai backend"
        ) from exc


def _openai_synthesize_stream(model: str, system: str, user: str,
                              timeout: float = 60.0, max_retries: int = 2):
    """Yield answer text deltas from an OpenAI-compatible streaming response."""
    try:
        from openai import OpenAI

        client = OpenAI(timeout=timeout, max_retries=max_retries)
        stream = client.chat.completions.create(
            model=model, max_tokens=800, stream=True,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    except Exception as exc:  # no key, no SDK, or API error -> fail loud
        raise AnswerError(
            f"answer synthesis failed ({exc!r}); check OPENAI_API_KEY / "
            "OPENAI_BASE_URL for the openai backend"
        ) from exc


def _synthesize(settings, system: str, user: str) -> str:
    """Pick the configured LLM backend for a single (non-streaming) answer."""
    if settings.llm_backend.lower() == "openai":
        return _openai_synthesize(
            settings.answer_model, system, user,
            timeout=settings.request_timeout, max_retries=settings.max_retries,
        )
    return _claude_synthesize(
        settings.answer_model, system, user,
        timeout=settings.request_timeout, max_retries=settings.max_retries,
    )


def _synthesize_stream(settings, system: str, user: str):
    """Pick the configured LLM backend for a streaming answer."""
    if settings.llm_backend.lower() == "openai":
        return _openai_synthesize_stream(
            settings.answer_model, system, user,
            timeout=settings.request_timeout, max_retries=settings.max_retries,
        )
    return _claude_synthesize_stream(
        settings.answer_model, system, user,
        timeout=settings.request_timeout, max_retries=settings.max_retries,
    )


def answer_question(query, store, settings, top_k: int = 6, synthesize=None) -> dict:
    from ..retrieval import search_unified
    results = search_unified(store, query, top_k=top_k,
                             mode=settings.search_mode, settings=settings)
    results = _apply_relevance_floor(results, settings)
    if not results:
        return {"answer": "No indexed content matched this question.", "citations": []}
    user = f"Question: {query}\n\nSources:\n{_format_sources(results)}"
    if synthesize is not None:
        answer = synthesize(settings.answer_model, _SYSTEM, user)
    else:
        answer = _synthesize(settings, _SYSTEM, user)
    return {"answer": answer, "citations": _citations(results)}


def answer_question_stream(query, store, settings, top_k: int = 6, synthesize_stream=None):
    """Like :func:`answer_question` but yields incremental events:

    ``{"type": "delta", "text": ...}`` for each answer fragment, then a final
    ``{"type": "citations", "citations": [...]}``. ``synthesize_stream`` lets
    tests inject an offline token generator.
    """
    from ..retrieval import search_unified
    results = search_unified(store, query, top_k=top_k,
                             mode=settings.search_mode, settings=settings)
    results = _apply_relevance_floor(results, settings)
    if not results:
        yield {"type": "delta", "text": "No indexed content matched this question."}
        yield {"type": "citations", "citations": []}
        return
    user = f"Question: {query}\n\nSources:\n{_format_sources(results)}"
    if synthesize_stream is not None:
        stream = synthesize_stream(settings.answer_model, _SYSTEM, user)
    else:
        stream = _synthesize_stream(settings, _SYSTEM, user)
    for delta in stream:
        yield {"type": "delta", "text": delta}
    yield {"type": "citations", "citations": _citations(results)}
