"""Chroma-backed vector store.

We supply embeddings ourselves (the collection has no embedding function) so the
store stays decoupled from the embedder choice. Cosine space is used, and chunk
IDs are content hashes so re-ingesting unchanged content is an idempotent upsert.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from ..embedding.base import Embedder
from ..models import Chunk, SearchResult
from ..retrieval import LexicalIndex, rrf_fuse

logger = logging.getLogger(__name__)


# Scalar metadata fields that support exact-match Chroma filtering. ``audience``
# is intentionally excluded: it is stored as a joined string (a chunk may serve
# several audiences), so it is post-filtered in the view layer instead.
_FILTER_FIELDS = ("kind", "language", "symbol", "node_type", "knowledge_type", "review_status",
                  "evidence_status")

# Valid review states; back-filling to anything else is rejected (Fail Loud).
VALID_REVIEW_STATUSES = ("approved", "pending", "rejected")


def build_where(filters: Optional[dict]) -> Optional[dict]:
    """Translate simple equality filters into a Chroma ``where`` clause."""
    if not filters:
        return None
    conds = [{k: filters[k]} for k in _FILTER_FIELDS if filters.get(k)]
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else {"$and": conds}


class ChromaStore:
    def __init__(
        self,
        embedder: Embedder,
        data_dir,
        collection_name: str = "domain_knowledge",
        client=None,
        max_retries: int = 0,
        reranker=None,
    ):
        import chromadb

        self._embedder = embedder
        self._collection_name = collection_name
        self._max_retries = max_retries
        self._reranker = reranker
        if client is not None:
            self._client = client
        else:
            self._client = chromadb.PersistentClient(path=str(data_dir))
        self._collection = self._client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"}
        )
        self._lexical = LexicalIndex()
        self._lexical_dirty = True

    def _retry(self, op, fn):
        """Run ``fn`` with bounded exponential backoff on transient failures.

        Chroma raises plain exceptions on transient issues; with ``max_retries``
        unset (0) this is a direct call, so existing behaviour is unchanged.
        """
        for attempt in range(self._max_retries + 1):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - Chroma errors are untyped
                if attempt >= self._max_retries:
                    raise
                delay = 0.5 * (2 ** attempt)
                logger.warning("chroma %s failed (%r); retry %d/%d in %.1fs",
                               op, exc, attempt + 1, self._max_retries, delay)
                time.sleep(delay)

    def upsert(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        # Identical content (e.g. repeated boilerplate) yields identical ids;
        # collapse them so a single batch never raises DuplicateIDError.
        unique = {c.id: c for c in chunks}
        chunks = list(unique.values())
        embeddings = self._embedder.embed([c.embedding_text() for c in chunks])
        self._retry("upsert", lambda: self._collection.upsert(
            ids=[c.id for c in chunks],
            embeddings=embeddings,
            documents=[c.text for c in chunks],
            metadatas=[c.metadata() for c in chunks],
        ))
        self._lexical_dirty = True
        return len(chunks)

    def search(
        self,
        query: str,
        top_k: int = 5,
        where: Optional[dict] = None,
        mode: str = "vector",
        source_contains: Optional[str] = None,
    ) -> list[SearchResult]:
        """Retrieve the most relevant chunks.

        ``mode="vector"`` is pure dense similarity. ``mode="hybrid"`` fuses dense
        and BM25 results with Reciprocal Rank Fusion, which helps exact-token
        queries (symbol names). ``source_contains`` post-filters by source path.
        """
        # Over-fetch when we need to fuse or post-filter, then trim to top_k.
        widen = mode == "hybrid" or bool(source_contains)
        n = max(top_k * 5, 30) if widen else top_k

        qvec = self._embedder.embed([query])[0]
        vres = self._retry("query", lambda: self._collection.query(
            query_embeddings=[qvec], n_results=n, where=where))
        v_ids = vres.get("ids", [[]])[0]
        v_dist = {i: d for i, d in zip(v_ids, vres.get("distances", [[]])[0])}

        if mode == "hybrid":
            self._ensure_lexical()
            l_ids = self._lexical.search(query, n)
            if where and l_ids:  # keep only lexical hits that pass the filter
                kept = set(self._collection.get(ids=l_ids, where=where, include=[])["ids"])
                l_ids = [i for i in l_ids if i in kept]
            ranked = [i for i, _ in rrf_fuse([v_ids, l_ids], top_k=max(n, top_k))]
        else:
            ranked = v_ids

        # Resolve documents/metadata for the ranked ids in one fetch, then keep
        # the fused order while applying the optional source post-filter.
        docs, metas = self._fetch(ranked)
        candidates = []
        for _id in ranked:
            if _id not in docs:
                continue
            if source_contains and source_contains not in (metas[_id].get("source") or ""):
                continue
            candidates.append(_id)

        if self._reranker is not None and candidates:
            scores = self._reranker.rerank(query, [docs[_id] for _id in candidates])
            order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
            return [
                SearchResult(
                    id=candidates[i], text=docs[candidates[i]],
                    score=round(float(scores[i]), 6), metadata=metas[candidates[i]],
                )
                for i in order[:top_k]
            ]

        results: list[SearchResult] = []
        for _id in candidates:
            if _id in v_dist:
                score = round(1.0 - v_dist[_id], 6)  # cosine similarity
            else:
                score = 0.0  # lexical-only hit (no dense distance)
            results.append(SearchResult(id=_id, text=docs[_id], score=score, metadata=metas[_id]))
            if len(results) >= top_k:
                break
        return results

    def _fetch(self, ids: list[str]):
        if not ids:
            return {}, {}
        res = self._collection.get(ids=ids, include=["documents", "metadatas"])
        docs = {i: d for i, d in zip(res["ids"], res["documents"])}
        metas = {i: (m or {}) for i, m in zip(res["ids"], res["metadatas"])}
        return docs, metas

    def _ensure_lexical(self) -> None:
        if self._lexical_dirty:
            res = self._collection.get(include=["documents"])
            self._lexical.build(res["ids"], res["documents"])
            self._lexical_dirty = False

    def get_items(
        self, limit: int = 50, offset: int = 0, where: Optional[dict] = None
    ) -> list[dict]:
        res = self._collection.get(
            limit=limit, offset=offset, where=where,
            include=["documents", "metadatas"],
        )
        items = []
        for i, _id in enumerate(res["ids"]):
            items.append(
                {"id": _id, "text": res["documents"][i], "metadata": res["metadatas"][i] or {}}
            )
        return items

    def get_item(self, item_id: str) -> Optional[dict]:
        res = self._collection.get(ids=[item_id], include=["documents", "metadatas"])
        if not res["ids"]:
            return None
        return {
            "id": res["ids"][0],
            "text": res["documents"][0],
            "metadata": res["metadatas"][0] or {},
        }

    def update_metadata(self, item_id: str, metadata: dict) -> bool:
        if self.get_item(item_id) is None:
            return False
        self._collection.update(ids=[item_id], metadatas=[metadata])
        return True

    def backfill_review_status(self, status: str = "approved", *, only_missing: bool = True) -> int:
        """Stamp ``review_status`` onto stored chunks, returning the count updated.

        With ``only_missing=True`` (default) only chunks whose ``review_status``
        metadata is absent or empty are touched -- this back-fills data ingested
        before the review feature existed. With ``only_missing=False`` every
        chunk is re-stamped. Fail Loud on an invalid ``status``.
        """
        if status not in VALID_REVIEW_STATUSES:
            raise ValueError(
                f"invalid review status {status!r}; expected one of {VALID_REVIEW_STATUSES}"
            )

        res = self._collection.get(include=["metadatas"])
        ids = res["ids"]
        metas = res["metadatas"]

        update_ids: list[str] = []
        update_metas: list[dict] = []
        for _id, meta in zip(ids, metas):
            meta = meta or {}
            if only_missing and meta.get("review_status"):
                continue
            update_ids.append(_id)
            # Immutable update: build a new metadata dict rather than mutating.
            update_metas.append({**meta, "review_status": status})

        if update_ids:
            self._retry(
                "update",
                lambda: self._collection.update(ids=update_ids, metadatas=update_metas),
            )
        return len(update_ids)

    def delete_item(self, item_id: str) -> bool:
        if self.get_item(item_id) is None:
            return False
        self._collection.delete(ids=[item_id])
        self._lexical_dirty = True
        return True

    def get_ids_for_source(self, source: str) -> set[str]:
        """All chunk ids currently stored for a given source file."""
        res = self._collection.get(where={"source": source}, include=[])
        return set(res["ids"])

    def delete_ids(self, ids) -> int:
        ids = list(ids)
        if ids:
            self._collection.delete(ids=ids)
            self._lexical_dirty = True
        return len(ids)

    def list_sources(self) -> list[dict]:
        """Aggregate stored chunks by their ``source`` for the source registry.

        Returns one dict per source with the chunk count, the sorted set of
        distinct ``kind`` values, and a review-status breakdown. Chunks with no
        ``source`` metadata are grouped under an empty-string key, and an absent
        or unrecognised ``review_status`` counts as ``unset``.
        """
        res = self._collection.get(include=["metadatas"])
        agg: dict[str, dict] = {}
        for meta in res["metadatas"]:
            meta = meta or {}
            source = meta.get("source") or ""
            entry = agg.get(source)
            if entry is None:
                entry = {
                    "source": source,
                    "chunks": 0,
                    "kinds": set(),
                    "review": {"approved": 0, "pending": 0, "rejected": 0, "unset": 0},
                }
                agg[source] = entry
            entry["chunks"] += 1
            kind = meta.get("kind")
            if kind:
                entry["kinds"].add(kind)
            status = meta.get("review_status")
            bucket = status if status in VALID_REVIEW_STATUSES else "unset"
            entry["review"][bucket] += 1
        return [
            {**entry, "kinds": sorted(entry["kinds"])}
            for entry in sorted(agg.values(), key=lambda e: e["source"])
        ]

    def delete_by_source(self, source: str) -> int:
        """Delete every chunk whose ``source`` equals ``source``.

        Returns the number of chunks removed. Fail Loud on an empty source so a
        caller cannot accidentally target the unsourced bucket.
        """
        if not source:
            raise ValueError("source must be a non-empty string")
        ids = self.get_ids_for_source(source)
        return self.delete_ids(ids)

    def get_all_sources(self) -> set[str]:
        """Distinct source paths present in the collection (used by dir sync)."""
        res = self._collection.get(include=["metadatas"])
        return {
            m["source"] for m in res["metadatas"] if m and m.get("source")
        }

    def stats(self) -> dict:
        return {
            "collection": self._collection_name,
            "count": self._collection.count(),
            "embedder": self._embedder.name,
            "dim": self._embedder.dim,
        }

    def clear(self) -> None:
        self._client.delete_collection(self._collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name, metadata={"hnsw:space": "cosine"}
        )
        self._lexical_dirty = True

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

    # -- collection administration (shared client) ----------------------
    def list_collections(self) -> list[dict]:
        out = []
        for c in self._client.list_collections():
            name = getattr(c, "name", c)
            # Hide internal siblings: article siblings back the Articles view;
            # chain siblings back chain-analysis retrieval.  Neither is a
            # user-selectable knowledge base.
            if name.endswith(("__articles", "__chains")):
                continue
            out.append({"name": name, "count": self._client.get_collection(name).count()})
        return sorted(out, key=lambda d: d["name"])

    def create_collection(self, name: str) -> None:
        self._client.get_or_create_collection(
            name=name, metadata={"hnsw:space": "cosine"}
        )

    def drop_collection(self, name: str) -> None:
        self._client.delete_collection(name)
