# Rule Consensus & Cross-Validation (Enhancement #5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A post-ingest consensus pass that finds the same business rule stated in multiple places (embedding shortlist + graph signals → LLM adjudication with a content-hash verdict cache), merges `same` groups into canonical `kind='rule'` items with derived trust tiers (cross-layer = high; conflicting = conflicted → review queue), and makes retrieval prefer canonical rules over their members.

**Architecture:** `consensus/units.py` collects rule units — chunk evidence claims (main collection) and chain rules (`__chains` sibling) — each carrying its #2 evidence and a layer tag (db/service/frontend/docs/chain). `consensus/pairing.py` shortlists candidate pairs via embedding cosine (`store._embedder`) OR graph signals (same chain membership; shared graph entity via a bounded entity scan). `consensus/adjudicate.py` wraps the LLM (ChainAnalyzer-style `complete` callable) with an atomic on-disk verdict cache keyed by claim-pair content hash — re-runs and incremental ingests skip judged pairs for free. `consensus/merge.py` union-finds `same` groups into canonical `RuleItem`s (main collection, so the Review queue and hybrid retrieval work unchanged) with trust tiers; conflicts mark items `conflicted` + `review_status="pending"` and add `conflicts` edges between rule entities in the graph. `consensus/run.py` orchestrates and prunes stale rules against expected ids. Retrieval: post-fusion suppression drops member chunks when their canonical rule is also in the results (`retrieve_prefer_rules`).

**Tech Stack:** Python ≥ 3.11; pytest offline (FakeEmbedder bag-of-words drives cosine; LLM faked).

**Spec:** `docs/superpowers/specs/2026-07-06-consensus-cross-validation-design.md`

## Global Constraints

- All tests offline; LLM always a fake `complete` callable; embeddings via the injected store embedder (FakeEmbedder in tests).
- RuleUnit key (fixed): `f"{origin}:{origin_id}:{index}"` where origin ∈ {"chunk","chain"}. Layer tags (fixed): plsql→`db`; java/vbnet/csharp→`service`; javascript/typescript/tsx→`frontend`; text/docs→`docs`; chain units→`chain`.
- Canonical rule id (fixed): `sha256("rule:" + statement.lower().strip())`; statement = the group's longest claim (tie → lexicographically first). Stored in the MAIN collection with `kind="rule"` (Review + retrieval integration for free).
- Trust tiers (fixed): `high` = corroborating units span ≥ 2 distinct layers (excluding `chain`) OR ≥ 2 units incl. a chain unit and a non-chain unit; `normal` = single source/layer; `conflicted` = any member has a `conflict` verdict with another unit. Conflicted ⇒ `review_status="pending"` regardless of review_mode; others follow review_mode.
- Verdict values (fixed): `same` | `related` | `conflict`. Cache file `<data_dir>/.consensus/verdicts.json`, atomic write (tmp + os.replace, mirroring checkpoint.py), key = `sha256("\x00".join(sorted([claimA.lower().strip(), claimB.lower().strip()])))`.
- New settings: `consensus_similarity_threshold: float = 0.80` (env-only), `retrieve_prefer_rules: bool = True` (EDITABLE). Document both in `.env.example`.
- Fail Loud: adjudication failures land in result `errors` (pair skipped, NOT treated as `same`); total-failure runs (0 verdicts, >0 candidates) skip rule upserts AND stale pruning; counts always reported.
- Bounded work: entity scan capped at 1000 entities (log when capped); pairs deduped; self-pairs and same-origin-same-id pairs excluded.
- Everything additive: no rules produced ⇒ all surfaces behave as today.

## Parallel execution note

Waves: **[T1] → [T2, T3, T4, T6 parallel — disjoint files] → [T5] → [T7]**. Parallel implementers `git add` only their own files; retry commit on index.lock.

---

### Task 1: `RuleItem` model + rule-unit collection

**Files:**
- Modify: `src/opendomainmcp/models.py` (add `RuleItem` after `ChainItem`)
- Create: `src/opendomainmcp/consensus/__init__.py`, `src/opendomainmcp/consensus/units.py`
- Test: `tests/test_consensus_units.py`

**Interfaces:**
- `RuleItem` (duck-typed store contract like Article/ChainItem):

```python
@dataclass
class RuleItem:
    statement: str
    trust: str = "normal"              # high | normal | conflicted
    corroborations: int = 1
    layers: list[str] = field(default_factory=list)
    member_keys: list[str] = field(default_factory=list)    # RuleUnit keys
    member_chunk_ids: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)        # "file:start-end" / entry names
    evidence: list[dict] = field(default_factory=list)      # union of member evidence entries
    evidence_status: str = ""
    review_status: str = "approved"

    @staticmethod
    def id_for_statement(statement: str) -> str: ...  # sha256("rule:" + lower/strip)
    @property
    def id(self) -> str: ...
    @property
    def text(self) -> str: ...          # statement + "\nCorroborated by N sources: ..." summary line
    def embedding_text(self) -> str: ...
    def metadata(self) -> dict: ...     # kind="rule", trust, corroborations,
                                        # layers/member_keys/member_chunk_ids/sources joined,
                                        # evidence JSON + evidence_status, review_status
```

- `consensus/units.py`:

```python
@dataclass
class RuleUnit:
    key: str                 # "{origin}:{origin_id}:{index}"
    claim: str
    origin: str              # chunk | chain
    origin_id: str           # chunk id / chain item id
    layer: str               # db|service|frontend|docs|chain
    source: str              # file path / chain entry
    chunk_ids: list[str]     # [chunk_id] for chunk units; member_chunk_ids for chain units
    evidence: list[dict]     # the entries backing this claim

def collect_rule_units(store, page_size: int = 200) -> list[RuleUnit]
```

Collection: paginate `store.get_items` (main collection) until exhausted; for items with parsed evidence (use `parse_evidence_field`), one unit per evidence entry with a non-blank claim (`layer` from `language`/`kind` metadata per the fixed mapping; claimless entries skipped). Then the `__chains` sibling (skip when `hasattr(store, "sibling")` is false or count 0): one unit per rule string in metadata `rules` (split " | "), evidence = the chain's parsed evidence entries whose claim equals the rule (fallback: all entries), `chunk_ids` = parsed member_chunk_ids, layer `chain`. Deterministic order (items in pagination order, entries in stored order).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_consensus_units.py
"""RuleItem model + rule-unit collection (enhancement #5)."""

import json

from opendomainmcp.models import ChainItem, Chunk, KnowledgeUnit, RuleItem
from opendomainmcp.consensus.units import collect_rule_units

EV = {"claim": "amount must not be negative", "quote": "if (amt < 0)",
      "source": "Billing.java", "start_line": 5, "end_line": 5, "verified": True}


def test_rule_item_contract():
    r = RuleItem(statement="Amount must not be negative", trust="high",
                 corroborations=2, layers=["service", "db"],
                 member_chunk_ids=["c1", "c2"], sources=["A.java:1-5"],
                 evidence=[EV], evidence_status="verified")
    assert r.id == RuleItem.id_for_statement("Amount must not be negative")
    assert r.id == RuleItem.id_for_statement("  amount must not be NEGATIVE ")
    meta = r.metadata()
    assert meta["kind"] == "rule" and meta["trust"] == "high"
    assert meta["corroborations"] == 2
    assert json.loads(meta["evidence"])[0]["claim"] == EV["claim"]
    assert all(not isinstance(v, (list, dict)) for v in meta.values())
    assert "Amount must not be negative" in r.text and "2" in r.text


def test_collect_units_from_chunks_and_chains(store):
    k = KnowledgeUnit(summary="S", knowledge_type="Code", confidence=0.9,
                      evidence=[EV], evidence_status="verified")
    store.upsert([Chunk(text="if (amt < 0) throw", source="Billing.java",
                        kind="code", language="java", knowledge=k)])
    chains = store.sibling(f"{store.stats()['collection']}__chains")
    chains.upsert([ChainItem(entry="api.charge", title="T", body="B",
                             rules=["amount must not be negative"],
                             member_chunk_ids=["c9"],
                             evidence=[EV], evidence_status="verified")])

    units = collect_rule_units(store)
    origins = {u.origin for u in units}
    assert origins == {"chunk", "chain"}
    chunk_unit = next(u for u in units if u.origin == "chunk")
    assert chunk_unit.layer == "service" and chunk_unit.claim == EV["claim"]
    assert chunk_unit.evidence and chunk_unit.chunk_ids
    chain_unit = next(u for u in units if u.origin == "chain")
    assert chain_unit.layer == "chain" and chain_unit.chunk_ids == ["c9"]
    assert chain_unit.source == "api.charge"


def test_collect_units_skips_claimless_and_paginates(store):
    from opendomainmcp.models import Chunk, KnowledgeUnit

    for i in range(7):
        k = KnowledgeUnit(summary="S", evidence=[
            {"claim": f"rule {i}", "quote": f"q{i}", "source": "a.sql",
             "start_line": 1, "end_line": 1, "verified": True}])
        store.upsert([Chunk(text=f"q{i} body", source=f"p{i}.sql", kind="code",
                            language="plsql", knowledge=k)])
    store.upsert([Chunk(text="no evidence here", source="plain.md", kind="text")])

    units = collect_rule_units(store, page_size=3)   # forces pagination
    assert len(units) == 7
    assert all(u.layer == "db" for u in units)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_consensus_units.py -v`
Expected: FAIL — no `RuleItem` / no `opendomainmcp.consensus`

- [ ] **Step 3: Implement** per Interfaces (mirror ChainItem's metadata style exactly; `consensus/__init__.py` gets a module docstring describing the pass). Layer mapping as a module constant `LAYER_BY_LANGUAGE` + `docs` fallback for kind=="text", `service` fallback for other code.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_consensus_units.py tests/test_models.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/models.py src/opendomainmcp/consensus tests/test_consensus_units.py
git commit -m "feat: RuleItem model and consensus rule-unit collection"
```

---

### Task 2: Candidate pairing

**Files:**
- Create: `src/opendomainmcp/consensus/pairing.py`
- Test: `tests/test_consensus_pairing.py`

**Interfaces:**
- Consumes: `RuleUnit` (T1); `store._embedder` (Embedder protocol: `embed(texts) -> list[vec]`); graph store (optional).
- Produces:

```python
@dataclass
class CandidatePair:
    a: RuleUnit
    b: RuleUnit
    signal: str      # "embedding" | "chain" | "entity"
    similarity: float = 0.0

def find_candidates(units: list[RuleUnit], embedder, graph=None,
                    threshold: float = 0.80,
                    entity_scan_limit: int = 1000) -> list[CandidatePair]
```

- Embedding signal: embed all claims in one batch; cosine (pure-python dot/norm helper `_cosine`) over all pairs `i<j`; ≥ threshold ⇒ candidate. (N units from a corpus pass is small — thousands at most; O(N²) float math is fine; note it in the docstring.)
- Chain signal: two units are candidates when one is a chain unit and the other's chunk_ids intersect its member chunk_ids, or two chunk units both appear in some chain unit's member ids (build a chunk_id → chain-unit index first).
- Entity signal (graph optional, `None`/Null-safe): `graph.list_entities(limit=entity_scan_limit)`; for each, `get_entity(name)["chunk_ids"]`; two units whose chunk_ids fall in the same entity's chunk set ⇒ candidate. Log a warning when the scan hits the cap. Wrap the whole scan in try/except (NullGraphStore returns empties — verify its actual behavior and handle).
- Dedup by unordered unit-key pair; exclude self and same-key pairs; prefer recording the strongest signal (embedding similarity recorded when both signals fire). Deterministic output order (sorted by key pair).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_consensus_pairing.py
"""Candidate pairing: embedding + chain + entity signals (enhancement #5)."""

from opendomainmcp.consensus.pairing import find_candidates
from opendomainmcp.consensus.units import RuleUnit


class TwoBucketEmbedder:
    """'negative' claims -> [1,0]; everything else -> [0,1]."""

    def embed(self, texts):
        return [[1.0, 0.0] if "negative" in t else [0.0, 1.0] for t in texts]

    @property
    def dim(self):
        return 2


def _unit(key, claim, chunk_ids, layer="service", origin="chunk"):
    return RuleUnit(key=key, claim=claim, origin=origin, origin_id=key,
                    layer=layer, source="s", chunk_ids=chunk_ids, evidence=[])


def test_embedding_signal_pairs_similar_claims():
    units = [_unit("chunk:a:0", "amount must not be negative", ["ca"]),
             _unit("chunk:b:0", "order amount cannot be negative", ["cb"]),
             _unit("chunk:c:0", "orders ship within two days", ["cc"])]
    pairs = find_candidates(units, TwoBucketEmbedder(), graph=None, threshold=0.9)
    keys = {(p.a.key, p.b.key) for p in pairs}
    assert ("chunk:a:0", "chunk:b:0") in keys
    assert not any("chunk:c:0" in k for pair in keys for k in pair)
    p = pairs[0]
    assert p.signal == "embedding" and p.similarity >= 0.9


def test_chain_signal_pairs_chunk_with_chain_unit():
    chain_unit = _unit("chain:x:0", "totally different wording", ["ca", "cb"],
                       layer="chain", origin="chain")
    chunk_unit = _unit("chunk:a:0", "amount rule", ["ca"])
    pairs = find_candidates([chain_unit, chunk_unit], TwoBucketEmbedder(),
                            graph=None, threshold=0.99)
    assert len(pairs) == 1 and pairs[0].signal == "chain"


def test_entity_signal_via_fake_graph(fake_graph):
    from opendomainmcp.graph.models import Entity

    fake_graph.upsert_entities([
        Entity(normalized_name="billing", display_name="Billing",
               type="Concept", chunk_id="ca"),
        Entity(normalized_name="billing", display_name="Billing",
               type="Concept", chunk_id="cb"),
    ])
    units = [_unit("chunk:a:0", "first wording", ["ca"]),
             _unit("chunk:b:0", "second phrasing", ["cb"])]
    pairs = find_candidates(units, TwoBucketEmbedder(), graph=fake_graph,
                            threshold=0.99)
    assert len(pairs) == 1 and pairs[0].signal == "entity"


def test_dedup_and_determinism():
    a = _unit("chunk:a:0", "no negative amounts", ["ca"])
    b = _unit("chunk:b:0", "negative amounts forbidden", ["ca"])  # also same chunk? no — chain-less
    pairs1 = find_candidates([a, b], TwoBucketEmbedder(), threshold=0.9)
    pairs2 = find_candidates([b, a], TwoBucketEmbedder(), threshold=0.9)
    assert len(pairs1) == len(pairs2) == 1
    assert (pairs1[0].a.key, pairs1[0].b.key) == (pairs2[0].a.key, pairs2[0].b.key)
```

- [ ] **Step 2: Run tests to verify they fail** (module not found)

- [ ] **Step 3: Implement** per Interfaces.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_consensus_pairing.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/consensus/pairing.py tests/test_consensus_pairing.py
git commit -m "feat: consensus candidate pairing via embedding and graph signals"
```

---

### Task 3: LLM adjudication + verdict cache

**Files:**
- Create: `src/opendomainmcp/consensus/adjudicate.py`
- Test: `tests/test_consensus_adjudicate.py`

**Interfaces:**
- Consumes: `parse_llm_json` (extract.knowledge), the `_default_complete(settings)` pattern from `codegraph/analyze_llm.py` (import it — it is module-level there; if private-name import is unpalatable to the reviewer, factor later at final review).
- Produces:

```python
VERDICTS = ("same", "related", "conflict")

class RuleAdjudicator:
    def __init__(self, settings, complete=None, cache_path: Optional[Path] = None):
        # cache_path default: Path(settings.data_dir) / ".consensus" / "verdicts.json"
    def judge(self, claim_a: str, quotes_a: list[str],
              claim_b: str, quotes_b: list[str]) -> str:
        # returns a VERDICT; caches by pair hash; cache hit skips the LLM
    @staticmethod
    def pair_key(claim_a: str, claim_b: str) -> str  # order-independent sha256
    @property
    def cache_hits(self) -> int
    def save(self) -> None   # atomic tmp+os.replace, mirrors checkpoint.py
```

- `_SYSTEM` (fixed): asks for ONLY JSON `{"verdict": "same" | "related" | "conflict", "reason": "..."}` — `same` = the two statements express the SAME business rule; `related` = same topic, different rules; `conflict` = contradictory constraints (e.g. ">= 0" vs "> 0"). User content: both claims with up to 2 supporting quotes each.
- Unknown verdict strings from the LLM normalize to `related` (safe middle — never merges, never flags). LLM exceptions propagate (caller records; pair skipped).
- Cache loads lazily at construction (missing/corrupt file ⇒ empty dict, warning on corrupt); `judge` writes to the in-memory dict; `save()` persists (caller decides when).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_consensus_adjudicate.py
"""LLM rule adjudication with content-hash verdict cache (enhancement #5)."""

import json

import pytest

from opendomainmcp.config import Settings
from opendomainmcp.consensus.adjudicate import RuleAdjudicator


def _adj(tmp_path, replies):
    calls = {"n": 0}

    def fake(system, user):
        calls["n"] += 1
        return json.dumps(replies[min(calls["n"] - 1, len(replies) - 1)])

    a = RuleAdjudicator(Settings(), complete=fake,
                        cache_path=tmp_path / "verdicts.json")
    return a, calls


def test_judge_parses_verdict_and_caches(tmp_path):
    adj, calls = _adj(tmp_path, [{"verdict": "same", "reason": "identical"}])
    v1 = adj.judge("amount >= 0", ["if (amt < 0)"], "no negative amounts", ["CHECK amt >= 0"])
    v2 = adj.judge("no negative amounts", ["CHECK amt >= 0"], "amount >= 0", ["if (amt < 0)"])
    assert v1 == v2 == "same"
    assert calls["n"] == 1 and adj.cache_hits == 1   # order-independent cache key


def test_cache_persists_across_instances(tmp_path):
    adj, calls = _adj(tmp_path, [{"verdict": "conflict", "reason": "boundary"}])
    adj.judge("a >= 0", [], "a > 0", [])
    adj.save()

    def boom(system, user):
        raise AssertionError("must not be called on cache hit")

    adj2 = RuleAdjudicator(Settings(), complete=boom,
                           cache_path=tmp_path / "verdicts.json")
    assert adj2.judge("a > 0", [], "a >= 0", []) == "conflict"


def test_unknown_verdict_normalizes_to_related(tmp_path):
    adj, _ = _adj(tmp_path, [{"verdict": "maybe?", "reason": ""}])
    assert adj.judge("x", [], "y", []) == "related"


def test_llm_failure_propagates(tmp_path):
    def broken(system, user):
        raise RuntimeError("llm down")

    adj = RuleAdjudicator(Settings(), complete=broken,
                          cache_path=tmp_path / "v.json")
    with pytest.raises(RuntimeError):
        adj.judge("x", [], "y", [])


def test_corrupt_cache_tolerated(tmp_path):
    p = tmp_path / "verdicts.json"
    p.write_text("{broken", encoding="utf-8")
    adj, _ = _adj(tmp_path, [{"verdict": "same", "reason": ""}])
    assert adj.judge("x", [], "y", []) == "same"
```

- [ ] **Step 2: Run tests to verify they fail** (module not found)

- [ ] **Step 3: Implement** per Interfaces (system prompt as module constant; quotes truncated to ~300 chars each in the user message).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_consensus_adjudicate.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/consensus/adjudicate.py tests/test_consensus_adjudicate.py
git commit -m "feat: rule adjudicator with atomic content-hash verdict cache"
```

---

### Task 4: Merge, trust tiers, conflict marking

**Files:**
- Create: `src/opendomainmcp/consensus/merge.py`
- Test: `tests/test_consensus_merge.py`

**Interfaces:**
- Consumes: `RuleUnit`, `RuleItem` (T1); verdict tuples.
- Produces:

```python
def merge_groups(units: list[RuleUnit],
                 verdicts: list[tuple[str, str, str]],   # (key_a, key_b, verdict)
                 review_mode: bool = False) -> list[RuleItem]
```

- Union-find over `same` edges (by unit key). Singleton groups (units in no `same` pair) produce NO RuleItem — canonical rules exist only where corroboration or conflict exists; but a singleton that has a `conflict` verdict DOES produce a conflicted single-member rule (it needs review visibility).
- Per group: statement = longest claim (tie → lexicographic min); corroborations = len(group); layers = sorted distinct member layers; member_keys/member_chunk_ids/sources = deduped unions (stable order); evidence = concatenated member evidence (deduped by (claim, quote, source)); evidence_status combined by entry flags (same rule as #2).
- Trust: `conflicted` if any member key appears in a `conflict` verdict (with any unit, in-group or cross-group); else `high` if ≥ 2 distinct non-`chain` layers, or (≥ 2 members and layers include `chain` plus ≥ 1 non-chain layer); else `normal`.
- review_status: `pending` when conflicted OR review_mode; else `approved`.
- Deterministic output (sorted by rule id).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_consensus_merge.py
"""Union-find merge, trust tiers, conflict marking (enhancement #5)."""

from opendomainmcp.consensus.merge import merge_groups
from opendomainmcp.consensus.units import RuleUnit


def _u(key, claim, layer, chunk_ids=None, source="s"):
    return RuleUnit(key=key, claim=claim, origin=key.split(":")[0],
                    origin_id=key.split(":")[1], layer=layer, source=source,
                    chunk_ids=chunk_ids or [], evidence=[
                        {"claim": claim, "quote": f"q-{key}", "source": source,
                         "start_line": 1, "end_line": 1, "verified": True}])


def test_cross_layer_same_group_is_high_trust():
    units = [_u("chunk:a:0", "amount must not be negative", "service", ["ca"]),
             _u("chunk:b:0", "order amount cannot be negative at all", "db", ["cb"])]
    rules = merge_groups(units, [("chunk:a:0", "chunk:b:0", "same")])
    assert len(rules) == 1
    r = rules[0]
    assert r.trust == "high" and r.corroborations == 2
    assert r.statement == "order amount cannot be negative at all"  # longest claim
    assert set(r.layers) == {"db", "service"}
    assert set(r.member_chunk_ids) == {"ca", "cb"}
    assert len(r.evidence) == 2 and r.evidence_status == "verified"
    assert r.review_status == "approved"


def test_same_layer_group_is_normal():
    units = [_u("chunk:a:0", "rule one wording", "service"),
             _u("chunk:b:0", "rule one longer wording", "service")]
    rules = merge_groups(units, [("chunk:a:0", "chunk:b:0", "same")])
    assert rules[0].trust == "normal"


def test_conflict_marks_conflicted_and_pending():
    units = [_u("chunk:a:0", "amount must be >= 0", "service"),
             _u("chunk:b:0", "amount must be > 0", "db")]
    rules = merge_groups(units, [("chunk:a:0", "chunk:b:0", "conflict")])
    # conflict without same ⇒ two single-member conflicted rules
    assert len(rules) == 2
    assert all(r.trust == "conflicted" and r.review_status == "pending"
               for r in rules)


def test_singletons_without_verdicts_produce_nothing():
    units = [_u("chunk:a:0", "lonely rule", "docs")]
    assert merge_groups(units, []) == []


def test_related_does_not_merge():
    units = [_u("chunk:a:0", "rule A", "service"),
             _u("chunk:b:0", "rule B", "db")]
    assert merge_groups(units, [("chunk:a:0", "chunk:b:0", "related")]) == []


def test_review_mode_marks_pending():
    units = [_u("chunk:a:0", "r one", "service"), _u("chunk:b:0", "r one long", "db")]
    rules = merge_groups(units, [("chunk:a:0", "chunk:b:0", "same")],
                         review_mode=True)
    assert rules[0].review_status == "pending"
```

- [ ] **Step 2: Run tests to verify they fail** (module not found)

- [ ] **Step 3: Implement** per Interfaces (small internal union-find; no external deps).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_consensus_merge.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/consensus/merge.py tests/test_consensus_merge.py
git commit -m "feat: consensus merge with trust tiers and conflict marking"
```

---

### Task 6 (parallel with T2–T4): Retrieval preference + settings + citations

**Files:**
- Modify: `src/opendomainmcp/retrieval/unified.py`, `src/opendomainmcp/query/rag.py`, `src/opendomainmcp/config.py`, `.env.example`
- Test: `tests/test_retrieval_unified.py` (append), `tests/test_rag.py` (append)

**Interfaces:**
- `config.py`: `retrieve_prefer_rules: bool = True` (+ EDITABLE_FIELDS, after `retrieve_include_chains`) and `consensus_similarity_threshold: float = 0.80` (env-only, after `codegraph_context_chars`); both documented in `.env.example`.
- `search_unified`: after the fusion tail (also on the single-list early return — apply uniformly just before returning), when `retrieve_prefer_rules`: collect member chunk ids from hits with `metadata["kind"] == "rule"` (parse `member_chunk_ids` CSV) and drop other hits whose id is in that set (rules rank on their own merits — no boosting).
- `rag.py`: `_source_label` for kind=="rule" → `metadata.get("statement") or the text's first line`... simpler: RuleItem metadata carries no `title`; use `meta.get("trust")`-agnostic label: the statement is the item text's first line — label = first 80 chars of text? Deterministic and simple: `_source_label` returns `meta.get("statement", "")` — ADD `statement` to RuleItem.metadata() in T1? T1 already froze metadata; instead RuleItem.metadata() DOES include the statement: coordinate — this task adds nothing to models; it reads `meta.get("statement")`. (T1's metadata must include `"statement"`; T1's test asserts flat scalars only — the implementer of THIS task should verify `statement` is present in RuleItem.metadata() and, if T1 omitted it, add it here with a test.) `_citations`: kind=="rule" → `type_="rule"`, `source=statement`, plus `"trust"` key.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_retrieval_unified.py` (follow its fixtures):

```python
def test_prefer_rules_suppresses_member_chunks(store):
    from opendomainmcp.config import Settings
    from opendomainmcp.models import Chunk, KnowledgeUnit, RuleItem
    from opendomainmcp.retrieval import search_unified

    member = Chunk(text="if (amt < 0) throw new Error('negative amount')",
                   source="Billing.java", kind="code", language="java")
    store.upsert([member])
    rule = RuleItem(statement="amount must not be negative",
                    member_chunk_ids=[member.id],
                    sources=["Billing.java:1-1"])
    store.upsert([rule])

    hits = search_unified(store, "negative amount rule", top_k=5,
                          settings=Settings(retrieve_prefer_rules=True,
                                            retrieve_include_articles=False,
                                            retrieve_include_chains=False))
    kinds = [h.metadata.get("kind") for h in hits]
    assert "rule" in kinds
    assert member.id not in [h.id for h in hits]

    hits_off = search_unified(store, "negative amount rule", top_k=5,
                              settings=Settings(retrieve_prefer_rules=False,
                                                retrieve_include_articles=False,
                                                retrieve_include_chains=False))
    assert member.id in [h.id for h in hits_off]
```

Append to `tests/test_rag.py`:

```python
def test_rule_citation_shape():
    from opendomainmcp.models import SearchResult
    from opendomainmcp.query.rag import _citations, _source_label

    meta = {"kind": "rule", "statement": "amount must not be negative",
            "trust": "high"}
    r = SearchResult(id="r1", text="amount must not be negative\n...", score=0.9,
                     metadata=meta)
    assert _source_label(r) == "amount must not be negative"
    cite = _citations([r])[0]
    assert cite["type"] == "rule" and cite["trust"] == "high"
```

(adapt SearchResult construction to its real signature; if RuleItem.metadata() lacks `statement`, add it in models.py with a one-line test in test_consensus_units.py — coordinate note above.)

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement** per Interfaces.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_retrieval_unified.py tests/test_rag.py tests/test_config.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/retrieval/unified.py src/opendomainmcp/query/rag.py src/opendomainmcp/config.py .env.example tests/test_retrieval_unified.py tests/test_rag.py
git commit -m "feat: retrieval prefers canonical rules; rule citations with trust"
```

---

### Task 5: Orchestrator `consensus/run.py`

**Files:**
- Create: `src/opendomainmcp/consensus/run.py`
- Test: `tests/test_consensus_run.py`

**Interfaces:**
- Consumes: T1–T4 (+ graph store for conflict edges).
- Produces:

```python
def run_consensus(store, settings, graph=None,
                  progress: Optional[Callable[[dict], None]] = None,
                  adjudicator: Optional[RuleAdjudicator] = None) -> dict
# result: {"units", "candidates", "adjudicated", "cache_hits", "rules_created",
#          "conflicts", "trust": {"high": n, "normal": n, "conflicted": n},
#          "pruned", "errors": [...]}
```

Stages (progress events per stage: `units`, `pairing`, `adjudicate`, `merge`, `store`):
1. `collect_rule_units(store)`.
2. `find_candidates(units, store._embedder, graph, threshold=settings.consensus_similarity_threshold)`.
3. Adjudicate each pair (default `RuleAdjudicator(settings)`); per-pair exceptions → `errors.append({"pair": [a.key, b.key], "error": repr(exc)})`, pair skipped. `adjudicator.save()` once after the loop (also on partial failure).
4. `merge_groups(units, verdicts, review_mode=settings.review_mode)`.
5. Store: `store.upsert(rules)`; prune stale: previous `kind="rule"` items (paginate `get_items(where={"kind": "rule"})`) whose ids are not in the expected set `{r.id for r in rules}` → `store.delete_ids`. Guard: skip upsert AND pruning when `adjudicated == 0 and candidates > 0` (total adjudication failure must not wipe prior consensus). Conflict edges: for each conflicted rule, upsert `Entity(normalized_name=normalize_name(r.statement), display_name=r.statement, type="rule", chunk_id=r.id)`; for each cross-group conflict verdict where both units map to (different) rules, an `Edge(src=..., dst=..., relation_type="conflicts", chunk_id=<rule id>)`; corroborating members: rule Entity rows re-upserted per member chunk id (entity_chunks lineage). Graph=None/Null tolerated (no-op).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_consensus_run.py
"""End-to-end consensus pass with fakes (enhancement #5)."""

import json

from opendomainmcp.config import Settings
from opendomainmcp.consensus.adjudicate import RuleAdjudicator
from opendomainmcp.consensus.run import run_consensus
from opendomainmcp.models import Chunk, KnowledgeUnit


def _seed(store, claim_a="amount must not be negative",
          claim_b="order amount cannot be negative"):
    for i, (claim, lang, src) in enumerate(
            [(claim_a, "java", "Billing.java"), (claim_b, "plsql", "pkg.pkb")]):
        k = KnowledgeUnit(summary="S", confidence=0.9, evidence=[
            {"claim": claim, "quote": f"q{i}", "source": src,
             "start_line": 1, "end_line": 1, "verified": True}])
        store.upsert([Chunk(text=f"q{i} negative amount guard", source=src,
                            kind="code", language=lang, knowledge=k)])


def _same_adjudicator(tmp_path):
    return RuleAdjudicator(
        Settings(), cache_path=tmp_path / "v.json",
        complete=lambda s, u: json.dumps({"verdict": "same", "reason": "r"}))


def test_run_creates_high_trust_rule(store, fake_graph, tmp_path):
    _seed(store)
    result = run_consensus(store, Settings(), graph=fake_graph,
                           adjudicator=_same_adjudicator(tmp_path))
    assert result["rules_created"] == 1
    assert result["trust"]["high"] == 1 and result["errors"] == []

    rules = store.get_items(limit=10, where={"kind": "rule"})
    assert len(rules) == 1
    meta = rules[0]["metadata"]
    assert meta["trust"] == "high" and meta["corroborations"] == 2
    assert meta["evidence_status"] == "verified"


def test_rerun_hits_cache_and_prunes_stale(store, fake_graph, tmp_path):
    _seed(store)
    adj = _same_adjudicator(tmp_path)
    run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj)

    # second run: cache hit, same rule id, no dupes
    adj2 = RuleAdjudicator(Settings(), cache_path=tmp_path / "v.json",
                           complete=lambda s, u: (_ for _ in ()).throw(
                               AssertionError("cache must hit")))
    r2 = run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj2)
    assert r2["cache_hits"] >= 1
    assert len(store.get_items(limit=10, where={"kind": "rule"})) == 1


def test_total_adjudication_failure_preserves_prior_rules(store, fake_graph,
                                                          tmp_path):
    _seed(store)
    run_consensus(store, Settings(), graph=fake_graph,
                  adjudicator=_same_adjudicator(tmp_path))

    def broken(system, user):
        raise RuntimeError("llm down")

    adj = RuleAdjudicator(Settings(), cache_path=tmp_path / "other.json",
                          complete=broken)
    result = run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj)
    assert result["errors"] and result["rules_created"] == 0
    assert len(store.get_items(limit=10, where={"kind": "rule"})) == 1  # preserved


def test_conflict_creates_pending_rules_and_graph_edge(store, fake_graph,
                                                       tmp_path):
    _seed(store, claim_a="amount must be >= 0", claim_b="amount must be > 0")
    adj = RuleAdjudicator(
        Settings(), cache_path=tmp_path / "v.json",
        complete=lambda s, u: json.dumps({"verdict": "conflict", "reason": "r"}))
    result = run_consensus(store, Settings(), graph=fake_graph, adjudicator=adj)
    assert result["conflicts"] >= 1 and result["trust"]["conflicted"] == 2

    pending = store.get_items(limit=10, where={"review_status": "pending"})
    assert any(i["metadata"].get("kind") == "rule" for i in pending)
    # a conflicts edge exists between the two rule entities
    ent = fake_graph.get_entity(
        next(i["metadata"]["statement"] for i in pending
             if i["metadata"].get("kind") == "rule").lower())
    assert ent is not None
```

Note: the last assertion's entity lookup uses normalize_name semantics (lowercase) — adapt to `normalize_name(statement)` exactly; assert the `conflicts` edge via `fake_graph.neighbors(...)` with relation_type "conflicts" if the direct lookup is awkward — keep the intent: both rule entities exist and are connected by a conflicts edge.

- [ ] **Step 2: Run tests to verify they fail** (module not found)

- [ ] **Step 3: Implement** per Interfaces.

- [ ] **Step 4: Run tests + full suite**

Run: `.venv/bin/python -m pytest tests/test_consensus_run.py -v` then `.venv/bin/python -m pytest`
Expected: all pass, no regressions

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/consensus/run.py tests/test_consensus_run.py
git commit -m "feat: consensus orchestrator with incremental cache and safe pruning"
```

---

### Task 7: Surfaces — CLI `consolidate` + task runner

**Files:**
- Modify: `src/opendomainmcp/cli.py`, `src/opendomainmcp/tasks/runners.py`, `src/opendomainmcp/api/task_routes.py`
- Test: `tests/test_cli.py` (append), the task-runner test file (append)

**Interfaces:**
- CLI: `opendomainmcp consolidate [--json]` — runs `run_consensus(ctx.store, ctx.settings, graph=ctx.graph, progress=stderr printer)`; prints the result dict (key: value lines, or JSON with `--json`); returns 0 (errors are IN the result; rc 1 only when the pass itself raises).
- Runner: `run_consolidate(ctx, store, task, is_cancelled)` — no children enumeration (single unit of work); coarse cancel check before starting; result dict into `store.update`; failures list from result errors. `RUNNERS["consolidate"]`; `_title` → `"Consolidate rules"`.

- [ ] **Step 1: Write the failing tests** — follow the established fake-ctx CLI pattern (monkeypatch `opendomainmcp.consensus.run.run_consensus`... note the runner should import it module-level in runners.py so tests can patch `opendomainmcp.tasks.runners.run_consensus`; the CLI imports it lazily inside `_cmd_consolidate`, patch at source). Assertions: CLI rc 0 + "rules_created" in output; runner registered + result dict in task.

- [ ] **Step 2–4: RED → implement → GREEN + FULL suite** (commands as usual)

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/cli.py src/opendomainmcp/tasks/runners.py src/opendomainmcp/api/task_routes.py tests/
git commit -m "feat: consolidate CLI and task runner for consensus pass"
```

---

## Self-review notes

- **Spec coverage:** Stage 1 deterministic+embedding pairing with the three signals (chain, entity, embedding) ✔ T2 (edge-between-sources signal folded into the chain signal — chains ARE the cross-layer edges materialized; noted as a conscious simplification); Stage 2 LLM adjudication with content-hash cache + incremental re-runs ✔ T3; Stage 3 canonical merge with per-source evidence, reversible (originals untouched; rules are additional items with member back-links) ✔ T4/T5; trust tiers high/normal/conflicted ✔ T4; conflicted → review queue with pending status ✔ T4/T5 (priority ORDERING stays deferred to #3 as recorded); `conflicts` graph relation ✔ T5 (corroborates lineage via rule-entity chunk links; a distinct `corroborates` edge type was simplified away — flag for final review as a conscious deviation); retrieval prefers canonical rules + trust in citations/MCP (metadata flows through the #2 lifting automatically) ✔ T6; batch pass + CLI/CommandCenter rerun ✔ T7.
- **Placeholder scan:** T7's tests intentionally defer to the two files' local fixtures (named precisely); everything else complete.
- **Type consistency:** RuleUnit/RuleItem defined in T1, consumed by T2/T4/T5/T6; verdict tuples `(key_a, key_b, verdict)` from T5's adjudication loop into T4's merge_groups; `statement` metadata key required by T6 and produced by T1.
- **Known risks:** `store._embedder` private access (pragmatic; note for final review whether to add a public accessor); O(N²) cosine loop bounded by corpus rule count; entity scan capped at 1000.
