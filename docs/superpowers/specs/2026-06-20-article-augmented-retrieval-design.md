# Article-Augmented Retrieval (`ask` + `search`)

**Date:** 2026-06-20
**Status:** Design approved; open questions resolved. Ready for implementation plan.

## Problem

The knowledge-synthesis feature (merged in #20) produces business-meaning
**articles** and stores them in a separate, retrievable Chroma collection
(`<base>__articles`). But nothing consumes them yet — they are write-only.
`ask` (`query/rag.py`) and `search` (CLI/web) retrieve only from the chunk
collection via `store.search(...)`, so the synthesized knowledge never reaches an
answer or a search result.

This is the first of two follow-ups to make articles useful. (The second, a UI
browse page, is a separate spec.)

## Goal

Let `ask` and `search` retrieve from **both** the chunk collection and the
articles collection, fused into one ranked result set, so the higher-level
synthesized knowledge competes alongside raw chunks. Articles, being concise
business-meaning summaries, often make better grounding for an answer than a
scattered set of chunks.

## Non-Goals

- No change to the **low-level** `ChromaStore.search` — fusion lives one layer up.
- MCP **views** and the **advisor** keep their current behavior (they are
  role-filtered chunk queries; mixing articles would break their semantics).
- No change to how articles are produced (`synthesis/`).
- The UI browse page is a separate spec.

## Approach (chosen)

A small fusion helper, called by `ask` and `search`, that searches both
collections and fuses with **RRF** (`retrieval/lexical.py:rrf_fuse`, already in
the codebase). RRF is rank-based and mode-agnostic, which matters because under
`hybrid` mode the per-collection scores (dense cosine vs. RRF-fused) are not
comparable across collections — only ranks are. Behavior is gated by a setting
defaulting to on; when off or when no articles exist, retrieval is **byte-for-byte
identical to today**.

## Components

### 1. `retrieval/unified.py` — `search_unified(store, query, *, top_k, mode, settings)`

Returns `list[SearchResult]`.

- If `not settings.retrieve_include_articles` → return `store.search(query,
  top_k=top_k, mode=mode)` unchanged (today's path).
- Else build the articles sibling: `article_store = store.sibling(
  f"{store.stats()['collection']}__articles")`. If it is empty
  (`article_store.stats()["count"] == 0`) → return the plain chunk search
  (no articles to add).
- Else:
  - `chunk_hits = store.search(query, top_k=top_k, mode=mode)`
  - `article_hits = article_store.search(query, top_k=top_k, mode=mode)`
  - Fuse the two ranked id lists with `rrf_fuse([[h.id for h in chunk_hits],
    [h.id for h in article_hits]], top_k=top_k)`.
  - Resolve fused ids back to `SearchResult`s from a `{id: SearchResult}` union of
    both hit lists (articles carry `metadata["kind"] == "article"`; chunks carry
    `"code"`/`"text"`), preserving each result's own score for display, ordered by
    the fused rank, truncated to `top_k`.

This is the only place that knows about the two-collection fusion.

### 2. `query/rag.py`

- `answer_question` / `answer_question_stream` call `search_unified(store, query,
  top_k=top_k, mode=settings.search_mode, settings=settings)` instead of
  `store.search(...)`.
- `_format_sources` and `_citations` must render an article source, whose metadata
  has **no `source` key** but has `title`, `topic`, and `sources` (a
  `" | "`-joined list of `file:line`):
  - For an article (`metadata.get("kind") == "article"`): the source label is the
    `title` (e.g. `Order Approval Rule`), and the cited locations come from
    `metadata["sources"]`.
  - For a chunk: unchanged (`source` + optional `symbol`).
  - `_citations` gains a `"type"` field: `"article"` or `"chunk"`.

### 3. `search` surfaces

- CLI `cli.py:_cmd_search` calls `search_unified(...)` and, for an article hit,
  prints the `title` and an `[article]` marker (instead of the `source::symbol`
  line). Chunk output unchanged.
- The web search route (`api/app.py` / wherever `/api/search` lives) calls
  `search_unified(...)` the same way; the JSON already carries `metadata`, so the
  frontend can distinguish by `metadata.kind` with no API shape change required.
  (Confirm the exact route during planning.)

### 4. Settings (`config.py`)

- Add `retrieve_include_articles: bool = True`, `ODM_`-prefixed, **runtime-editable**
  (listed in the runtime-editable set so the web UI can toggle it). Credentials/
  data_dir remain non-editable per the existing policy.

## Data flow

```
ask/search → search_unified(store, query, top_k, mode, settings)
                ├─ flag off OR no articles → store.search(...)   [today's behavior]
                └─ else → fuse(store.search, article_store.search) via rrf_fuse
                          → list[SearchResult] (kind distinguishes provenance)
ask: _format_sources/_citations render article vs chunk; citation carries "type"
```

## Error handling (Fail Loud)

- Articles sibling collection that was never created → `sibling()` /
  `get_or_create_collection` yields an empty collection → contributes nothing, no
  crash (same as flag-off).
- An article hit missing expected metadata (`title`/`sources`) → fall back to a
  safe label (`topic` or the id), never raise inside formatting.
- No new network paths; the synthesize callable injection in `rag.py` is unchanged,
  so `ask` tests stay offline.

## Testing (business-logic, offline)

- **Fusion includes both:** seed a store with chunks and its `__articles` sibling
  with one article; `search_unified` returns both, the article appears with
  `metadata["kind"] == "article"`, and a chunk also appears — fused order respects
  `rrf_fuse`.
- **Flag off → identical:** with `retrieve_include_articles=False`, `search_unified`
  returns exactly `store.search(...)` (no article).
- **No articles → identical:** empty `__articles` sibling → returns exactly
  `store.search(...)`.
- **`ask` uses articles:** inject a fake `synthesize`; assert the formatted sources
  passed to it contain the article body, and the returned citation for the article
  has `type == "article"` with the article title as its label.
- **Formatting robustness:** `_format_sources`/`_citations` render an
  article-metadata result (no `source` key) without `KeyError`, and a chunk result
  unchanged.
- `search_unified` LLM-free and store-backed via the conftest `store` fixture +
  `.sibling(...)`; no network.

## Resolved decisions

1. **Default `retrieve_include_articles=True`** — articles participate by default;
   the runtime-editable flag lets it be turned off.
2. **Only article hits get a marker.** An article hit prints with an `[article]`
   marker + its `title`; chunk output is unchanged from today (smallest diff,
   least-surprising, keeps existing `search` tests intact).
3. **`top_k` from each, fuse to `top_k`.** Search each collection for the full
   `top_k`, RRF-fuse the two ranked id lists, truncate to `top_k`. No per-source
   quota.
