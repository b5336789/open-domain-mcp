# Document Export — Design Spec

**Date:** 2026-07-22
**Status:** Approved in brainstorming; awaiting implementation plan
**Depends on:** synthesis (Article), consensus (RuleItem, trust tiers), graph store
(workflows), tasks framework (background jobs)

## Problem

The platform now extracts and structures legacy-system domain knowledge —
synthesized Articles, consensus-validated RuleItems (with trust tiers and
evidence), and workflow steps in the graph — but everything lives in the vector
store and MariaDB, reachable only through search, the web UI, and MCP tools.
There is no way to produce the actual deliverable the corpus exists for:
**formatted documents** a team can read, print, version, and hand to
stakeholders.

Requirements settled in brainstorming:

- **Readers:** both engineers (need source locations, evidence quotes) and
  business/PM (need business-language prose). One set of documents, layered —
  not two parallel sets.
- **Format/surface:** Markdown; a directory tree plus a merged single file.
  Triggered from the CLI and downloadable as a zip from the web UI.
- **Quality handling:** export everything, badge each rule with its trust tier
  and corroboration count; `conflicted` rules go to a dedicated
  "needs clarification" chapter, never silently mixed in or dropped.
- **Language:** translate content to Chinese at export time via an LLM pass
  (stored content is English, produced by the extraction/synthesis prompts).
- **Organization:** business-oriented hierarchy — functional domains → main
  workflows → per-flow detail — produced by an LLM outline pass, with a
  deterministic flat layout as the no-LLM fallback.

## Design

### Module structure

New package `src/opendomainmcp/export/`, one file per stage, stages independent
and individually testable:

```
export/
├── __init__.py     # export_documents() entry point wiring the stages
├── collect.py      # store/graph → ExportBundle (read-only, zero LLM)
├── organize.py     # ExportBundle → Outline (LLM outline pass, cached)
├── translate.py    # ExportBundle → translated bundle (LLM pass, cached)
└── render.py       # (ExportBundle, Outline) → .md tree + merged file (pure templates)
```

Data flow:

```
collect(ctx) → ExportBundle
organize(bundle, llm, cache) → Outline          # skipped with --no-llm
translate(bundle, llm, cache) → ExportBundle    # skipped with --no-translate / --no-llm
render(bundle, outline, out_dir) → ExportReport
```

CLI and the web task runner are thin adapters over `export_documents()` — same
single-path principle as `build_context()`.

### collect.py

Pages the main collection with `ChromaStore.get_items`, splitting objects by
metadata `kind`: **Article** (synthesis output), **RuleItem** (consensus
output: statement, trust, corroborations, evidence, sources). Pulls workflows
from the graph store via `list_workflows` + `get_workflow` (steps ordered by
chunk_index/step_order). Also captures `stats()` and the readiness summary for
the index page. No transformation happens here; the output is a plain
`ExportBundle` dataclass so render can be tested on hand-built fixtures.

### organize.py — LLM outline pass

One LLM call per export (cached): input is the list of **titles + one-line
summaries** of every Article, Workflow, and Rule (never full bodies — token
cost stays small). The prompt asks for a JSON outline in business terms:

```
{ "domains": [ { "name": "訂單管理",
                 "flows": [ { "workflow": "<workflow name>",
                              "articles": ["<topic>", ...],
                              "rules": ["<rule id>", ...] } ],
                 "articles": [...], "rules": [...] } ],
  "unassigned": { "articles": [...], "workflows": [...], "rules": [...] } }
```

- Response is validated against the item ids actually in the bundle; unknown
  ids are dropped with a report warning, items the LLM failed to place are
  moved to `unassigned`.
- **Nothing is dropped:** unassigned items render into a "未分類" chapter and
  their count appears in the `ExportReport` (Fail Loud).
- Cached by `sha256` of the input listing → unchanged corpus never re-calls.
- `--no-llm` skips this stage: render falls back to the deterministic flat
  layout (by object type), which remains fully supported.

### translate.py — LLM translation pass

- Translator is an injectable callable `translate(text) -> str`; the default
  implementation reuses the extraction provider settings (`ANTHROPIC_API_KEY`
  / `ANTHROPIC_BASE_URL`); tests inject a fake.
- Cache at `<data_dir>/translation_cache.json`: key `sha256(source text)`,
  value translated text. Re-export translates only new/changed content.
- A per-item translation failure does not abort the export: the item keeps its
  original text, is visibly marked 〔未翻譯〕 in the document, and is recorded
  in `ExportReport.errors` (Fail Loud — the report states exactly how many
  items failed).
- `--no-translate` (or `--no-llm`) skips the pass entirely; export is then
  fully deterministic.

### render.py — document structure

With an outline (default):

```
<out_dir>/
├── index.md                  # system overview: stats, readiness, domain TOC,
│                             # generation time, trust legend
├── domains/
│   └── <領域>/               # e.g. 訂單管理
│       ├── README.md         # domain overview: functions, main-flow list,
│       │                     # key-rule digest
│       └── <主流程>.md        # main flow: step table → linked sub-flows and
│                             # articles per step → flow rules → tech appendix
├── misc/                     # unassigned items ("未分類"), if any
├── rules-conflicted.md       # "needs clarification" chapter: conflicted rules
│                             # with their conflicting sources side by side
└── handbook.md               # everything merged in domain order (concat + TOC)
```

Without an outline (`--no-llm` fallback): flat layout — `articles/<topic>.md`,
`workflows/<name>.md`, `rules.md` — same per-document templates.

Dual-audience layering, uniform across every document: **body text is written
for business readers** (article body, plain-language rule statements, workflow
step tables with preconditions); **every document ends with a fixed
"技術對照" appendix for engineers** (source file:line references, verbatim
evidence quotes, related chunk ids). Rules are grouped by trust tier with
badges (🟢 high / 🟡 normal) and corroboration counts; conflicted rules appear
only in `rules-conflicted.md`.

File names are slugified from topic/workflow/domain names with collision
suffixes (`-2`, `-3`). `handbook.md` is a pure concatenation of the rendered
sections plus a generated table of contents.

### Surfaces

- **CLI:** `opendomainmcp export --out DIR [--no-translate] [--no-llm]
  [--zip]` — synchronous, progress events printed like ingest.
- **Web:** a `run_export` runner registered in the existing tasks `RUNNERS`
  registry (translation over a large corpus runs minutes; must not block a
  request). `POST /api/export` creates the task; output is written to
  `<data_dir>/exports/<task_id>/` and zipped; `GET /api/export/{task_id}/download`
  streams the zip. Frontend: an "匯出文件" button plus download link on the
  existing tasks page — reuses the task-progress UI, no new page.

### Error handling

- Empty corpus (no Articles and no Rules) → explicit error telling the user to
  run `synthesize` / `consolidate` first; never an empty document set.
- Graph unwired (`NullGraphStore`) → workflow chapters are skipped and
  `index.md` states "graph store not enabled".
- All skips and failures aggregate into `ExportReport` (counts, errors,
  skipped, unassigned) — printed at the end of the CLI run and stored as the
  task result, same shape conventions as the ingest report.

## Testing (offline, business-logic-driven)

- **render:** hand-built `ExportBundle` + `Outline` fixtures → assert trust
  grouping is correct, conflicted rules land only in the dedicated chapter,
  the tech appendix carries file:line provenance, handbook contains every
  section, slug collisions resolve, fallback flat layout renders without an
  outline.
- **organize:** fake LLM → assert outline validation drops unknown ids with a
  warning, unplaced items land in `unassigned`, cache hit skips the call.
- **translate:** fake translator → assert cache hits skip calls, a single
  failure keeps the original text with the 〔未翻譯〕 marker and lands in the
  report.
- **collect:** existing fake-store pattern → assert all three object kinds are
  paged completely.
- **API:** TestClient through create-task → completion → zip download.

## Out of scope

- docx/PDF output (Markdown converts downstream via pandoc if ever needed).
- mkdocs/static-site generation, incremental doc builds, version diffing.
- Changing extraction/synthesis prompt language (the translate pass covers the
  Chinese requirement at export time; prompt-side language is a separate
  decision).
