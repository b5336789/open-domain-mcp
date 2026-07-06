# Ingest Input Filtering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deterministic, zero-token filtering that keeps test/generated/vendored files out of the ingest pipeline, configurable at three layers (built-in defaults, `ODM_INGEST_EXCLUDE` setting, CLI flags), with every skipped file reported alongside the rule that skipped it.

**Architecture:** A single new `IngestFilter` class (`src/opendomainmcp/ingest/filters.py`) owns the decision "should this file be ingested". `Pipeline._ingest()` applies it to both directory walks and single-file ingests; `Pipeline.list_files()` applies it too so the Task Center enumeration matches. Filtered files land in a new `IngestReport.filtered` list and emit a `filter` progress event. The existing `_sync_deletions` prunes now-excluded files for free (they are absent from the `seen` set); we only label the prune reason.

**Tech Stack:** Python ≥ 3.11, stdlib `fnmatch`, pydantic-settings (existing `config.py` pattern), pytest (offline, fakes from `tests/conftest.py`).

**Spec:** `docs/superpowers/specs/2026-07-06-ingest-filtering-design.md`

## Global Constraints

- All tests offline — no network, no model download (`pytest` from repo root, venv at `.venv`).
- Fail Loud: a filtered file must always appear in the report with its matching rule; never silently dropped.
- Zero LLM/token cost in filtering; head sniff reads at most the first 10 lines.
- Precedence: CLI > settings > built-in; `--no-default-excludes` disables built-in globs **and** the head sniff for that run.
- Match existing style: snake_case, dataclasses, injected dependencies, module docstrings explaining "why".
- Every ingest surface (CLI, web, MCP) goes through `Pipeline` — do not add per-surface filtering.

---

### Task 1: `IngestFilter` — glob rules

**Files:**
- Create: `src/opendomainmcp/ingest/filters.py`
- Test: `tests/test_ingest_filters.py`

**Interfaces:**
- Produces: `IngestFilter(extra_excludes: Sequence[str] = (), use_defaults: bool = True)` with method `exclusion_reason(path: Path, root: Optional[Path] = None) -> Optional[str]` (returns the matching pattern string, or `None` to ingest). Also `DEFAULT_EXCLUDES: tuple[str, ...]`, `GENERATED_RULE = "generated-marker"`, and helper `_parse_exclude_spec(spec: str) -> list[str]`.
- Pattern semantics (document in the module docstring): a pattern ending in `/` matches when any **directory segment** of the root-relative path fnmatches it; any other pattern fnmatches the **basename** or the full relative posix path.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ingest_filters.py
"""IngestFilter: deterministic business-meaning filtering (spec 2026-07-06)."""

from pathlib import Path

from opendomainmcp.ingest.filters import IngestFilter, _parse_exclude_spec


def test_default_excludes_match_test_and_lock_files(tmp_path):
    flt = IngestFilter()
    root = tmp_path
    assert flt.exclusion_reason(root / "src" / "test_billing.py", root) == "test_*.py"
    assert flt.exclusion_reason(root / "app" / "billing.spec.ts", root) == "*.spec.ts"
    assert (
        flt.exclusion_reason(root / "package-lock.json", root) == "package-lock.json"
    )
    assert flt.exclusion_reason(root / "static" / "app.min.js", root) == "*.min.js"


def test_directory_patterns_match_any_segment(tmp_path):
    flt = IngestFilter()
    root = tmp_path
    assert flt.exclusion_reason(root / "tests" / "helper.py", root) == "tests/"
    assert flt.exclusion_reason(root / "pkg" / "vendor" / "lib.go", root) == "vendor/"
    assert flt.exclusion_reason(root / "db" / "migrations" / "0001.sql", root) == "migrations/"
    # A *file* named like a dir pattern is not a directory match.
    assert flt.exclusion_reason(root / "src" / "vendor", root) is None


def test_business_files_pass(tmp_path):
    flt = IngestFilter()
    root = tmp_path
    for rel in ("src/billing.py", "src/OrderService.java", "docs/pricing.md",
                "frontend/checkout.ts"):
        assert flt.exclusion_reason(root / Path(rel), root) is None


def test_extra_excludes_layer_over_defaults(tmp_path):
    flt = IngestFilter(extra_excludes=("*.sql", "legacy/"))
    root = tmp_path
    assert flt.exclusion_reason(root / "proc" / "billing.sql", root) == "*.sql"
    assert flt.exclusion_reason(root / "legacy" / "old.java", root) == "legacy/"
    # defaults still apply
    assert flt.exclusion_reason(root / "test_x.py", root) == "test_*.py"


def test_no_defaults_mode_keeps_only_user_rules(tmp_path):
    flt = IngestFilter(extra_excludes=("*.sql",), use_defaults=False)
    root = tmp_path
    assert flt.exclusion_reason(root / "test_x.py", root) is None
    assert flt.exclusion_reason(root / "a.sql", root) == "*.sql"


def test_parse_exclude_spec_splits_commas_and_newlines():
    assert _parse_exclude_spec("*.sql, legacy/\n*.tmp\n\n") == [
        "*.sql", "legacy/", "*.tmp"
    ]
    assert _parse_exclude_spec("") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ingest_filters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'opendomainmcp.ingest.filters'`

- [ ] **Step 3: Write the implementation**

```python
# src/opendomainmcp/ingest/filters.py
"""Deterministic ingest filtering: which files are worth indexing.

Test code, generated artifacts, lock files and vendored dependencies carry
little business meaning; they waste extraction tokens and pollute retrieval.
Rules are gitignore-flavoured globs plus a generated-file head sniff — all
zero-token, and every filtered file is reported with the rule that dropped
it (Fail Loud).

Pattern semantics: a pattern ending in ``/`` matches when any directory
segment of the root-relative path fnmatches it; any other pattern fnmatches
the basename or the full relative posix path. Precedence of rule sources is
handled by the caller (CLI > settings > built-in); within a filter, first
matching pattern wins.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Optional, Sequence

# Built-in globs for files that carry no business meaning.
DEFAULT_EXCLUDES: tuple[str, ...] = (
    # tests
    "tests/", "__tests__/", "fixtures/", "__snapshots__/",
    "test_*.py", "*_test.py", "conftest.py",
    "*.test.ts", "*.test.js", "*.spec.ts", "*.spec.js",
    # generated
    "*.min.js", "*.min.css", "*.map",
    "*_pb2.py", "*.pb.go", "*.generated.*",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "uv.lock", "Cargo.lock", "Pipfile.lock", "composer.lock", "Gemfile.lock",
    # vendored
    "vendor/", "third_party/", "migrations/",
)

GENERATED_RULE = "generated-marker"


def _parse_exclude_spec(spec: str) -> list[str]:
    """Split a comma/newline-separated glob list, dropping empties."""
    parts: list[str] = []
    for line in spec.splitlines():
        parts.extend(p.strip() for p in line.split(","))
    return [p for p in parts if p]


def _relative_to(path: Path, root: Optional[Path]) -> Path:
    if root is not None:
        try:
            return path.relative_to(root)
        except ValueError:
            pass
    return Path(path.name)


class IngestFilter:
    """Decides whether a file should be ingested. One instance per run."""

    def __init__(self, extra_excludes: Sequence[str] = (),
                 use_defaults: bool = True):
        base = DEFAULT_EXCLUDES if use_defaults else ()
        self._patterns: tuple[str, ...] = tuple(base) + tuple(extra_excludes)

    def exclusion_reason(self, path: Path,
                         root: Optional[Path] = None) -> Optional[str]:
        """The rule that excludes ``path``, or ``None`` if it should be ingested."""
        rel = _relative_to(path, root)
        name = rel.name
        dir_segments = rel.parts[:-1]
        for pattern in self._patterns:
            if pattern.endswith("/"):
                seg = pattern.rstrip("/")
                if any(fnmatch(part, seg) for part in dir_segments):
                    return pattern
            elif fnmatch(name, pattern) or fnmatch(rel.as_posix(), pattern):
                return pattern
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ingest_filters.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/ingest/filters.py tests/test_ingest_filters.py
git commit -m "feat: IngestFilter glob rules for business-meaning filtering"
```

---

### Task 2: Generated-marker head sniff

**Files:**
- Modify: `src/opendomainmcp/ingest/filters.py`
- Test: `tests/test_ingest_filters.py` (append)

**Interfaces:**
- Consumes: `IngestFilter` from Task 1.
- Produces: `exclusion_reason()` additionally returns `GENERATED_RULE` (`"generated-marker"`) when the first 10 lines contain a marker; sniffing is active only when `use_defaults=True`. Adds `GENERATED_MARKERS: tuple[str, ...]` and private `_has_generated_marker(path) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ingest_filters.py`:

```python
def test_generated_marker_in_head_is_filtered(tmp_path):
    f = tmp_path / "Model.java"
    f.write_text("// Code generated by protoc-gen. DO NOT EDIT.\nclass Model {}\n")
    assert IngestFilter().exclusion_reason(f, tmp_path) == "generated-marker"


def test_generated_marker_is_case_insensitive_and_at_generated(tmp_path):
    f = tmp_path / "api.ts"
    f.write_text("/* @GENERATED by codegen */\nexport const x = 1\n")
    assert IngestFilter().exclusion_reason(f, tmp_path) == "generated-marker"


def test_marker_beyond_head_lines_is_not_sniffed(tmp_path):
    f = tmp_path / "billing.py"
    f.write_text("\n" * 15 + "# do not edit this section by hand\n")
    assert IngestFilter().exclusion_reason(f, tmp_path) is None


def test_no_defaults_mode_disables_sniff(tmp_path):
    f = tmp_path / "gen.py"
    f.write_text("# autogenerated\n")
    flt = IngestFilter(use_defaults=False)
    assert flt.exclusion_reason(f, tmp_path) is None


def test_missing_file_does_not_raise(tmp_path):
    # exclusion_reason may be probed on paths that vanish mid-run
    assert IngestFilter().exclusion_reason(tmp_path / "gone.py", tmp_path) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ingest_filters.py -v`
Expected: the 4 new sniff tests FAIL (marker files pass through); `test_no_defaults_mode_disables_sniff` and `test_missing_file_does_not_raise` may already pass — that is fine.

- [ ] **Step 3: Implement the sniff**

In `src/opendomainmcp/ingest/filters.py`, add below `GENERATED_RULE`:

```python
# Case-insensitive markers scanned in the first _SNIFF_LINES lines of a file.
GENERATED_MARKERS: tuple[str, ...] = (
    "@generated", "code generated by", "do not edit", "autogenerated",
    "auto-generated",
)
_SNIFF_LINES = 10


def _has_generated_marker(path: Path) -> bool:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for _ in range(_SNIFF_LINES):
                line = fh.readline()
                if not line:
                    break
                lowered = line.lower()
                if any(marker in lowered for marker in GENERATED_MARKERS):
                    return True
    except OSError:
        return False
    return False
```

In `IngestFilter.__init__`, record the flag:

```python
        self._sniff = use_defaults
```

At the end of `exclusion_reason`, before `return None`:

```python
        if self._sniff and _has_generated_marker(path):
            return GENERATED_RULE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ingest_filters.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/ingest/filters.py tests/test_ingest_filters.py
git commit -m "feat: generated-marker head sniff in IngestFilter"
```

---

### Task 3: `ODM_INGEST_EXCLUDE` setting + `IngestFilter.from_settings`

**Files:**
- Modify: `src/opendomainmcp/config.py` (add field after `ingest_root` at line ~67; add to `EDITABLE_FIELDS` tuple at line ~27)
- Modify: `src/opendomainmcp/ingest/filters.py`
- Modify: `.env.example`
- Test: `tests/test_ingest_filters.py` (append)

**Interfaces:**
- Consumes: `IngestFilter`, `_parse_exclude_spec` (Tasks 1–2); `Settings` (pydantic, env prefix `ODM_`).
- Produces: `Settings.ingest_exclude: str = ""` (runtime-editable) and `IngestFilter.from_settings(settings, extra_excludes: Sequence[str] = (), use_defaults: bool = True) -> IngestFilter`. Layering: built-ins + parsed setting + per-run extras.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ingest_filters.py`:

```python
def test_from_settings_layers_setting_and_run_excludes(tmp_path):
    from opendomainmcp.config import Settings

    settings = Settings(ingest_exclude="*.sql, legacy/")
    flt = IngestFilter.from_settings(settings, extra_excludes=("*.tmp",))
    root = tmp_path
    assert flt.exclusion_reason(root / "p.sql", root) == "*.sql"        # setting
    assert flt.exclusion_reason(root / "x.tmp", root) == "*.tmp"        # per-run
    assert flt.exclusion_reason(root / "test_a.py", root) == "test_*.py"  # built-in


def test_ingest_exclude_is_runtime_editable():
    from opendomainmcp.config import EDITABLE_FIELDS

    assert "ingest_exclude" in EDITABLE_FIELDS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ingest_filters.py -k "from_settings or runtime_editable" -v`
Expected: FAIL — `AttributeError: ... no attribute 'from_settings'` / assertion on `EDITABLE_FIELDS`

- [ ] **Step 3: Implement**

`src/opendomainmcp/config.py` — inside `Settings`, directly after the `ingest_root` field (line ~67):

```python
    # Ingest filtering: comma/newline-separated extra exclude globs layered
    # over the built-in defaults (tests/generated/vendored — see
    # ingest/filters.py). Runtime-editable from the web Settings page.
    ingest_exclude: str = ""
```

`EDITABLE_FIELDS` — add one entry (keep tuple order, append after `"retrieve_include_graph"`):

```python
    "ingest_exclude",
```

`src/opendomainmcp/ingest/filters.py` — add classmethod to `IngestFilter`:

```python
    @classmethod
    def from_settings(cls, settings, extra_excludes: Sequence[str] = (),
                      use_defaults: bool = True) -> "IngestFilter":
        """Built-ins + ``ODM_INGEST_EXCLUDE`` + per-run extras (CLI layer)."""
        configured = _parse_exclude_spec(getattr(settings, "ingest_exclude", ""))
        return cls(tuple(configured) + tuple(extra_excludes), use_defaults)
```

`.env.example` — add next to the other `ODM_` ingest settings:

```bash
# Extra ingest exclude globs (comma/newline-separated), layered over built-in
# defaults that skip tests/generated/vendored files. Pattern ending in "/"
# matches a directory segment. Example: ODM_INGEST_EXCLUDE=*.sql,legacy/
#ODM_INGEST_EXCLUDE=
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ingest_filters.py tests/test_config.py -v`
Expected: all pass (test_config.py guards `EDITABLE_FIELDS`/overrides behavior — if it asserts an exact field list, update it to include `ingest_exclude`).

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/config.py src/opendomainmcp/ingest/filters.py .env.example tests/test_ingest_filters.py tests/test_config.py
git commit -m "feat: ODM_INGEST_EXCLUDE setting layered into IngestFilter"
```

---

### Task 4: Pipeline integration — filter, report, progress

**Files:**
- Modify: `src/opendomainmcp/ingest/pipeline.py` (`IngestReport` line ~56; `ingest_path` line ~82; `_ingest` line ~104; `list_files` line ~157)
- Test: `tests/test_pipeline.py` (append)

**Interfaces:**
- Consumes: `IngestFilter.from_settings` (Task 3).
- Produces:
  - `IngestReport.filtered: list` of `{"path": str, "rule": str}`;
  - `Pipeline.ingest_path(..., exclude: Optional[Sequence[str]] = None, use_default_excludes: bool = True)` (new keyword-only-style optional params, default behavior = filtering ON);
  - a `filter` progress event per filtered file;
  - `Pipeline.list_files()` returns the post-filter list (Task Center parity).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:

```python
def _make_mixed_corpus(root):
    (root / "billing.py").write_text("def charge(amount):\n    return amount\n")
    (root / "test_billing.py").write_text("def test_charge():\n    assert True\n")
    (root / "vendor").mkdir()
    (root / "vendor" / "lib.py").write_text("def vendored():\n    pass\n")
    (root / "gen.py").write_text("# autogenerated\ndef g():\n    pass\n")
    (root / "package-lock.json").write_text("{}")


def test_ingest_filters_non_business_files(pipeline, store, tmp_path):
    _make_mixed_corpus(tmp_path)
    report = pipeline.ingest_path(tmp_path)

    assert report.files_indexed == 1  # only billing.py
    rules = {f["path"].split("/")[-1]: f["rule"] for f in report.filtered}
    assert rules["test_billing.py"] == "test_*.py"
    assert rules["lib.py"] == "vendor/"
    assert rules["gen.py"] == "generated-marker"
    assert rules["package-lock.json"] == "package-lock.json"
    sources = store.get_all_sources()
    assert all("billing.py" in s for s in sources)


def test_filter_report_is_serialised_and_events_emitted(pipeline, tmp_path):
    _make_mixed_corpus(tmp_path)
    events = []
    report = pipeline.ingest_path(tmp_path, progress=events.append)
    assert "filtered" in report.to_dict()
    filter_events = [e for e in events if e["stage"] == "filter"]
    assert len(filter_events) == len(report.filtered) == 4


def test_per_run_exclude_and_no_defaults(pipeline, tmp_path):
    _make_mixed_corpus(tmp_path)
    report = pipeline.ingest_path(tmp_path, exclude=["billing.*"])
    assert report.files_indexed == 0
    assert any(f["rule"] == "billing.*" for f in report.filtered)

    report2 = pipeline.ingest_path(tmp_path, use_default_excludes=False)
    # only the binary-safety and content rules of the loader apply now
    assert report2.files_indexed == 5  # all .py/.json files ingested


def test_single_file_ingest_is_filtered_with_report(pipeline, tmp_path):
    f = tmp_path / "test_only.py"
    f.write_text("def test_x():\n    pass\n")
    report = pipeline.ingest_path(f)
    assert report.files_indexed == 0
    assert report.filtered == [{"path": str(f), "rule": "test_*.py"}]


def test_list_files_applies_filter(pipeline, tmp_path):
    _make_mixed_corpus(tmp_path)
    files = pipeline.list_files(tmp_path)
    assert [Path(f).name for f in files] == ["billing.py"]
```

Add `from pathlib import Path` at the top of `tests/test_pipeline.py` if not already imported.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -k "filter or list_files_applies or single_file_ingest" -v`
Expected: FAIL — `IngestReport` has no `filtered`; `ingest_path` got unexpected keyword `exclude`

- [ ] **Step 3: Implement**

`src/opendomainmcp/ingest/pipeline.py`:

Import (top of file, after the loader import):

```python
from .filters import IngestFilter
```

`IngestReport` — add field after `skipped`:

```python
    filtered: list = field(default_factory=list)  # [{"path", "rule"}] excluded by filter rules
```

`ingest_path` — extend the signature and pass through (both `_ingest` calls):

```python
    def ingest_path(
        self,
        path: str | Path,
        progress: Optional[Progress] = None,
        sync: bool = False,
        allowed_root: Optional[str | Path] = None,
        checkpoint=None,
        exclude: Optional[Sequence[str]] = None,
        use_default_excludes: bool = True,
    ) -> IngestReport:
```

(add `Sequence` to the `typing` import), build the filter before `prepared_source`:

```python
        ingest_filter = IngestFilter.from_settings(
            self._settings, tuple(exclude or ()), use_default_excludes
        )
```

and pass `ingest_filter=ingest_filter` to both `self._ingest(...)` calls.

`_ingest` — accept `ingest_filter: IngestFilter` as a new parameter. After the existing `self._filter_within(...)` block (line ~129), apply:

```python
        base = path if path.is_dir() else path.parent
        files = self._apply_ingest_filter(files, base, report, progress, ingest_filter)
```

New method next to `_filter_within`:

```python
    def _apply_ingest_filter(self, files, base: Path, report: IngestReport,
                             progress: Optional[Progress],
                             ingest_filter: IngestFilter):
        """Drop files excluded by filter rules — always reported, never silent."""
        kept = []
        for f in files:
            rule = ingest_filter.exclusion_reason(f, base)
            if rule is None:
                kept.append(f)
            else:
                report.filtered.append({"path": str(f), "rule": rule})
                self._emit(progress, "filter", str(f), detail=rule)
        return kept
```

`list_files` — apply the settings-level filter (no per-run overrides in this path):

```python
    def list_files(self, path: str | Path) -> list[str]:
        """The file paths ingest_path would process, in processing order.
        Used by the Task Center to enumerate child entries up front."""
        p = Path(path)
        flt = IngestFilter.from_settings(self._settings)
        if p.is_dir():
            return [str(f) for f in self._walk(p)
                    if flt.exclusion_reason(f, p) is None]
        if p.is_file():
            return [str(p)] if flt.exclusion_reason(p, p.parent) is None else []
        return []
```

- [ ] **Step 4: Run the pipeline test file**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`
Expected: all pass, including the pre-existing tests (their corpus — `calc.py`, `notes.md`, `image.bin` — matches no default rule).

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/ingest/pipeline.py tests/test_pipeline.py
git commit -m "feat: apply IngestFilter in pipeline with fail-loud filtered report"
```

---

### Task 5: `--sync` prunes newly-excluded files, with visible reason

**Files:**
- Modify: `src/opendomainmcp/ingest/pipeline.py` (`_sync_deletions` line ~381)
- Test: `tests/test_pipeline.py` (append)

**Interfaces:**
- Consumes: Task 4 (filtered files are absent from the `seen` set, so `_sync_deletions` already prunes them — this task only labels why).
- Produces: prune progress events carry `detail="excluded"` when the source file still exists on disk (i.e. it was filtered, not deleted), `detail="file removed"` otherwise.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline.py`:

```python
def test_sync_prunes_chunks_of_newly_excluded_files(pipeline, store, tmp_path):
    f = tmp_path / "was_business.py"
    f.write_text("def rule():\n    return 1\n")
    pipeline.ingest_path(tmp_path)
    assert store.get_ids_for_source(str(f))

    # The file becomes excluded (per-run rule) — sync must prune its chunks.
    events = []
    report = pipeline.ingest_path(tmp_path, sync=True, exclude=["was_business.py"],
                                  progress=events.append)
    assert report.chunks_pruned > 0
    assert not store.get_ids_for_source(str(f))
    prune_events = [e for e in events if e["stage"] == "prune"]
    assert any(e["detail"] == "excluded" for e in prune_events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py::test_sync_prunes_chunks_of_newly_excluded_files -v`
Expected: FAIL on the `detail == "excluded"` assertion (pruning itself already works; the detail still says `file removed`)

- [ ] **Step 3: Implement**

In `_sync_deletions`, replace the emit line:

```python
                self._emit(progress, "prune", source, detail="file removed")
```

with:

```python
                reason = "excluded" if Path(source).exists() else "file removed"
                self._emit(progress, "prune", source, detail=reason)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/ingest/pipeline.py tests/test_pipeline.py
git commit -m "feat: label sync prunes of excluded files as 'excluded'"
```

---

### Task 6: CLI flags `--exclude` / `--no-default-excludes` + report output

**Files:**
- Modify: `src/opendomainmcp/cli.py` (`_cmd_ingest` line ~12; parser line ~155)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `Pipeline.ingest_path(exclude=..., use_default_excludes=...)` (Task 4).
- Produces: `opendomainmcp ingest PATH [--exclude GLOB]... [--no-default-excludes]`; stderr shows `filter` events; stdout summarizes `Filtered N file(s) by exclude rules.`

- [ ] **Step 1: Write the failing test**

Look at the top of `tests/test_cli.py` first to reuse its existing fixture/invocation pattern (it drives `build_parser()` + `args.func(ctx, args)` with the fake-backed context). Following that pattern, append:

```python
def test_ingest_cli_passes_filter_flags(cli_ctx, tmp_path, capsys):
    from opendomainmcp.cli import build_parser

    (tmp_path / "billing.py").write_text("def charge():\n    return 1\n")
    (tmp_path / "test_billing.py").write_text("def test_c():\n    pass\n")

    parser = build_parser()
    args = parser.parse_args(["ingest", str(tmp_path), "--exclude", "*.md"])
    assert args.exclude == ["*.md"]
    assert args.no_default_excludes is False
    rc = args.func(cli_ctx, args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Filtered 1 file(s)" in out  # test_billing.py

    args2 = parser.parse_args(["ingest", str(tmp_path), "--no-default-excludes"])
    assert args2.no_default_excludes is True
    rc = args2.func(cli_ctx, args2)
    assert rc == 0
    assert "Filtered" not in capsys.readouterr().out
```

(If `tests/test_cli.py` names its context fixture differently — e.g. `ctx` — use that name; keep the body identical.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k filter_flags -v`
Expected: FAIL — `argparse` error `unrecognized arguments: --exclude`

- [ ] **Step 3: Implement**

`src/opendomainmcp/cli.py` — in `build_parser()` after the `--sync` argument (line ~162):

```python
    p_ingest.add_argument(
        "--exclude", action="append", default=[], metavar="GLOB",
        help="Extra exclude pattern for this run (repeatable; layered over "
             "built-in defaults and ODM_INGEST_EXCLUDE)",
    )
    p_ingest.add_argument(
        "--no-default-excludes", action="store_true",
        help="Disable the built-in exclude list and generated-marker sniff",
    )
```

`_cmd_ingest` — pass the flags and surface the report; also show `filter` events on stderr:

```python
def _cmd_ingest(ctx, args) -> int:
    def progress(event):
        if event["stage"] in ("load", "skip", "filter", "error", "done"):
            detail = f" - {event['detail']}" if event["detail"] else ""
            print(f"[{event['stage']:>6}] {event['path']}{detail}", file=sys.stderr)

    report = ctx.pipeline.ingest_path(
        args.path, progress=progress, sync=args.sync,
        exclude=args.exclude, use_default_excludes=not args.no_default_excludes,
    )
    print(f"Indexed {report.files_indexed} files / {report.chunks_indexed} chunks.")
    if report.chunks_pruned:
        print(f"Pruned {report.chunks_pruned} stale chunk(s).")
    if report.filtered:
        print(f"Filtered {len(report.filtered)} file(s) by exclude rules.")
    if report.skipped:
        print(f"Skipped {len(report.skipped)} file(s).")
    if report.errors:
        print(f"Errors: {len(report.errors)}", file=sys.stderr)
        for err in report.errors:
            print(f"  {err['path']}: {err['error']}", file=sys.stderr)
    return 0
```

(The stage column widens from `>5` to `>6` so `filter` aligns.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/opendomainmcp/cli.py tests/test_cli.py
git commit -m "feat: ingest CLI --exclude / --no-default-excludes flags"
```

---

### Task 7: Full-suite verification

**Files:**
- Possibly modify: any test whose fixture corpus now collides with default excludes (e.g. a fixture file literally named `test_*.py` ingested by an unrelated test).

**Interfaces:**
- Consumes: everything above.
- Produces: a green offline suite; no behavioral regressions outside the spec.

- [ ] **Step 1: Run the full suite**

Run: `.venv/bin/python -m pytest`
Expected: all pass. If a pre-existing test fails because its fixture corpus is now filtered (its ingested files match a default rule), that is a test-fixture collision, not a product bug.

- [ ] **Step 2: Fix collisions minimally**

For each collision, rename the fixture file to a non-matching name (e.g. `test_data.py` → `sample_data.py`) **or** pass `use_default_excludes=False` in that test — whichever preserves the test's original intent. Do not weaken the filter to accommodate a fixture.

- [ ] **Step 3: Re-run and commit**

Run: `.venv/bin/python -m pytest`
Expected: all pass

```bash
git add -A tests
git commit -m "test: full-suite pass with ingest filtering enabled"
```

---

## Self-review notes

- **Spec coverage:** built-in globs (Task 1), head sniff (Task 2), `ODM_INGEST_EXCLUDE` + runtime-editable + `.env.example` (Task 3), CLI layer + precedence (Tasks 4/6), fail-loud `filtered` report + progress (Task 4), single-file + `list_files` parity (Task 4), `--sync` prune semantics with visible reason (Task 5). Out-of-scope items from the spec are not implemented anywhere. ✔
- **Web surface:** report serialization (`to_dict`) and the generic Settings editor pick up `filtered` / `ingest_exclude` without SPA changes; no web task needed. ✔
- **Type consistency:** `exclusion_reason(path, root) -> Optional[str]`, `from_settings(settings, extra_excludes, use_defaults)`, `ingest_path(..., exclude, use_default_excludes)` used identically across Tasks 1–6. ✔
