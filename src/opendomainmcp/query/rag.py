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


def _format_sources(results: list[SearchResult]) -> str:
    blocks = []
    for i, r in enumerate(results, 1):
        loc = r.metadata.get("source", "?")
        if r.metadata.get("symbol"):
            loc += f"::{r.metadata['symbol']}"
        blocks.append(f"[{i}] {loc}\n{r.text}")
    return "\n\n".join(blocks)


def _citations(results: list[SearchResult]) -> list[dict]:
    cites = []
    for i, r in enumerate(results, 1):
        cites.append({
            "n": i,
            "id": r.id,
            "source": r.metadata.get("source"),
            "symbol": r.metadata.get("symbol"),
            "score": r.score,
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


def answer_question(query, store, settings, top_k: int = 6, synthesize=None) -> dict:
    results = store.search(query, top_k=top_k, mode=settings.search_mode)
    if not results:
        return {"answer": "No indexed content matched this question.", "citations": []}
    user = f"Question: {query}\n\nSources:\n{_format_sources(results)}"
    if synthesize is not None:
        answer = synthesize(settings.answer_model, _SYSTEM, user)
    else:
        answer = _claude_synthesize(
            settings.answer_model, _SYSTEM, user,
            timeout=settings.request_timeout, max_retries=settings.max_retries,
        )
    return {"answer": answer, "citations": _citations(results)}


def answer_question_stream(query, store, settings, top_k: int = 6, synthesize_stream=None):
    """Like :func:`answer_question` but yields incremental events:

    ``{"type": "delta", "text": ...}`` for each answer fragment, then a final
    ``{"type": "citations", "citations": [...]}``. ``synthesize_stream`` lets
    tests inject an offline token generator.
    """
    results = store.search(query, top_k=top_k, mode=settings.search_mode)
    if not results:
        yield {"type": "delta", "text": "No indexed content matched this question."}
        yield {"type": "citations", "citations": []}
        return
    user = f"Question: {query}\n\nSources:\n{_format_sources(results)}"
    if synthesize_stream is not None:
        stream = synthesize_stream(settings.answer_model, _SYSTEM, user)
    else:
        stream = _claude_synthesize_stream(
            settings.answer_model, _SYSTEM, user,
            timeout=settings.request_timeout, max_retries=settings.max_retries,
        )
    for delta in stream:
        yield {"type": "delta", "text": delta}
    yield {"type": "citations", "citations": _citations(results)}
