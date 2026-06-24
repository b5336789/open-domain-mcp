# Synthesize: topic-stable article ids + lightweight prune

## Problem

`synthesize_articles()` only ever `upsert`s; it never removes articles. Article id
is a content hash of `topic + sorted(source_chunk_ids)` (`models.py:175`), and
`source_chunk_ids` is whatever the top-8 hybrid search returned for that topic at
synthesis time.

Under continuous re-ingestion (the normal workflow) this accumulates stale,
duplicate articles:

- An article's id shifts whenever the top-8 retrieval set shifts — which happens not
  only when the topic's own source files change, but also when the corpus grows
  elsewhere and BM25/dense ranking moves. So a *stable* topic produces a *new* id
  (a new article) on most runs, and the previous one is never deleted.
- Rejected / no-evidence topics leave their previously-stored article in place even
  though the latest run judged them unworthy or unsupported.

Result: many co-existing, sometimes contradictory articles per topic, some citing
chunk ids that no longer exist, all fused into retrieval via `retrieval/unified.py`.

## Goal

Under continuous updates, converge toward **one current article per topic**, with no
orphaned articles referencing dead chunks. Keep the change surgical: `models.py`
(id) and `synthesis/articles.py` (loop + prune) plus the report.

## Design

### 1. Topic-stable article id (`models.py`)

```python
@staticmethod
def id_for_topic(topic: str) -> str:
    return hashlib.sha256(topic.encode("utf-8")).hexdigest()

@property
def id(self) -> str:
    return Article.id_for_topic(self.topic)
```

- Drops `source_chunk_ids` from the identity → each topic maps to exactly one
  article; re-synthesis overwrites it in place.
- `id_for_topic` is the single source of the id formula, reused by the prune step to
  address an article by topic without constructing a full `Article`.
- Update the class docstring (`models.py:157-163`), which currently says the id is a
  hash of "the topic plus its sorted member chunk ids".

### 2. Drop the old article on reject / no-evidence (`synthesis/articles.py` loop)

For a topic processed this run:

- `not results` (no evidence retrieved) → record in `report.rejected` **and**
  delete `Article.id_for_topic(tc.name)` from `article_store`.
- `not keep_article(verdict)` (critic rejected) → record in `report.rejected`
  **and** delete that topic's article.

`store.delete_item` is idempotent (returns `False` if absent), so deleting a topic
that never had an article is a no-op. Under `dry_run` the delete call is skipped but
the would-be deletion is still counted in `report.removed`.

### 3. Dead-chunk prune (after the loop)

```python
live_ids = {it["id"] for it in items}   # main collection, already paged at top
for row in <paged article_store.get_items>:
    meta = row.get("metadata") or {}
    cited = [c.strip() for c in str(meta.get("source_chunk_ids", "")).split(",")
             if c.strip()]
    if cited and any(c not in live_ids for c in cited):
        # stale: at least one cited chunk no longer exists
        if not dry_run:
            article_store.delete_item(row["id"])
        report.removed += 1
```

- **Criterion: any cited chunk missing → stale.** A broken citation makes the
  article untrustworthy; a full synthesize run regenerates it (id is now stable per
  topic). Chosen over "all cited chunks missing" (more conservative) deliberately.
- Reuses `items` (the main collection's current chunks, already paged at the top of
  `synthesize_articles`) — zero extra store queries to build `live_ids`.
- `--limit`-safe: the criterion is independent of the gated-topic list, so a valid
  topic that simply wasn't processed this run (because of `--limit`) keeps its
  still-live citations and is not pruned.
- Skipped when `dry_run` is set (counts only).

### 4. Report (`SynthesisReport`)

Add `removed: int = 0`, incremented on every deletion — reject/no-evidence deletes
(step 2) **and** dead-chunk prune (step 3). Counted even under `dry_run` (where the
delete call itself is skipped), so the report shows would-be removals. Surfaced in
the CLI report so the accounting is visible (Fail Loud).

## Known limitations (accepted)

- A topic that **stops being gated but whose source files are unchanged** keeps its
  article — all its cited chunks are still live, so the dead-chunk prune does not
  catch it. This is the deliberate trade-off for choosing the dead-chunk criterion
  over a gated-list criterion (which would mis-delete valid topics under `--limit`).
  The retained content is still valid; a full manual rebuild of the `__articles`
  collection remains available if a hard reset is wanted.

## Testing

Business-logic tests (offline, fake store) covering:

1. Re-running synthesize for the same topic with shifted retrieval results overwrites
   one article (id stable) instead of accumulating two.
2. A topic rejected by the critic on a later run has its previously-stored article
   removed.
3. A topic whose evidence retrieval returns nothing on a later run has its
   previously-stored article removed.
4. An article citing a chunk id absent from the main collection is pruned; an article
   whose citations are all live is kept.
5. `dry_run=True` performs no deletions but reports the would-be `removed` count.
6. `id_for_topic(topic) == Article(topic=topic, ...).id`.
