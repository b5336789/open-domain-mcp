"""Shared offline test fixtures.

FakeEmbedder produces deterministic vectors via a hashing bag-of-words, so token
overlap drives cosine similarity. This lets retrieval tests run with no network
and no model download.
"""

import hashlib
import math

import pytest

from opendomainmcp.embedding.base import Embedder

_DIM = 64


def _stable_hash(token: str) -> int:
    """Process-independent hash. Builtin hash() is salted per process
    (PYTHONHASHSEED), which made retrieval order — and tests that assert on
    it — flaky across runs."""
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")


class FakeEmbedder(Embedder):
    name = "fake"

    def embed(self, texts):
        vectors = []
        for text in texts:
            vec = [0.0] * _DIM
            for token in text.lower().split():
                vec[_stable_hash(token) % _DIM] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])
        return vectors

    @property
    def dim(self):
        return _DIM


@pytest.fixture(autouse=True)
def _hermetic_settings(monkeypatch):
    """Keep tests offline and reproducible: never read a developer's project
    ``.env``. Without this, a deployment ``.env`` (e.g. ODM_LLM_BACKEND=openai)
    would leak into ``Settings()`` and change test behaviour."""
    from opendomainmcp.config import Settings

    monkeypatch.setattr(
        Settings, "model_config",
        {**Settings.model_config, "env_file": None},
    )


@pytest.fixture
def fake_embedder():
    return FakeEmbedder()


@pytest.fixture
def store(fake_embedder):
    import uuid

    import chromadb

    from opendomainmcp.store import ChromaStore

    # EphemeralClient is process-shared, so isolate each test in its own collection.
    client = chromadb.EphemeralClient()
    return ChromaStore(
        fake_embedder, data_dir=None,
        collection_name=f"test_{uuid.uuid4().hex}", client=client,
    )


class FakeExtractor:
    """Deterministic offline stand-in for ClaudeExtractor."""

    def __init__(self):
        self.calls = 0

    def extract(self, text, kind, language=None):
        from opendomainmcp.models import KnowledgeUnit

        self.calls += 1
        first_word = text.strip().split()[0] if text.strip() else ""
        # Deterministic classification so view/filter tests have stable fixtures.
        knowledge_type = "Code" if kind == "code" else "Feature"
        audience = ["engineering"] if kind == "code" else ["product_manager"]
        return KnowledgeUnit(
            summary=f"about {first_word}",
            concepts=[kind],
            knowledge_type=knowledge_type,
            audience=audience,
            confidence=1.0,
            version="1.0.0",
            entities=[{"name": first_word or kind, "type": "Concept"}],
            typed_relations=[],
        )


@pytest.fixture
def fake_extractor():
    return FakeExtractor()


@pytest.fixture
def pipeline(store, fake_extractor, fake_graph):
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline

    settings = Settings(chunk_size=200, chunk_overlap=20)
    return Pipeline(store, fake_extractor, settings, graph=fake_graph)


class FakeGraphStore:
    """In-memory GraphStoreProtocol implementation for offline tests.

    ``backing`` is an optional shared dict so two instances with different
    ``collection`` values can prove filter-based isolation offline.
    """

    def __init__(self, collection: str = "domain_knowledge", backing=None):
        self._collection = collection
        # backing: {collection: {"entities": {}, "entity_chunks": {}, "edges": []}}
        self._backing = backing if backing is not None else {}

    def _slot(self):
        """Return (or lazily create) the per-collection data slot."""
        if self._collection not in self._backing:
            self._backing[self._collection] = {
                "entities": {},
                "entity_chunks": {},
                "edges": [],
                "workflow_steps": [],    # list of dicts
                "workflow_prereqs": [],  # list of dicts
                "code_functions": {},    # qualified_name -> dict
            }
        return self._backing[self._collection]

    def ensure_schema(self):
        pass

    def upsert_entities(self, entities):
        slot = self._slot()
        for e in entities:
            cur = slot["entities"].get(e.normalized_name)
            conf = max(e.confidence, cur["confidence"]) if cur else e.confidence
            # Keep existing evidence when new value is empty (mirrors Maria IF() logic).
            existing_ev = cur.get("evidence_json", "") if cur else ""
            evidence_json = e.evidence if e.evidence else existing_ev
            slot["entities"][e.normalized_name] = {
                "name": e.display_name, "normalized_name": e.normalized_name,
                "type": e.type, "confidence": conf, "evidence_json": evidence_json}
            slot["entity_chunks"].setdefault(e.normalized_name, set()).add(e.chunk_id)

    def upsert_edges(self, edges):
        slot = self._slot()
        # Dedupe by (src, dst, relation_type, chunk_id), keeping max confidence —
        # mirrors MariaDB's ON DUPLICATE KEY UPDATE confidence=GREATEST(...).
        index = {(e.src, e.dst, e.relation_type, e.chunk_id): e for e in slot["edges"]}
        for e in edges:
            key = (e.src, e.dst, e.relation_type, e.chunk_id)
            existing = index.get(key)
            if existing is None or e.confidence > existing.confidence:
                index[key] = e
        slot["edges"] = list(index.values())

    def delete_for_chunks(self, chunk_ids):
        slot = self._slot()
        ids = set(chunk_ids)
        slot["edges"] = [e for e in slot["edges"] if e.chunk_id not in ids]
        for norm in list(slot["entity_chunks"]):
            slot["entity_chunks"][norm] -= ids
            if not slot["entity_chunks"][norm]:
                del slot["entity_chunks"][norm]
                slot["entities"].pop(norm, None)
        slot["workflow_steps"] = [r for r in slot["workflow_steps"] if r["chunk_id"] not in ids]
        slot["workflow_prereqs"] = [r for r in slot["workflow_prereqs"] if r["chunk_id"] not in ids]

    def delete_collection(self, name: str):
        """Remove all data for the named collection slice."""
        self._backing.pop(name, None)

    def get_entity(self, name):
        import json
        from opendomainmcp.graph.normalize import normalize_name
        norm = normalize_name(name)
        slot = self._slot()
        row = slot["entities"].get(norm)
        if row is None:
            return None
        raw_ev = row.get("evidence_json", "")
        try:
            evidence = json.loads(raw_ev) if raw_ev else []
        except (TypeError, ValueError):
            evidence = []
        return {**{k: v for k, v in row.items() if k != "evidence_json"},
                "aliases": [], "chunk_ids": sorted(slot["entity_chunks"].get(norm, set())),
                "evidence": evidence}

    def neighbors(self, name, relation_type=None, depth=1):
        from opendomainmcp.graph.normalize import normalize_name
        depth = max(1, min(2, depth))
        root = self.get_entity(name)
        if root is None:
            return {"entity": None, "neighbors": []}
        slot = self._slot()
        seen = {root["normalized_name"]}
        frontier = [root["normalized_name"]]
        collected = []
        for _ in range(depth):
            nxt = []
            for norm in frontier:
                for e in slot["edges"]:
                    if relation_type and e.relation_type != relation_type:
                        continue
                    if e.src == norm:
                        other, direction = e.dst, "out"
                    elif e.dst == norm:
                        other, direction = e.src, "in"
                    else:
                        continue
                    if other in seen:
                        continue
                    seen.add(other)
                    nxt.append(other)
                    ent = self.get_entity(other)
                    if ent:
                        collected.append({"entity": ent, "relation_type": e.relation_type,
                                          "direction": direction})
            frontier = nxt
        return {"entity": root, "neighbors": collected}

    def upsert_workflow(self, workflow_name, chunk_id, chunk_index, steps, prerequisites):
        if not workflow_name or (not steps and not prerequisites):
            return
        from opendomainmcp.graph.normalize import normalize_name
        key = normalize_name(workflow_name)
        slot = self._slot()
        sidx = {(r["workflow_key"], r["chunk_id"], r["step_order"]): r
                for r in slot["workflow_steps"]}
        for s in steps:
            sidx[(key, chunk_id, s.step_order)] = {
                "workflow_key": key, "workflow_name": workflow_name, "chunk_id": chunk_id,
                "chunk_index": chunk_index, "step_order": s.step_order,
                "text": s.text, "precondition": s.precondition}
        slot["workflow_steps"] = list(sidx.values())
        pidx = {(r["workflow_key"], r["chunk_id"], r["prerequisite"]): r
                for r in slot["workflow_prereqs"]}
        for p in prerequisites:
            pidx[(key, chunk_id, p)] = {"workflow_key": key, "chunk_id": chunk_id,
                                        "prerequisite": p}
        slot["workflow_prereqs"] = list(pidx.values())

    def get_workflow(self, name):
        from opendomainmcp.graph.normalize import normalize_name
        key = normalize_name(name)
        slot = self._slot()
        rows = sorted((r for r in slot["workflow_steps"] if r["workflow_key"] == key),
                      key=lambda r: (r["chunk_index"], r["step_order"]))
        prereqs = []
        for r in slot["workflow_prereqs"]:
            if r["workflow_key"] == key and r["prerequisite"] not in prereqs:
                prereqs.append(r["prerequisite"])
        if not rows and not prereqs:
            return None
        display = rows[0]["workflow_name"] if rows else name
        steps = [{"order": r["step_order"], "text": r["text"],
                  "precondition": r["precondition"] or "", "chunk_id": r["chunk_id"]}
                 for r in rows]
        return {"workflow_name": display, "prerequisites": prereqs, "steps": steps}

    def list_workflows(self, q=None, limit=50):
        slot = self._slot()
        names = {}
        for r in slot["workflow_steps"]:
            if q and q.lower().strip() not in r["workflow_key"]:
                continue
            names[r["workflow_key"]] = max(names.get(r["workflow_key"], r["workflow_name"]), r["workflow_name"])
        out = [{"name": n} for _, n in sorted(names.items())]
        return out[:max(1, min(500, limit))]

    def upsert_functions(self, functions):
        table = self._slot().setdefault("code_functions", {})
        for fn in functions:
            table[fn["qualified_name"]] = dict(fn)

    def get_function(self, qualified_name):
        return self._slot().get("code_functions", {}).get(qualified_name)

    def list_entities(self, type=None, q=None, limit=50):
        import json
        slot = self._slot()
        rows = []
        for norm, row in sorted(slot["entities"].items()):
            if type and row["type"] != type:
                continue
            if q and q.lower().strip() not in norm:
                continue
            raw_ev = row.get("evidence_json", "")
            try:
                evidence = json.loads(raw_ev) if raw_ev else []
            except (TypeError, ValueError):
                evidence = []
            rows.append({"name": row["name"], "normalized_name": norm,
                         "type": row["type"], "evidence": evidence})
        return rows[:max(1, min(500, limit))]

    def delete_codegraph(self) -> None:
        """Mirror Maria semantics: remove cg:* edge/entity_chunks rows and
        all code_functions. Entities themselves are left in place (harmless;
        re-upserted on next persist)."""
        slot = self._slot()
        # Remove edges whose chunk_id starts with "cg:"
        slot["edges"] = [e for e in slot["edges"] if not e.chunk_id.startswith("cg:")]
        # Remove cg: entries from entity_chunks (leave empty sets; entity rows stay)
        for norm in list(slot["entity_chunks"]):
            slot["entity_chunks"][norm] = {
                c for c in slot["entity_chunks"][norm] if not c.startswith("cg:")
            }
            if not slot["entity_chunks"][norm]:
                del slot["entity_chunks"][norm]
        # Clear all code_functions for this collection
        slot["code_functions"] = {}


@pytest.fixture
def fake_graph():
    return FakeGraphStore()
