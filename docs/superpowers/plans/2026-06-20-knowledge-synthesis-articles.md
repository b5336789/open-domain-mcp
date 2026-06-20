# Knowledge Synthesis — Business-Meaning Articles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fully autonomous post-ingest stage that groups stored chunks by topic and writes self-verified, conversational "business-meaning" articles into a separate, retrievable Chroma collection.

**Architecture:** A new `synthesis/` package, driven by the existing `build_context()` store. Six stages: gather candidate topics from stored chunk metadata (+ graph entities), gate them with a structural rule (no tuned numbers), collect code/doc evidence per topic via hybrid search, synthesize an article (LLM author), verify it with an independent LLM critic (grounded? business-meaningful?, reject-when-uncertain), and store survivors. `Article` duck-types the chunk interface so the existing `ChromaStore.upsert`/`search` are reused as-is.

**Tech Stack:** Python ≥ 3.11, ChromaDB, Anthropic / OpenAI SDKs (injected, mirroring `extract/knowledge.py`), pytest (offline).

## Global Constraints

- Backend Python ≥ 3.11; all tests offline (no network, no model download) — inject fake LLM clients, mirroring `tests/test_extract.py`.
- **Fail Loud:** no API key when synthesis runs → raise, never fabricate. Per-topic failures recorded in the report, never silently dropped. Zero survivors → explicit message.
- **No human-tuned magic numbers in the default path.** Topic gate is structural; the article gate is the binary critic verdict. The only CLI flags are optional escape hatches (`--limit`, `--dry-run`).
- Match existing conventions: snake_case, plain dataclasses in `models.py`, injected clients with lazy SDK construction, comma/`|`-joined scalars for Chroma metadata.
- Critic v1 is a **single** call (best-of-N is an out-of-scope escalation).
- `ask`/search integration with the articles collection is **out of scope** (later phase).

Spec: `docs/superpowers/specs/2026-06-20-knowledge-synthesis-articles-design.md`

## File Structure

- Create `src/opendomainmcp/synthesis/__init__.py` — package exports.
- Create `src/opendomainmcp/synthesis/topics.py` — pure topic gather + structural gate (`TopicCandidate`, `gather_topics`).
- Create `src/opendomainmcp/synthesis/llm.py` — injected `ArticleWriter` / `ArticleCritic`, JSON parsing, `get_article_llms`, pure `keep_article`.
- Create `src/opendomainmcp/synthesis/articles.py` — orchestrator `synthesize_articles` + `SynthesisReport`, evidence collection, storage.
- Modify `src/opendomainmcp/models.py` — add the `Article` dataclass.
- Modify `src/opendomainmcp/store/chroma_store.py` — add `ChromaStore.sibling()`.
- Modify `src/opendomainmcp/cli.py` — add the `synthesize` subcommand.
- Create tests: `tests/test_synthesis_article_model.py`, `tests/test_synthesis_topics.py`, `tests/test_synthesis_llm.py`, `tests/test_store_sibling.py`, `tests/test_synthesis_articles.py`, and extend `tests/test_cli.py`.

---

### Task 1: `Article` model

**Files:**
- Modify: `src/opendomainmcp/models.py` (add after `Chunk`, before `SearchResult`)
- Test: `tests/test_synthesis_article_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Article` dataclass with fields `title:str, topic:str, body:str, business_relevance:float, source_chunk_ids:list[str], sources:list[str], cross_validated:bool, critic_verdict:dict`. Duck-types the chunk storage interface: `id:str` (property), `text:str` (property → body), `embedding_text()->str`, `metadata()->dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_synthesis_article_model.py
from opendomainmcp.models import Article


def _article(**kw):
    base = dict(
        title="Order Approval Rule", topic="order approval",
        body="Orders over $10k require manager sign-off [1].",
        business_relevance=0.8, source_chunk_ids=["b", "a"],
        sources=["billing.py:42", "policy.md:5"], cross_validated=True,
        critic_verdict={"grounded": True, "business_meaningful": True, "note": ""},
    )
    base.update(kw)
    return Article(**base)


def test_article_id_is_stable_and_order_independent():
    a1 = _article(source_chunk_ids=["a", "b"])
    a2 = _article(source_chunk_ids=["b", "a"])
    assert a1.id == a2.id  # sorted member ids → idempotent regardless of order
    assert _article(topic="other").id != a1.id


def test_article_duck_types_chunk_storage_interface():
    a = _article()
    assert a.text == a.body
    et = a.embedding_text()
    assert "Order Approval Rule" in et and "order approval" in et and a.body in et
    meta = a.metadata()
    assert meta["kind"] == "article"
    assert meta["topic"] == "order approval"
    assert meta["business_relevance"] == 0.8
    assert meta["cross_validated"] is True
    assert meta["grounded"] is True
    assert meta["business_meaningful"] is True
    assert meta["sources"] == "billing.py:42 | policy.md:5"
    # No None/empty values leak into Chroma metadata.
    assert all(v is not None and v != "" for v in meta.values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_synthesis_article_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'Article'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/opendomainmcp/models.py — add after the Chunk dataclass
@dataclass
class Article:
    """A synthesized, business-meaning article over several chunks.

    Duck-types the storage interface used by ``ChromaStore.upsert``/``search``
    (``id`` / ``text`` / ``embedding_text`` / ``metadata``) so articles reuse the
    same store with no special-casing. ``id`` is a content hash of the topic plus
    its sorted member chunk ids → re-synthesis is idempotent.
    """

    title: str
    topic: str
    body: str
    business_relevance: float = 0.0
    source_chunk_ids: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    cross_validated: bool = False
    critic_verdict: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        members = "\n".join(sorted(self.source_chunk_ids))
        digest = hashlib.sha256(f"{self.topic}\n{members}".encode("utf-8"))
        return digest.hexdigest()

    @property
    def text(self) -> str:
        return self.body

    def embedding_text(self) -> str:
        """Title + topic + body, so retrieval matches the article's subject."""
        return f"{self.title}\n{self.topic}\n{self.body}"

    def metadata(self) -> dict:
        v = self.critic_verdict or {}
        meta = {
            "kind": "article",
            "title": self.title,
            "topic": self.topic,
            "business_relevance": self.business_relevance,
            "cross_validated": self.cross_validated,
            "grounded": bool(v.get("grounded")),
            "business_meaningful": bool(v.get("business_meaningful")),
            "sources": " | ".join(self.sources),
            "source_chunk_ids": ", ".join(self.source_chunk_ids),
        }
        return {k: val for k, val in meta.items() if val is not None and val != ""}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_synthesis_article_model.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/models.py tests/test_synthesis_article_model.py
git commit -m "feat(synthesis): add Article model duck-typing the chunk store interface"
```

---

### Task 2: Topic gather + structural gate

**Files:**
- Create: `src/opendomainmcp/synthesis/__init__.py`
- Create: `src/opendomainmcp/synthesis/topics.py`
- Test: `tests/test_synthesis_topics.py`

**Interfaces:**
- Consumes: stored items shaped like `ChromaStore.get_items()` output — `{"id": str, "text": str, "metadata": {...}}` where metadata may carry `kind` (`"code"`/`"text"`), `concepts` (comma-joined string), `knowledge_type` (str), `audience` (comma-joined string). Optional `extra_topics: Iterable[str]` (graph entity names).
- Produces:
  - `TopicCandidate` dataclass: `name:str, chunk_ids:list[str], in_code:bool, in_docs:bool, business_hits:int`. Property `cross_validated:bool` (`in_code and in_docs`). Property `rank_key:tuple` for sorting.
  - `gather_topics(items: list[dict], extra_topics: Iterable[str] = ()) -> list[TopicCandidate]` — normalizes/dedups topics (case-insensitive), aggregates signals, applies the structural gate, returns survivors sorted strongest-first.

The gate (verbatim from spec): keep a topic if `cross_validated`, **or** it is mentioned by **more than one** chunk whose `knowledge_type` ∈ {Feature, Workflow, Permission, Constraint} or whose `audience` includes `product_manager`/`solutions_architect`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_synthesis_topics.py
from opendomainmcp.synthesis.topics import TopicCandidate, gather_topics


def _item(_id, kind, concepts, ktype="", audience=""):
    return {"id": _id, "text": "",
            "metadata": {"kind": kind, "concepts": concepts,
                         "knowledge_type": ktype, "audience": audience}}


def test_cross_validated_topic_passes_and_ranks_first():
    items = [
        _item("c1", "code", "Billing Engine"),
        _item("d1", "text", "billing engine"),          # same topic, doc side
        _item("c2", "code", "Retry Loop", ktype="Code"),  # code-only, not business
    ]
    topics = gather_topics(items)
    names = [t.name for t in topics]
    assert "billing engine" in names           # normalized, deduped across code+doc
    assert "retry loop" not in names           # code-only single mention → gated out
    top = topics[0]
    assert top.name == "billing engine" and top.cross_validated is True


def test_business_typed_multi_mention_passes_without_cross_validation():
    items = [
        _item("a", "code", "Approval Policy", ktype="Permission"),
        _item("b", "code", "Approval Policy", ktype="Permission"),  # >1 business mention
        _item("e", "text", "One Off", ktype="Feature"),            # single mention → out
    ]
    topics = {t.name: t for t in gather_topics(items)}
    assert "approval policy" in topics
    assert topics["approval policy"].business_hits == 2
    assert "one off" not in topics


def test_extra_topics_from_graph_are_folded_and_deduped():
    items = [_item("c1", "code", "Billing Engine"),
             _item("d1", "text", "billing engine")]
    topics = gather_topics(items, extra_topics=["Billing Engine", "Ledger"])
    names = [t.name for t in topics]
    assert names.count("billing engine") == 1   # deduped against existing concept
    # "ledger" has no chunk support → cannot pass the gate, so it is dropped.
    assert "ledger" not in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_synthesis_topics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'opendomainmcp.synthesis'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/opendomainmcp/synthesis/__init__.py
"""Autonomous post-ingest synthesis of business-meaning articles."""
```

```python
# src/opendomainmcp/synthesis/topics.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

_BUSINESS_TYPES = {"feature", "workflow", "permission", "constraint"}
_BUSINESS_AUDIENCE = {"product_manager", "solutions_architect"}


@dataclass
class TopicCandidate:
    name: str
    chunk_ids: list[str] = field(default_factory=list)
    in_code: bool = False
    in_docs: bool = False
    business_hits: int = 0

    @property
    def cross_validated(self) -> bool:
        return self.in_code and self.in_docs

    @property
    def rank_key(self) -> tuple:
        # Strongest first: cross-validated, then business support, then breadth.
        return (self.cross_validated, self.business_hits, len(self.chunk_ids))


def _concepts(meta: dict) -> list[str]:
    return [c.strip() for c in str(meta.get("concepts", "")).split(",") if c.strip()]


def _is_business(meta: dict) -> bool:
    if str(meta.get("knowledge_type", "")).strip().lower() in _BUSINESS_TYPES:
        return True
    aud = {a.strip().lower() for a in str(meta.get("audience", "")).split(",")}
    return bool(aud & _BUSINESS_AUDIENCE)


def gather_topics(items: list[dict], extra_topics: Iterable[str] = ()) -> list[TopicCandidate]:
    """Aggregate candidate topics from stored chunk metadata and apply the
    structural gate. Topic names are normalized to lowercase for dedup; the
    first-seen surface form is not preserved (kept simple, deterministic)."""
    cand: dict[str, TopicCandidate] = {}

    def _ensure(name: str) -> TopicCandidate | None:
        key = name.strip().lower()
        if not key:
            return None
        tc = cand.get(key)
        if tc is None:
            tc = TopicCandidate(name=key)
            cand[key] = tc
        return tc

    for item in items:
        meta = item.get("metadata") or {}
        is_code = str(meta.get("kind", "")).lower() == "code"
        business = _is_business(meta)
        for name in _concepts(meta):
            tc = _ensure(name)
            if tc is None:
                continue
            tc.chunk_ids.append(item["id"])
            if is_code:
                tc.in_code = True
            else:
                tc.in_docs = True
            if business:
                tc.business_hits += 1

    for name in extra_topics:  # graph entities widen the candidate set only
        _ensure(name)

    gated = [tc for tc in cand.values() if tc.cross_validated or tc.business_hits > 1]
    return sorted(gated, key=lambda t: t.rank_key, reverse=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_synthesis_topics.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/synthesis/__init__.py src/opendomainmcp/synthesis/topics.py tests/test_synthesis_topics.py
git commit -m "feat(synthesis): structural topic gather + gate (no tuned thresholds)"
```

---

### Task 3: Article writer + critic LLM clients

**Files:**
- Create: `src/opendomainmcp/synthesis/llm.py`
- Test: `tests/test_synthesis_llm.py`

**Interfaces:**
- Consumes: `Settings` (`llm_backend`, `extraction_model`, `request_timeout`, `max_retries`).
- Produces:
  - `parse_article(raw: str) -> dict` → `{"title": str, "body": str, "business_relevance": float}` (clamped 0–1; missing/garbage → defaults, body required else `ExtractionError`-style `SynthesisError`).
  - `parse_verdict(raw: str) -> dict` → `{"grounded": bool, "business_meaningful": bool, "note": str}`.
  - `keep_article(verdict: dict) -> bool` → True only if both flags strictly True (reject-when-uncertain).
  - `ArticleWriter` with `write(topic: str, evidence: str) -> dict` (returns `parse_article` output).
  - `ArticleCritic` with `judge(topic: str, body: str, evidence: str) -> dict` (returns `parse_verdict` output).
  - `get_article_llms(settings) -> tuple[ArticleWriter, ArticleCritic]` — anthropic/openai per `llm_backend`, lazy SDK construction (mirrors `extract.knowledge.get_extractor`). Both accept an injectable `client` for tests.
  - `SynthesisError(Exception)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_synthesis_llm.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_synthesis_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'opendomainmcp.synthesis.llm'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/opendomainmcp/synthesis/llm.py
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
    def __init__(self, model, system, max_tokens, timeout, max_retries, client=None):
        if client is None:
            import anthropic
            client = anthropic.Anthropic(timeout=timeout, max_retries=max_retries)
        self._client, self._model = client, model
        self._system, self._max_tokens = system, max_tokens

    def _call(self, user: str) -> str:
        msg = self._client.messages.create(
            model=self._model, max_tokens=self._max_tokens, system=self._system,
            messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in msg.content if b.type == "text")


class _OpenAICaller:
    def __init__(self, model, system, max_tokens, timeout, max_retries, client=None):
        if client is None:
            from openai import OpenAI
            client = OpenAI(timeout=timeout, max_retries=max_retries)
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
                 client=None, backend="anthropic"):
        self._c = _caller(backend, model=model, system=_WRITER_SYSTEM,
                          max_tokens=max_tokens, timeout=timeout,
                          max_retries=max_retries, client=client)

    def write(self, topic: str, evidence: str) -> dict:
        return parse_article(self._c._call(f"Topic: {topic}\n\nEvidence:\n{evidence}"))


class ArticleCritic:
    def __init__(self, model, max_tokens=400, timeout=60.0, max_retries=2,
                 client=None, backend="anthropic"):
        self._c = _caller(backend, model=model, system=_CRITIC_SYSTEM,
                          max_tokens=max_tokens, timeout=timeout,
                          max_retries=max_retries, client=client)

    def judge(self, topic: str, body: str, evidence: str) -> dict:
        return parse_verdict(self._c._call(
            f"Topic: {topic}\n\nArticle:\n{body}\n\nEvidence:\n{evidence}"))


def get_article_llms(settings: Settings) -> tuple[ArticleWriter, ArticleCritic]:
    kw = dict(model=settings.extraction_model, timeout=settings.request_timeout,
              max_retries=settings.max_retries, backend=settings.llm_backend)
    return ArticleWriter(**kw), ArticleCritic(**kw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_synthesis_llm.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/synthesis/llm.py tests/test_synthesis_llm.py
git commit -m "feat(synthesis): article writer + skeptical critic LLM clients"
```

---

### Task 4: `ChromaStore.sibling()` for the articles collection

**Files:**
- Modify: `src/opendomainmcp/store/chroma_store.py` (add method on `ChromaStore`)
- Test: `tests/test_store_sibling.py`

**Interfaces:**
- Consumes: an existing `ChromaStore`.
- Produces: `ChromaStore.sibling(self, collection_name: str) -> ChromaStore` — a new store over a different collection in the **same client**, sharing the same embedder/retries (no second on-disk client). Reuses existing `upsert`/`search`/`get_items` for `Article` objects (duck-typed).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_store_sibling.py — uses the conftest `store` fixture
# (FakeEmbedder + per-test EphemeralClient collection `test_<uuid>`).
from opendomainmcp.models import Article


def test_sibling_shares_client_and_isolates_collection(store):
    sib_name = f"{store.stats()['collection']}__articles"
    articles = store.sibling(sib_name)
    assert articles.stats()["collection"] == sib_name
    art = Article(title="T", topic="billing", body="Orders over $10k need sign-off",
                  source_chunk_ids=["a"], sources=["x.py:1"])
    assert articles.upsert([art]) == 1
    assert store.stats()["count"] == 0          # base collection untouched
    assert articles.stats()["count"] == 1
    hits = articles.search("sign-off orders", top_k=3, mode="vector")
    assert hits and hits[0].metadata["kind"] == "article"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store_sibling.py -v`
Expected: FAIL with `AttributeError: 'ChromaStore' object has no attribute 'sibling'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/opendomainmcp/store/chroma_store.py — add inside class ChromaStore,
# near the collection-administration section.
    def sibling(self, collection_name: str) -> "ChromaStore":
        """A store over another collection in the SAME client/embedder.

        Used to keep synthesized articles in a separate collection without
        opening a second on-disk client or reconnecting the graph store.
        """
        return ChromaStore(
            self._embedder, data_dir=None, collection_name=collection_name,
            client=self._client, max_retries=self._max_retries,
            reranker=self._reranker,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_store_sibling.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/store/chroma_store.py tests/test_store_sibling.py
git commit -m "feat(store): ChromaStore.sibling for a second collection on one client"
```

---

### Task 5: Orchestrator `synthesize_articles`

**Files:**
- Create: `src/opendomainmcp/synthesis/articles.py`
- Modify: `src/opendomainmcp/synthesis/__init__.py` (export `synthesize_articles`, `SynthesisReport`)
- Test: `tests/test_synthesis_articles.py`

**Interfaces:**
- Consumes: `ChromaStore` (has `get_items`, `search`, `sibling`, `stats`), `Settings`, optional `graph` (has `list_entities`), `TopicCandidate`/`gather_topics` (Task 2), `ArticleWriter`/`ArticleCritic`/`keep_article` (Task 3), `Article` (Task 1).
- Produces:
  - `SynthesisReport` dataclass: `topics_gated:int, articles_written:int, stored:int, rejected:list[dict], errors:list[dict]`.
  - `synthesize_articles(store, settings, *, graph=None, writer=None, critic=None, limit=None, dry_run=False) -> SynthesisReport`. Builds the articles store via `store.sibling(f"{store.stats()['collection']}__articles")`. For each gated topic (capped by `limit`): collect evidence with `store.search(topic, top_k=8, mode="hybrid")`, build a numbered evidence block split code/doc, `writer.write` → draft, `critic.judge` → verdict; if `keep_article`, build `Article` and (unless `dry_run`) `article_store.upsert([article])`. Per-topic exceptions appended to `errors`, loop continues. Default `writer`/`critic` come from `get_article_llms(settings)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_synthesis_articles.py — uses the conftest `store` fixture
from opendomainmcp.config import Settings
from opendomainmcp.models import Chunk, KnowledgeUnit
from opendomainmcp.synthesis import synthesize_articles


class _Writer:
    def write(self, topic, evidence):
        return {"title": f"About {topic}", "body": f"{topic} explained [1]",
                "business_relevance": 0.9}


class _Critic:
    def __init__(self, keep): self._keep = keep
    def judge(self, topic, body, evidence):
        return {"grounded": self._keep, "business_meaningful": self._keep, "note": ""}


def _seed(store):
    # One concept present in BOTH a code and a doc chunk → cross-validated topic.
    ku = KnowledgeUnit(summary="billing", concepts=["Billing Engine"],
                       knowledge_type="Feature")
    store.upsert([
        Chunk(text="def charge(): ...", source="billing.py", kind="code",
              start_line=1, end_line=2, knowledge=ku),
        Chunk(text="The billing engine charges orders.", source="billing.md",
              kind="text", start_line=1, end_line=1, knowledge=ku),
    ])


def _arts(store):
    return store.sibling(f"{store.stats()['collection']}__articles")


def test_synthesize_stores_only_critic_approved_articles(store):
    _seed(store)
    report = synthesize_articles(store, Settings(), writer=_Writer(),
                                 critic=_Critic(keep=True))
    assert report.topics_gated >= 1
    assert report.stored == report.articles_written >= 1
    assert _arts(store).stats()["count"] == report.stored


def test_synthesize_rejects_when_critic_fails(store):
    _seed(store)
    report = synthesize_articles(store, Settings(), writer=_Writer(),
                                 critic=_Critic(keep=False))
    assert report.stored == 0
    assert len(report.rejected) >= 1
    assert _arts(store).stats()["count"] == 0


def test_synthesize_is_idempotent(store):
    _seed(store)
    synthesize_articles(store, Settings(), writer=_Writer(), critic=_Critic(keep=True))
    synthesize_articles(store, Settings(), writer=_Writer(), critic=_Critic(keep=True))
    # Same topic + same member chunks → same Article id → no duplicate row.
    assert _arts(store).stats()["count"] == 1
```

`Settings()` is safe here: the conftest autouse fixture isolates env, and with
`writer`/`critic` injected the orchestrator never builds a real LLM from settings.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_synthesis_articles.py -v`
Expected: FAIL with `ImportError: cannot import name 'synthesize_articles'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/opendomainmcp/synthesis/articles.py
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Article
from .llm import get_article_llms, keep_article
from .topics import gather_topics


@dataclass
class SynthesisReport:
    topics_gated: int = 0
    articles_written: int = 0
    stored: int = 0
    rejected: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


def _evidence_block(results) -> tuple[str, list[str], list[str], bool, bool]:
    """Number the evidence and collect provenance. Returns
    (text, chunk_ids, sources, in_code, in_docs)."""
    lines, ids, sources = [], [], []
    in_code = in_docs = False
    for n, r in enumerate(results, 1):
        meta = r.metadata or {}
        src = meta.get("source", "?")
        loc = f"{src}:{meta.get('start_line')}" if meta.get("start_line") else src
        side = "code" if str(meta.get("kind", "")).lower() == "code" else "doc"
        in_code = in_code or side == "code"
        in_docs = in_docs or side == "doc"
        lines.append(f"[{n}] ({side}) {loc}\n{r.text}")
        ids.append(r.id)
        sources.append(loc)
    return "\n\n".join(lines), ids, sources, in_code, in_docs


def synthesize_articles(store, settings, *, graph=None, writer=None, critic=None,
                        limit=None, dry_run=False) -> SynthesisReport:
    if writer is None or critic is None:
        w, c = get_article_llms(settings)
        writer, critic = writer or w, critic or c

    items = store.get_items(limit=10_000)
    extra = []
    if graph is not None:
        extra = [e.get("name", "") for e in graph.list_entities(limit=500)]
    topics = gather_topics(items, extra_topics=extra)
    if limit is not None:
        topics = topics[:limit]

    article_store = store.sibling(f"{store.stats()['collection']}__articles")
    report = SynthesisReport(topics_gated=len(topics))

    for tc in topics:
        try:
            results = store.search(tc.name, top_k=8, mode="hybrid")
            if not results:
                continue
            evidence, ids, sources, in_code, in_docs = _evidence_block(results)
            draft = writer.write(tc.name, evidence)
            report.articles_written += 1
            verdict = critic.judge(tc.name, draft["body"], evidence)
            if not keep_article(verdict):
                report.rejected.append({"topic": tc.name, "verdict": verdict})
                continue
            article = Article(
                title=draft["title"], topic=tc.name, body=draft["body"],
                business_relevance=draft["business_relevance"],
                source_chunk_ids=ids, sources=sources,
                cross_validated=in_code and in_docs, critic_verdict=verdict,
            )
            if not dry_run:
                article_store.upsert([article])
            report.stored += 1
        except Exception as exc:  # noqa: BLE001 - Fail Loud into the report, keep going
            report.errors.append({"topic": tc.name, "error": str(exc)})
    return report
```

```python
# src/opendomainmcp/synthesis/__init__.py — append
from .articles import SynthesisReport, synthesize_articles

__all__ = ["SynthesisReport", "synthesize_articles"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_synthesis_articles.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/synthesis/articles.py src/opendomainmcp/synthesis/__init__.py tests/test_synthesis_articles.py
git commit -m "feat(synthesis): autonomous orchestrator with critic gate + idempotent store"
```

---

### Task 6: `synthesize` CLI command

**Files:**
- Modify: `src/opendomainmcp/cli.py`
- Test: `tests/test_cli.py` (add a test; match the file's existing harness)

**Interfaces:**
- Consumes: `build_context()` → `ctx.store`, `ctx.settings`, `ctx.graph`; `synthesize_articles` (Task 5).
- Produces: `opendomainmcp synthesize [--limit N] [--dry-run]` printing the report. No required arguments.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py — add (reuse this file's existing context/monkeypatch pattern)
def test_synthesize_command_prints_report(monkeypatch, capsys):
    from opendomainmcp import cli
    from opendomainmcp.synthesis import SynthesisReport

    captured = {}

    def fake_synth(store, settings, *, graph=None, limit=None, dry_run=False):
        captured["limit"] = limit
        captured["dry_run"] = dry_run
        return SynthesisReport(topics_gated=2, articles_written=2, stored=1,
                               rejected=[{"topic": "x", "verdict": {}}])

    monkeypatch.setattr(cli, "build_context", lambda **kw: _FakeCtx())
    monkeypatch.setattr("opendomainmcp.synthesis.synthesize_articles", fake_synth)
    rc = cli.main(["synthesize", "--limit", "5"])
    out = capsys.readouterr().out
    assert rc == 0
    assert captured["limit"] == 5 and captured["dry_run"] is False
    assert "Stored 1" in out and "Rejected 1" in out
```

(`_FakeCtx` exposes `.store`, `.settings`, `.graph`; model it on how other `test_cli.py` tests fake the context. If `synthesize_articles` is imported into `cli` by name, patch `cli.synthesize_articles` instead of the module path — match the import style you write in Step 3.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -k synthesize -v`
Expected: FAIL (`invalid choice: 'synthesize'` or AttributeError).

- [ ] **Step 3: Write minimal implementation**

```python
# src/opendomainmcp/cli.py — add a command handler
def _cmd_synthesize(ctx, args) -> int:
    from .synthesis import synthesize_articles

    report = synthesize_articles(
        ctx.store, ctx.settings, graph=ctx.graph,
        limit=args.limit, dry_run=args.dry_run,
    )
    print(f"Gated {report.topics_gated} topic(s); wrote {report.articles_written}.")
    print(f"Stored {report.stored} article(s). Rejected {len(report.rejected)}.")
    for r in report.rejected:
        print(f"  rejected: {r['topic']}  {r['verdict']}", file=sys.stderr)
    if report.errors:
        print(f"Errors: {len(report.errors)}", file=sys.stderr)
        for e in report.errors:
            print(f"  {e['topic']}: {e['error']}", file=sys.stderr)
    return 0
```

```python
# src/opendomainmcp/cli.py — register in build_parser(), before `return parser`
    p_synth = sub.add_parser(
        "synthesize",
        help="Autonomously synthesize business-meaning articles from indexed content",
    )
    p_synth.add_argument("--limit", type=int, default=None,
                         help="Cap topics processed (cost control); default: all gated")
    p_synth.add_argument("--dry-run", action="store_true",
                         help="Synthesize and critique but do not store")
    p_synth.set_defaults(func=_cmd_synthesize)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli.py -k synthesize -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest`
Expected: PASS (all green, no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/opendomainmcp/cli.py tests/test_cli.py
git commit -m "feat(cli): add autonomous 'synthesize' command"
```

---

## Self-Review Notes

- **Spec coverage:** Gather (Task 2) ✓; structural Gate (Task 2) ✓; Collect code/doc evidence (Task 5 `_evidence_block`) ✓; Synthesize author (Task 3 + 5) ✓; Critic verification replacing human gate (Task 3 `keep_article` + Task 5) ✓; Store in separate retrievable collection (Task 1 duck-type + Task 4 sibling + Task 5) ✓; provenance + idempotency (Task 1 `id`, tested Task 5) ✓; CLI zero-arg autonomous run + optional `--limit`/`--dry-run` (Task 6) ✓; Fail Loud / report (Task 5 `SynthesisReport`, errors list) ✓; resolved decisions — entities+concepts (Task 2 `extra_topics` from `graph.list_entities`), `ask` deferred (not built), single critic (Task 3) ✓.
- **Deferred per spec (not gaps):** UI browse page; `ask`/search preferring the articles collection.
- **Type consistency:** `gather_topics(items, extra_topics)` → `list[TopicCandidate]`; `TopicCandidate.name/chunk_ids/cross_validated`; `ArticleWriter.write→{title,body,business_relevance}`; `ArticleCritic.judge→{grounded,business_meaningful,note}`; `keep_article(verdict)→bool`; `Article(title,topic,body,business_relevance,source_chunk_ids,sources,cross_validated,critic_verdict)` with `id/text/embedding_text/metadata`; `store.sibling(name)`; `synthesize_articles(store, settings, *, graph, writer, critic, limit, dry_run)→SynthesisReport`. Names consistent across tasks.
- **Test conventions (pinned):** use the conftest `store` fixture (FakeEmbedder + per-test `EphemeralClient` collection `test_<uuid>`, `data_dir=None`) for store-backed tests; derive the articles collection in tests as `f"{store.stats()['collection']}__articles"` to match the orchestrator. `Settings()` is safe when `writer`/`critic` are injected (conftest autouse fixture isolates env). Task 3's `_FakeAnthropic` and Task 6's `_FakeCtx` are local test doubles — model `_FakeCtx` on the existing `test_cli.py` context-faking pattern, exposing `.store`, `.settings`, `.graph`.
