# Evidence Traceability (Enhancement #2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every extracted rule/concept carries iron evidence — a verbatim quote located to exact file:line by a deterministic verifier — stored end-to-end (Chroma metadata, graph store) and surfaced on all four interfaces (Review page, RAG citations, MCP payloads, Graph browser).

**Architecture:** LLMs (chunk extraction and chain analysis) emit only `{claim, quote}` pairs — they never report line numbers (they can't see them; asking invites hallucination). A deterministic verifier (`extract/verify.py`) locates each quote in the known source text (exact match → whitespace-normalized match → unverified), computes absolute line ranges from the chunk/function's known `start_line`, and stamps `verified`. Unverified evidence is kept, flagged (`evidence_status`), and penalizes confidence. Evidence is serialized as a JSON-string metadata field (Chroma metadata is flat scalars), lifted back to a structured `evidence` list in `SearchResult.to_dict()` so every API/MCP surface gets it for free. Graph entities/edges gain an `evidence_json` column threaded from the source KnowledgeUnit. ChainItems derive their evidence deterministically from member FunctionSummaries (no new LLM surface). The SPA shows expandable evidence on Review cards and Graph entity detail.

**Deviation from spec (improvement, note in commit):** the spec had the LLM attach line ranges and the verifier auto-correct them; this plan has the LLM attach only quotes and the verifier *compute* lines — strictly stronger (no hallucinated line numbers exist to correct), same stored shape `{source, start_line, end_line, quote, verified}`.

**Tech Stack:** Python ≥ 3.11 backend; React/TS SPA (Playwright e2e only — SPA task verifies via `npm run build` + a Playwright smoke); pytest offline.

**Spec:** `docs/superpowers/specs/2026-07-06-evidence-traceability-design.md`

## Global Constraints

- All tests offline; LLM always faked. `.venv/bin/python -m pytest`.
- Evidence entry stored shape (fixed): `{"claim": str, "quote": str, "source": str, "start_line": int|None, "end_line": int|None, "verified": bool}`.
- `evidence_status` values (fixed): `""` (no evidence), `"verified"` (all verified), `"partial"` (some), `"unverified"` (none verified).
- Confidence penalty: module constant `UNVERIFIED_PENALTY = 0.5` in `extract/verify.py` — multiply confidence when status == `"unverified"`; `"partial"` and `"verified"` unpenalized. Never dropped (Fail Loud).
- Verifier matching: stage 1 exact substring; stage 2 whitespace-normalized (every whitespace run in the quote matches any whitespace run incl. newlines in the text); no third stage — not found ⇒ `verified=False`, lines None.
- Chroma metadata stays flat scalars: `evidence` = JSON string (only when non-empty), `evidence_status` = plain string added to `_FILTER_FIELDS`.
- Graph: additive `evidence_json TEXT` column on `entities` and `edges` (CREATE ... for new installs AND `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for existing DBs, MariaDB supports it); Entity/Edge dataclasses gain `evidence: str = ""` (JSON string). Codegraph call-site quotes are OUT of scope (those edges already carry file:line from 4A) — record as deferred.
- Everything additive/backward-compatible: no evidence ⇒ all surfaces behave exactly as today.

## Parallel execution note

Waves: **[T1] → [T2, T3, T4 parallel] → [T5, T6, T7 parallel] → [T8] → [T9]**. Parallel implementers `git add` only their own files; retry commit on index.lock.

---

### Task 1: Evidence model + metadata serialization

**Files:**
- Modify: `src/opendomainmcp/models.py` (KnowledgeUnit + Chunk.metadata; ChainItem.evidence + metadata)
- Test: `tests/test_evidence_model.py`

**Interfaces:**
- Produces: `KnowledgeUnit.evidence: list[dict] = field(default_factory=list)` and `KnowledgeUnit.evidence_status: str = ""` (after `workflow`). `Chunk.metadata()` emits `"evidence": json.dumps(k.evidence, ensure_ascii=False)` when non-empty and `"evidence_status"` when non-empty (both dropped otherwise, matching the existing empty-drop behavior). `ChainItem` gains `evidence: list[dict] = field(default_factory=list)` and `evidence_status: str = ""`; its `metadata()` emits the same two fields. Also a module-level helper used by consumers:

```python
def parse_evidence_field(meta: dict) -> list[dict]:
    """Parse the JSON-string 'evidence' metadata field; [] on absence/corruption."""
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evidence_model.py
"""Evidence storage shape on KnowledgeUnit/Chunk/ChainItem (enhancement #2)."""

import json

from opendomainmcp.models import ChainItem, Chunk, KnowledgeUnit, parse_evidence_field

EV = [{"claim": "amount >= 0", "quote": "if (amt < 0) throw", "source": "A.java",
      "start_line": 12, "end_line": 12, "verified": True}]


def test_knowledge_unit_evidence_defaults():
    k = KnowledgeUnit()
    assert k.evidence == [] and k.evidence_status == ""


def test_chunk_metadata_serializes_evidence_json():
    k = KnowledgeUnit(summary="S", evidence=EV, evidence_status="verified")
    c = Chunk(text="code", source="A.java", kind="code", knowledge=k)
    meta = c.metadata()
    assert meta["evidence_status"] == "verified"
    assert json.loads(meta["evidence"]) == EV
    assert all(not isinstance(v, (list, dict)) for v in meta.values())


def test_chunk_metadata_omits_empty_evidence():
    c = Chunk(text="t", source="a.md", knowledge=KnowledgeUnit(summary="S"))
    meta = c.metadata()
    assert "evidence" not in meta and "evidence_status" not in meta


def test_chain_item_evidence_metadata():
    item = ChainItem(entry="e", title="T", body="B", evidence=EV,
                     evidence_status="partial")
    meta = item.metadata()
    assert meta["evidence_status"] == "partial"
    assert json.loads(meta["evidence"])[0]["claim"] == "amount >= 0"


def test_parse_evidence_field_roundtrip_and_corruption():
    assert parse_evidence_field({"evidence": json.dumps(EV)}) == EV
    assert parse_evidence_field({}) == []
    assert parse_evidence_field({"evidence": "{not json"}) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_evidence_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_evidence_field'`

- [ ] **Step 3: Implement**

`models.py`: add the two fields to `KnowledgeUnit` (after `workflow`) and to `ChainItem` (after `truncated`); in `Chunk.metadata()` where knowledge fields are emitted add:

```python
            if k.evidence:
                meta["evidence"] = json.dumps(k.evidence, ensure_ascii=False)
            if k.evidence_status:
                meta["evidence_status"] = k.evidence_status
```

(mirror in `ChainItem.metadata()` with `self.evidence`/`self.evidence_status`). Module-level helper:

```python
def parse_evidence_field(meta: dict) -> list[dict]:
    """Parse the JSON-string 'evidence' metadata field; [] on absence/corruption."""
    raw = meta.get("evidence")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []
```

(`import json` at models.py top if absent.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_evidence_model.py tests/test_models.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/models.py tests/test_evidence_model.py
git commit -m "feat: evidence fields on KnowledgeUnit/Chunk/ChainItem metadata"
```

---

### Task 2: Deterministic evidence verifier

**Files:**
- Create: `src/opendomainmcp/extract/verify.py`
- Test: `tests/test_evidence_verify.py`

**Interfaces:**
- Consumes: evidence entry dicts (Task 1 shape; input entries need only `claim`/`quote`).
- Produces:

```python
UNVERIFIED_PENALTY = 0.5

def verify_evidence(evidence: list[dict], text: str, source: str,
                    base_line: int = 1) -> tuple[list[dict], str]:
    """Locate each quote in ``text``; return (completed entries, status).

    Every returned entry has the full stored shape. start/end lines are
    absolute (text's first line is ``base_line``). status: "" (no evidence),
    "verified" (all), "partial" (some), "unverified" (none)."""

def apply_penalty(confidence: float, status: str) -> float:
    """confidence * UNVERIFIED_PENALTY when status == 'unverified', else unchanged."""
```

Matching: stage 1 `text.find(quote)`; stage 2 regex built from the quote with every whitespace run replaced by `\s+` and all else `re.escape`d (quotes with no word characters skip stage 2). Line math: `start_line = base_line + text.count("\n", 0, idx)`; `end_line = start_line + matched_text.count("\n")`. Empty/blank quotes ⇒ unverified entry. Entries never dropped or reordered.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evidence_verify.py
"""Deterministic quote-locating verifier (enhancement #2)."""

from opendomainmcp.extract.verify import UNVERIFIED_PENALTY, apply_penalty, verify_evidence

TEXT = "def charge(amt):\n    if amt < 0:\n        raise ValueError('neg')\n    return amt\n"


def _ev(quote, claim="amount must not be negative"):
    return [{"claim": claim, "quote": quote}]


def test_exact_match_computes_absolute_lines():
    out, status = verify_evidence(_ev("if amt < 0:"), TEXT, "billing.py", base_line=10)
    assert status == "verified"
    e = out[0]
    assert e["verified"] and e["source"] == "billing.py"
    assert e["start_line"] == 11 and e["end_line"] == 11
    assert e["claim"] == "amount must not be negative"


def test_whitespace_drift_still_verifies():
    # local models often collapse/expand whitespace when copying
    out, status = verify_evidence(_ev("if amt < 0:  raise ValueError('neg')"),
                                  TEXT, "billing.py", base_line=1)
    assert status == "verified"
    assert out[0]["start_line"] == 2 and out[0]["end_line"] == 3


def test_fabricated_quote_is_unverified_not_dropped():
    out, status = verify_evidence(_ev("if amount.is_negative():"), TEXT, "b.py")
    assert status == "unverified"
    assert out[0]["verified"] is False
    assert out[0]["start_line"] is None and out[0]["end_line"] is None
    assert len(out) == 1


def test_mixed_evidence_is_partial_and_order_preserved():
    ev = _ev("return amt") + _ev("nothing like this")
    out, status = verify_evidence(ev, TEXT, "b.py")
    assert status == "partial"
    assert out[0]["verified"] and not out[1]["verified"]


def test_empty_evidence_and_blank_quote():
    assert verify_evidence([], TEXT, "b.py") == ([], "")
    out, status = verify_evidence(_ev("   "), TEXT, "b.py")
    assert status == "unverified" and not out[0]["verified"]


def test_apply_penalty():
    assert apply_penalty(0.8, "unverified") == 0.8 * UNVERIFIED_PENALTY
    assert apply_penalty(0.8, "partial") == 0.8
    assert apply_penalty(0.8, "verified") == 0.8
    assert apply_penalty(0.8, "") == 0.8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_evidence_verify.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

```python
# src/opendomainmcp/extract/verify.py
"""Deterministic evidence verification (enhancement #2).

The LLM reports only verbatim quotes — never line numbers (it cannot see
them). This verifier locates each quote in the known source text and
computes absolute line ranges from the chunk/function's known start line,
so a line number in stored evidence is a fact, not a claim. Quotes that
cannot be located (exactly or whitespace-normalized) are kept and flagged
unverified — never silently dropped (Fail Loud)."""

from __future__ import annotations

import re

UNVERIFIED_PENALTY = 0.5


def verify_evidence(evidence: list[dict], text: str, source: str,
                    base_line: int = 1) -> tuple[list[dict], str]:
    if not evidence:
        return [], ""
    out: list[dict] = []
    verified_count = 0
    for entry in evidence:
        quote = str(entry.get("quote", "") or "")
        claim = str(entry.get("claim", "") or "")
        located = _locate(quote, text)
        if located is None:
            out.append({"claim": claim, "quote": quote, "source": source,
                        "start_line": None, "end_line": None, "verified": False})
            continue
        idx, matched = located
        start = base_line + text.count("\n", 0, idx)
        out.append({"claim": claim, "quote": quote, "source": source,
                    "start_line": start,
                    "end_line": start + matched.count("\n"),
                    "verified": True})
        verified_count += 1
    if verified_count == len(out):
        status = "verified"
    elif verified_count:
        status = "partial"
    else:
        status = "unverified"
    return out, status


def _locate(quote: str, text: str) -> tuple[int, str] | None:
    """(index, matched text) of ``quote`` in ``text``, or None."""
    if not quote.strip():
        return None
    idx = text.find(quote)
    if idx != -1:
        return idx, quote
    # whitespace-normalized: any whitespace run in the quote matches any
    # whitespace run (incl. newlines) in the text
    parts = [re.escape(p) for p in quote.split()]
    if not parts:
        return None
    m = re.search(r"\s+".join(parts), text)
    if m:
        return m.start(), m.group(0)
    return None


def apply_penalty(confidence: float, status: str) -> float:
    return confidence * UNVERIFIED_PENALTY if status == "unverified" else confidence
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_evidence_verify.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/extract/verify.py tests/test_evidence_verify.py
git commit -m "feat: deterministic quote-locating evidence verifier"
```

---

### Task 3: Extraction prompt + parse emit evidence

**Files:**
- Modify: `src/opendomainmcp/extract/knowledge.py` (`_SYSTEM`, `_parse`, new `_parse_evidence`)
- Test: `tests/test_extract.py` (append)

**Interfaces:**
- Consumes: Task 1 (`KnowledgeUnit.evidence`).
- Produces: `_SYSTEM` gains one key line (verbatim, insert before the workflow line):

```
  "evidence": a list of {"claim", "quote"} objects — for each important concept, relation, or constraint, "quote" is an EXACT contiguous snippet copied character-for-character from the snippet above that supports the "claim" (may be empty),
```

`_parse` sets `evidence=_parse_evidence(data.get("evidence"))` where `_parse_evidence` normalizes to `[{"claim": str, "quote": str}]`, dropping entries without a non-blank quote, tolerating strings (treated as quote with empty claim) and non-list input (⇒ `[]`). `evidence_status` is NOT set here (the verifier owns it — Task 5).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_extract.py` (follow its existing `_parse`-driven test style — read the top of the file first):

```python
def test_parse_extracts_evidence_pairs():
    from opendomainmcp.extract.knowledge import _parse

    raw = ('{"summary": "S", "concepts": ["billing"], "knowledge_type": "Code",'
           ' "evidence": [{"claim": "no negative amounts",'
           ' "quote": "if (amt < 0) throw"}]}')
    k = _parse(raw)
    assert k.evidence == [{"claim": "no negative amounts",
                           "quote": "if (amt < 0) throw"}]
    assert k.evidence_status == ""


def test_parse_evidence_tolerates_junk():
    from opendomainmcp.extract.knowledge import _parse

    raw = ('{"summary": "S", "evidence": ["bare quote string",'
           ' {"claim": "c", "quote": "  "}, {"claim": "no quote"}, 42]}')
    k = _parse(raw)
    assert k.evidence == [{"claim": "", "quote": "bare quote string"}]


def test_system_prompt_mentions_evidence():
    from opendomainmcp.extract.knowledge import _SYSTEM

    assert '"evidence"' in _SYSTEM and "character-for-character" in _SYSTEM
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_extract.py -k evidence -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Add `_parse_evidence` next to the other `_parse_*` helpers:

```python
def _parse_evidence(value) -> list[dict]:
    """Normalize LLM evidence to [{"claim", "quote"}]; quoteless entries drop."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str):
            claim, quote = "", item
        elif isinstance(item, dict):
            claim = str(item.get("claim", "") or "").strip()
            quote = str(item.get("quote", "") or "")
        else:
            continue
        if quote.strip():
            out.append({"claim": claim, "quote": quote})
    return out
```

wire into `_parse` (`evidence=_parse_evidence(data.get("evidence")),`) and add the `_SYSTEM` line verbatim from Interfaces.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_extract.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/extract/knowledge.py tests/test_extract.py
git commit -m "feat: extraction prompt and parser emit claim/quote evidence"
```

---

### Task 4: Chain analyzer emits evidence

**Files:**
- Modify: `src/opendomainmcp/codegraph/analyze_llm.py` (`_FUNC_SYSTEM`, `FunctionSummary`, `summarize_function`)
- Test: `tests/test_codegraph_analyze_llm.py` (append)

**Interfaces:**
- Consumes: nothing new (self-contained; verification happens in Task 6).
- Produces: `FunctionSummary.evidence: list[dict] = field(default_factory=list)` (entries `{"claim","quote"}`). `_FUNC_SYSTEM` gains (verbatim, after the rules line):

```
  "evidence": a list of {"claim", "quote"} objects — "quote" is an EXACT contiguous snippet copied character-for-character from the function source above that supports the "claim" (may be empty),
```

`summarize_function` parses it with the same tolerance rules as Task 3 (duplicate the small normalizer locally or import `_parse_evidence` — import it: `from ..extract.knowledge import _parse_evidence` is acceptable cross-module reuse of the shared normalization; if the reviewer of Task 3 made it public under another name, use that). `_CHAIN_SYSTEM` unchanged — chain evidence is derived deterministically from members in Task 6.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_codegraph_analyze_llm.py`:

```python
def test_summarize_function_parses_evidence():
    def fake(system, user):
        assert '"evidence"' in system
        return json.dumps({"summary": "Validates.", "rules": ["amt >= 0"],
                           "confidence": 0.9,
                           "evidence": [{"claim": "amt >= 0",
                                         "quote": "if (amt < 0) throw"}]})

    fs = ChainAnalyzer(Settings(), complete=fake).summarize_function(
        _fd("a.B.validate"), "if (amt < 0) throw", {}, {})
    assert fs.evidence == [{"claim": "amt >= 0", "quote": "if (amt < 0) throw"}]


def test_summarize_function_evidence_defaults_empty():
    def fake(system, user):
        return json.dumps({"summary": "S", "rules": [], "confidence": 0.5})

    fs = ChainAnalyzer(Settings(), complete=fake).summarize_function(
        _fd("x.Y.z"), "code", {}, {})
    assert fs.evidence == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_codegraph_analyze_llm.py -v`
Expected: the two new tests FAIL

- [ ] **Step 3: Implement** per Interfaces (dataclass field, prompt line, parse wiring).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_codegraph_analyze_llm.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/codegraph/analyze_llm.py tests/test_codegraph_analyze_llm.py
git commit -m "feat: chain analyzer emits claim/quote evidence per function"
```

---

### Task 5: Pipeline verification wiring

**Files:**
- Modify: `src/opendomainmcp/ingest/pipeline.py` (`_extract_one`, `IngestReport`)
- Test: `tests/test_pipeline.py` (append)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: in `_extract_one`, immediately after extraction succeeds and knowledge is set (and only when `chunk.knowledge.evidence` is non-empty): run

```python
                from ..extract.verify import apply_penalty, verify_evidence

                verified, status = verify_evidence(
                    chunk.knowledge.evidence, chunk.text, chunk.source,
                    base_line=chunk.start_line or 1)
                chunk.knowledge.evidence = verified
                chunk.knowledge.evidence_status = status
                chunk.knowledge.confidence = apply_penalty(
                    chunk.knowledge.confidence, status)
```

`IngestReport` gains `evidence_verified: int = 0` and `evidence_unverified: int = 0` (counting individual evidence entries, accumulated in `_extract_one`; thread-safe enough — same duck as `report.errors` under the existing ThreadPool usage, use the same idiom). CLI ingest output: after the Filtered line, print `Evidence: {v} verified / {u} unverified.` only when either is non-zero (modify `_cmd_ingest` in cli.py — include cli.py + tests/test_cli.py in your files if you add this; it is required, spec says report includes counts).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py` — you need an extractor fake that returns evidence; the shared `fake_extractor` fixture does not. Define locally:

```python
def test_evidence_verified_at_ingest(store, fake_graph, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.ingest.pipeline import Pipeline
    from opendomainmcp.models import KnowledgeUnit

    class EvidenceExtractor:
        def extract(self, text, kind, language=None):
            return KnowledgeUnit(
                summary="S", knowledge_type="Code", confidence=0.8,
                evidence=[{"claim": "real", "quote": text[:10]},
                          {"claim": "fake", "quote": "zz_not_in_text_zz"}])

    f = tmp_path / "billing.py"
    f.write_text("def charge(amt):\n    return amt\n")
    settings = Settings(chunk_size=200, chunk_overlap=20)
    report = Pipeline(store, EvidenceExtractor(), settings,
                      graph=fake_graph).ingest_path(f)

    assert report.evidence_verified >= 1 and report.evidence_unverified >= 1
    items = store.get_items(limit=10, where={"evidence_status": "partial"})
    assert items, "partial evidence_status must be stored and filterable"
    import json as _json
    ev = _json.loads(items[0]["metadata"]["evidence"])
    assert any(e["verified"] and e["start_line"] for e in ev)
    assert any(not e["verified"] for e in ev)


def test_unverified_evidence_penalizes_confidence(store, fake_graph, tmp_path):
    from opendomainmcp.config import Settings
    from opendomainmcp.extract.verify import UNVERIFIED_PENALTY
    from opendomainmcp.ingest.pipeline import Pipeline
    from opendomainmcp.models import KnowledgeUnit

    class FabricatingExtractor:
        def extract(self, text, kind, language=None):
            return KnowledgeUnit(summary="S", knowledge_type="Code",
                                 confidence=0.8,
                                 evidence=[{"claim": "x", "quote": "not there"}])

    f = tmp_path / "a.py"
    f.write_text("def f():\n    return 1\n")
    Pipeline(store, FabricatingExtractor(), Settings(chunk_size=200,
             chunk_overlap=20), graph=fake_graph).ingest_path(f)
    items = store.get_items(limit=10, where={"evidence_status": "unverified"})
    assert items and abs(float(items[0]["metadata"]["confidence"])
                         - 0.8 * UNVERIFIED_PENALTY) < 1e-6
```

Also add `"evidence_status"` to `_FILTER_FIELDS` in `src/opendomainmcp/store/chroma_store.py` (needed for the `where=` in these tests) — include that file in this task.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -k evidence -v`
Expected: FAIL — IngestReport has no `evidence_verified`

- [ ] **Step 3: Implement** per Interfaces (pipeline hook + report fields + `_FILTER_FIELDS` + CLI line).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py tests/test_cli.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/ingest/pipeline.py src/opendomainmcp/store/chroma_store.py src/opendomainmcp/cli.py tests/test_pipeline.py tests/test_cli.py
git commit -m "feat: verify evidence at ingest with fail-loud counts and penalty"
```

---

### Task 6: Analyze-pass verification + ChainItem evidence

**Files:**
- Modify: `src/opendomainmcp/codegraph/analyze.py` (`_summarize_levels` verification; `_backfill` threads evidence; `_store_chains` derives ChainItem evidence)
- Test: `tests/test_codegraph_analyze.py` (append)

**Interfaces:**
- Consumes: Tasks 1, 2, 4.
- Produces:
  - In `_summarize_levels`, after a `FunctionSummary` returns: verify its evidence against the function's own source — `verify_evidence(fs.evidence, src, fn.file, base_line=fn.start_line)`; store the completed entries back on `fs.evidence` and keep a per-function status map (attach `fs` is frozen? FunctionSummary is a plain dataclass — add nothing to it; keep `evidence_status_by_fn: dict[str, str]` alongside `summaries`, returned or threaded to callers as the implementation requires). Apply `apply_penalty` to `fs.confidence`.
  - `_backfill`: the merged KnowledgeUnit gains `evidence` = concatenation of the contributing functions' verified entries (order = same sorted-qualified-name order as summaries) and `evidence_status` = combined (all verified ⇒ verified; none ⇒ unverified; else partial; empty ⇒ "").
  - `_store_chains`: `ChainItem.evidence` = concatenation of member functions' evidence entries (members order), `evidence_status` combined the same way. No new LLM calls.
  - Result dict gains `"evidence": {"verified": n, "unverified": m}` (entry counts across all function summaries).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_codegraph_analyze.py` (reuse its `_setup`/fixtures; extend `_fake_complete` to include evidence):

```python
def _fake_complete_with_evidence(system, user):
    if "call chain" in system:
        return json.dumps({"title": "Chain title", "body": "End to end.",
                           "rules": ["chain rule"]})
    # quote copied from the JAVA fixture's validate body — real; plus one fake
    return json.dumps({"summary": "Summary.", "rules": ["amount >= 0"],
                       "confidence": 0.8,
                       "evidence": [{"claim": "amount >= 0",
                                     "quote": "prepareCall"},
                                    {"claim": "bogus", "quote": "zz_nope_zz"}]})


def test_analyze_verifies_and_threads_evidence(tmp_path, pipeline, store,
                                               fake_graph):
    _setup(tmp_path, pipeline)
    settings = Settings(codegraph_extract=True)
    result = analyze_corpus(tmp_path, store, settings, fake_graph,
                            analyzer=ChainAnalyzer(
                                settings, complete=_fake_complete_with_evidence))
    assert result["evidence"]["verified"] >= 1
    assert result["evidence"]["unverified"] >= 1

    # backfilled chunk carries verified + unverified entries with real lines
    import json as _json
    items = [i for i in store.get_items(limit=50, where={"language": "java"})
             if i["metadata"].get("evidence")]
    assert items
    ev = _json.loads(items[0]["metadata"]["evidence"])
    good = [e for e in ev if e["verified"]]
    assert good and all(e["start_line"] for e in good)
    assert items[0]["metadata"]["evidence_status"] == "partial"

    # chain item derives member evidence deterministically
    chains = store.sibling(f"{store.stats()['collection']}__chains")
    got = chains.get_items(limit=10)
    assert got and got[0]["metadata"].get("evidence_status") == "partial"
    cev = _json.loads(got[0]["metadata"]["evidence"])
    assert any(e["verified"] for e in cev)
```

Note: the verified quote's `source` will be the repo-relative function file and its lines absolute in that file (base_line = fn.start_line) — do not assert exact numbers, assert presence/positivity.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_codegraph_analyze.py -k evidence -v`
Expected: FAIL — result has no "evidence" key

- [ ] **Step 3: Implement** per Interfaces.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_codegraph_analyze.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/codegraph/analyze.py tests/test_codegraph_analyze.py
git commit -m "feat: verify chain-analysis evidence and thread it to chunks and chains"
```

---

### Task 7: Graph evidence columns

**Files:**
- Modify: `src/opendomainmcp/graph/models.py` (Entity/Edge `evidence: str = ""`), `src/opendomainmcp/graph/builder.py`, `src/opendomainmcp/graph/store.py` (DDL + upserts + reads), `tests/conftest.py` (FakeGraphStore)
- Test: `tests/test_graph_builder.py` (append), `tests/test_graph_store_fake.py` (append)

**Interfaces:**
- Consumes: Task 1 shape.
- Produces: `Entity.evidence: str = ""` and `Edge.evidence: str = ""` (JSON-string of the evidence entries supporting that entity/relation — `""` when none). `build_graph(knowledge, chunk_id)` threads evidence: an entity gets the JSON of the knowledge's verified entries whose `claim` mentions the entity name (case-insensitive substring), falling back to `""`; every edge gets the JSON of verified entries whose claim mentions src or dst, else `""` (keep it simple and deterministic — this is provenance hint, not perfect attribution). MariaDB: `evidence_json TEXT` on entities + edges via both the CREATE TABLE statements and `ALTER TABLE ... ADD COLUMN IF NOT EXISTS evidence_json TEXT NULL` in `ensure_schema` (existing installs); upserts write it (`ON DUPLICATE KEY UPDATE evidence_json=VALUES(evidence_json)` only when the new value is non-empty — implement as `evidence_json=IF(VALUES(evidence_json)='' , evidence_json, VALUES(evidence_json))`); `get_entity`/`neighbors`/`list_entities` include `"evidence": <parsed list or []>`. FakeGraphStore mirrors (store the string, parse on read). NullGraphStore untouched (no-ops already).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_graph_builder.py`:

```python
def test_build_graph_threads_matching_evidence():
    import json

    from opendomainmcp.graph.builder import build_graph
    from opendomainmcp.models import KnowledgeUnit

    k = KnowledgeUnit(
        summary="S", confidence=0.9,
        entities=[{"name": "BillingService", "type": "Service"}],
        typed_relations=[{"src": "BillingService", "dst": "OrderRepo",
                          "type": "uses"}],
        evidence=[{"claim": "BillingService validates amounts",
                   "quote": "q", "source": "A.java", "start_line": 3,
                   "end_line": 3, "verified": True},
                  {"claim": "unrelated", "quote": "x", "source": "A.java",
                   "start_line": 9, "end_line": 9, "verified": False}])
    entities, edges = build_graph(k, "c1")
    billing = next(e for e in entities if e.display_name == "BillingService")
    ev = json.loads(billing.evidence)
    assert len(ev) == 1 and ev[0]["start_line"] == 3   # verified + name-matched only
    assert edges and json.loads(edges[0].evidence)[0]["verified"]
```

Append to `tests/test_graph_store_fake.py`:

```python
def test_fake_store_roundtrips_entity_evidence(fake_graph):
    import json

    from opendomainmcp.graph.models import Entity

    ev = json.dumps([{"claim": "c", "quote": "q", "source": "A.java",
                      "start_line": 1, "end_line": 1, "verified": True}])
    fake_graph.upsert_entities([Entity(normalized_name="x", display_name="X",
                                       type="Concept", chunk_id="c1",
                                       evidence=ev)])
    got = fake_graph.get_entity("x")
    assert got["evidence"] == json.loads(ev)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_graph_builder.py tests/test_graph_store_fake.py -v`
Expected: new tests FAIL

- [ ] **Step 3: Implement** per Interfaces (read `graph/store.py`'s existing method idioms first; keep the Maria changes symmetrical between entities and edges).

- [ ] **Step 4: Run tests + full check of graph tests**

Run: `.venv/bin/python -m pytest tests/test_graph_builder.py tests/test_graph_store_fake.py tests/test_graph_models.py tests/test_graph_api.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/graph tests/conftest.py tests/test_graph_builder.py tests/test_graph_store_fake.py
git commit -m "feat: evidence_json on graph entities and edges"
```

---

### Task 8: API surfacing — SearchResult lifting, citations, graph payloads

**Files:**
- Modify: `src/opendomainmcp/models.py` (SearchResult.to_dict), `src/opendomainmcp/query/rag.py` (`_citations`)
- Test: `tests/test_models.py` (append), `tests/test_rag.py` (append), `tests/test_views.py` (append)

**Interfaces:**
- Consumes: Tasks 1, 5, 7.
- Produces:
  - `SearchResult.to_dict()` lifts evidence: when `metadata` has an `evidence` JSON string, the returned dict gains a top-level `"evidence": <parsed list>` (metadata itself unchanged). All consumers — `/api/search`, `/api/items` (returns get_items dicts — ALSO lift there: check whether get_items goes through SearchResult; if it returns raw dicts, apply the same lifting in `ChromaStore.get_items`), MCP `run_view_tool`, advisor facets — inherit it with zero further changes. Verify with tests, don't assume: read `to_dict` and `get_items` first.
  - `rag._citations`: chunk citations gain `"start_line"`/`"end_line"` (from metadata, when present) and `"quote"` = the first verified evidence entry's quote (else absent). Chain/article/graph citations unchanged except chains also lift `"quote"` when their evidence has a verified entry.

- [ ] **Step 1: Write the failing tests**

`tests/test_models.py` append:

```python
def test_search_result_to_dict_lifts_evidence():
    import json

    from opendomainmcp.models import SearchResult

    ev = [{"claim": "c", "quote": "q", "source": "a.py",
           "start_line": 2, "end_line": 2, "verified": True}]
    r = SearchResult(id="i", text="t", score=0.5,
                     metadata={"evidence": json.dumps(ev), "kind": "code"})
    d = r.to_dict()
    assert d["evidence"] == ev
    r2 = SearchResult(id="i", text="t", score=0.5, metadata={"kind": "code"})
    assert "evidence" not in r2.to_dict()
```

(adapt the SearchResult constructor to its real signature). `tests/test_rag.py` append (follow its existing citation-test style):

```python
def test_citations_include_lines_and_quote():
    import json

    from opendomainmcp.models import SearchResult
    from opendomainmcp.query.rag import _citations

    ev = [{"claim": "c", "quote": "if (amt < 0) throw", "source": "A.java",
           "start_line": 12, "end_line": 12, "verified": True}]
    r = SearchResult(id="i", text="t", score=0.9,
                     metadata={"kind": "code", "source": "A.java",
                               "start_line": 10, "end_line": 20,
                               "evidence": json.dumps(ev)})
    cite = _citations([r])[0]
    assert cite["start_line"] == 10 and cite["end_line"] == 20
    assert cite["quote"] == "if (amt < 0) throw"
```

`tests/test_views.py` append: a view-tool test that upserts a chunk whose knowledge carries evidence and asserts the run_view_tool result dicts contain the lifted `"evidence"` list (follow the file's existing store/view fixtures).

- [ ] **Step 2: Run tests to verify they fail** (commands as usual)

- [ ] **Step 3: Implement** per Interfaces (use `parse_evidence_field` from Task 1 everywhere; no duplicate JSON parsing logic).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_models.py tests/test_rag.py tests/test_views.py tests/test_advisor.py tests/test_api.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/models.py src/opendomainmcp/query/rag.py src/opendomainmcp/store/chroma_store.py tests/test_models.py tests/test_rag.py tests/test_views.py
git commit -m "feat: lift evidence into API results and line-precise citations"
```

---

### Task 9: SPA — Review evidence panel + Graph entity evidence

**Files:**
- Modify: `web/src/pages/Review.tsx`, `web/src/pages/Graph.tsx` (entity detail), possibly `web/src/api.ts` (types only)
- Test: `npm run build` must pass; extend the Playwright spec ONLY if an existing review-page spec exists (check `web/e2e/` or `web/tests/` — otherwise skip e2e, build check suffices)

**Interfaces:**
- Consumes: Task 8 payloads (`item.evidence` list; graph entity `evidence` list).
- Produces:
  - **Review card:** when an item has `evidence` (lifted list) or metadata `evidence_status`, render a status badge (`verified` green / `partial` amber / `unverified` red — reuse the page's existing badge/tag styling; read the file first and match its conventions) and a collapsible "Evidence (n)" section listing each entry: `quote` in a `<code>`/monospace block, `source:start_line-end_line` beneath (or "unverified" when lines are null). Unverified entries get the red badge inline.
  - **Graph entity detail:** when the selected entity payload has non-empty `evidence`, render the same quote + file:line list under the existing detail fields.
  - Match existing component style exactly (plain useState collapse, existing CSS classes; no new deps).

- [ ] **Step 1: Read both pages fully**; identify the item-card render block in Review.tsx and the entity-detail block in Graph.tsx.
- [ ] **Step 2: Implement** the two panels.
- [ ] **Step 3: Build**

Run: `cd web && npm run build`
Expected: clean build (tsc + vite), output regenerated under `src/opendomainmcp/api/static/`.

- [ ] **Step 4: Commit**

```bash
git add web/src src/opendomainmcp/api/static
git commit -m "feat: evidence panels on Review cards and Graph entity detail"
```

(If the built static assets are gitignored, commit only web/src — check `.gitignore` first.)

---

## Self-review notes

- **Spec coverage:** Evidence model with the spec's exact stored shape ✔ (T1); extraction contract for chunk AND chain prompts ✔ (T3, T4 — chain-level conclusions get evidence via deterministic member derivation, T6); deterministic verifier with whitespace tolerance and never-drop ✔ (T2 — line numbers computed rather than corrected, documented deviation); confidence penalty + `evidence_status` + fail-loud ingest counts ✔ (T2, T5); Chroma JSON-string storage + filterable status ✔ (T1, T5); graph entity/edge evidence columns with existing-install migration ✔ (T7; codegraph call-site quotes deferred — those edges already carry file:line); all four surfaces ✔ (T8 lifts once for items/search/MCP/advisor + line-precise RAG citations; T9 Review + Graph browser panels). Entity-merge lineage was already preserved by `entity_chunks` (4A) — no further work needed; noted here so the reviewer doesn't hunt for it.
- **Placeholder scan:** T8's get_items-lifting and T9's e2e are conditional on reading the actual code first — both name exactly what to check and what must hold. Everything else is complete code.
- **Type consistency:** the evidence entry dict shape is defined once (Global Constraints) and used identically in T1–T8; `parse_evidence_field` (T1) is the single JSON-parsing point reused in T7/T8; `verify_evidence`/`apply_penalty` (T2) used in T5/T6.
- **Known risks:** `_parse_evidence` import across modules in T4 (if T3's reviewer renames it, T4 adapts); Review.tsx has no component tests — build + visual conventions only (Playwright only if a spec already exists).

## Post-review addendum

- Edge evidence is surfaced in `neighbors()` payloads (`edge_evidence`, Maria + Fake); a dedicated edge-detail UI element in the SPA is DEFERRED (no edge-detail view exists yet).
- Deferred to enhancement #3 (review priority queue): the spec's "unverified items sort first" — the `evidence_status` filter field added here is the hook it needs.
