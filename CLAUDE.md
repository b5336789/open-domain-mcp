# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Part 1: Core Coding Rules

- **Think Before Coding:** State your assumptions clearly. Discuss trade-offs and ask clarifying questions instead of guessing.
- **Simplicity First:** Write the minimum code required to solve the immediate problem. Avoid speculative features or premature abstractions.
- **Surgical Changes:** Only modify code directly relevant to the task. Do not "clean up" adjacent code, styling, or comments.
- **Goal-Driven Execution:** Define what success looks like (e.g., write a test and make it pass). Let the AI iterate until verification.

## Part 2: Agent Orchestration Rules

- **Keep Deterministic Work out of AI:** Do not make Claude handle raw string formatting or mechanical tasks; delegate these to standard code tools.
- **Manage Token Budgets:** Enforce strict limits on context usage (e.g., 4k per message, 30k per session) to prevent token bloat.
- **Resolve Style Conflicts:** If formatting or lint rules conflict, prioritize a single unified configuration and discard the rest.
- **Verify Context Before Editing:** Always read the surrounding code and imports before writing a single line to ensure compatibility.
- **Use Business-Logic Tests:** Write meaningful tests that validate actual intent and business outcomes, not just empty code coverage.
- **Create Step-by-Step Checkpoints:** For multi-step long tasks, halt at milestones to log what was done, what was verified, and what remains.
- **Match Existing Codebase Style:** Strictly follow established code conventions (e.g., snake_case or class components) even if you disagree.
- **Explicitly Fail Loud (Fail Loud):** If a step fails, skips data, or cannot be fully verified, report the error immediately. Never hide uncertainties.

## Part 3: Commands

Backend uses a venv at `.venv` (Python ≥ 3.11). Activate it, or use `./run.sh` which loads `.env` first so credentials reach the SDK.

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# Tests (all offline — no network, no model download)
pytest                                  # full suite
pytest tests/test_pipeline.py           # one file
pytest tests/test_pipeline.py::test_x   # one test
pytest -k "hybrid or rrf"               # by keyword
pytest -m integration                   # graph/MariaDB tests (need live GRAPH_DB_* env)

# Run the surfaces via run.sh (any subcommand other than web/server passes to the CLI)
./run.sh web                            # FastAPI + SPA → http://127.0.0.1:8000
./run.sh server                         # MCP server over stdio
./run.sh ingest ./path [--sync]
./run.sh search "query" --top-k 5
./run.sh ask "question"
# Console scripts when venv active: opendomainmcp / opendomainmcp-server /
#   opendomainmcp-view --view product / opendomainmcp-web

# Frontend (only needed to rebuild the dashboard)
cd web
npm run dev      # Vite dev server, proxies /api → 127.0.0.1:8000
npm run build    # outputs to src/opendomainmcp/api/static/ (served by opendomainmcp-web)
npm run test:e2e # Playwright
```

`pytest` markers live in `pyproject.toml`; `integration` needs a live MariaDB.

## Part 4: Architecture

A **domain-knowledge workflow platform**: ingest documents or source code → chunk →
LLM-enrich → embed → store in a vector DB → query via CLI, MCP server, or web UI.
The README has full mermaid diagrams; this section is the orientation map. Note the
code has grown past the README — the graph, views, advisor, and auth subsystems below
are real but lightly covered there.

### The single source of truth

Every surface (CLI `cli.py`, MCP `server.py`, web `api/app.py`) is a thin adapter
that calls **`context.py:build_context()`**, which assembles a `Context` of
`{ settings, store, pipeline, graph }` from `Settings`. There is exactly one
ingestion/retrieval path — change behavior there, not per-surface. Dependencies
(embedder, extractor, reranker, graph store) are **injected**, which is what lets
the whole stack run offline in tests by swapping in fakes.

### Ingestion pipeline (`ingest/pipeline.py`)

`Pipeline.ingest_path()` runs five stages per file, emitting a `progress` dict per
stage (CLI/web stream it):
1. **LOAD** (`loader.py`) — detect type by extension. Code → tree-sitter; pdf/docx/html → text; UTF-8 text passes through; binary/non-UTF-8 is **skipped and reported**, never silently dropped.
2. **SPLIT** — code → `code_splitter.py` (AST at function/class boundaries, line-window fallback for unbundled grammars); text → `text_splitter.py` (recursive, with overlap).
3. **EXTRACT** (`extract/knowledge.py`) — `ClaudeExtractor` (or `NullExtractor`) produces a `KnowledgeUnit { summary, concepts, relations }` per chunk via `ThreadPoolExecutor`. Per-chunk failures are recorded in the report (Fail Loud).
4. **EMBED + STORE** — `Chunk.embedding_text()` appends summary + concepts to raw text *before* embedding (so retrieval matches meaning), then `ChromaStore.upsert()`.

Chunk `id` is a content hash (`sha256` of `source:start-end + text`) → ingestion is
idempotent. PRUNE drops stale ids for a re-ingested source; `--sync` on a directory
also removes chunks for files no longer present. Non-local sources (Git URL, `.zip`)
are materialized under `<data_dir>/.sources/` by `ingest/sources.py` and confined as
the `allowed_root`. Specialized ingesters: `openapi.py`, `graphql.py`, `wiki.py`.

### Retrieval (`store/chroma_store.py` + `retrieval/`)

`ChromaStore.search()` in `hybrid` mode runs dense (Chroma cosine) and lexical
(`retrieval/lexical.py`, BM25 built lazily / rebuilt when dirty) independently, fuses
with **RRF** (`rrf_fuse()`, k=60), applies filters, and optionally re-ranks
(`retrieval/rerank.py` cross-encoder — off by default, downloads a model on first
use). `mode="vector"` skips the lexical branch. Filters `kind`/`language`/`symbol`/
`knowledge_type` become a Chroma `where` via `store.build_where()`; `source` is a
substring post-filter.

### RAG / ask (`query/rag.py`)

`answer_question()` (sync) and `answer_question_stream()` (SSE) synthesize an answer
**strictly from numbered sources**, citing inline as `[n]`. No API key → fails loud
rather than fabricating; no matching chunk → explicit "no content matched" message.

### Knowledge graph (`graph/`)

Typed entities + relations persisted in **MariaDB** (`MariaGraphStore` via PyMySQL;
`NullGraphStore` no-op when unwired). Built from extracted relations (`builder.py`,
`normalize.py`, `workflow.py`). Exposed as pure-read MCP tools (`get_entity`,
`list_related_entities`, `get_workflow_steps`, `list_workflows`) on the Developer/
Architecture views.

### MCP views + advisor (`views/`, `advisor/`, `server.py`)

Role-specific MCP servers (Product / Operations / Developer / Support / Architecture)
are **described as data** in `views.VIEWS` (tool name + filters) and turned into real
tools by `server.build_view_server()`; each tool is just a filtered search over the
shared store via `views.run_view_tool()`. Select with `--view NAME` / `ODM_MCP_VIEW`.
The default server also exposes `what_should_i_know_before` → the **advisor**, a pure
(no-LLM) aggregation of retrieval + graph into facets (Workflow / Risks / Permissions
/ Dependencies / Constraints).

### Other

- `evals/` — offline grounding/hallucination eval harness; injected callables, no network.
- `api/` beyond `app.py`: `auth.py` (optional API-key auth with per-key view scoping), `source_routes.py`, `insight_routes.py`, `mcp_endpoints.py`, `observability.py`.
- `models.py` — plain dataclasses (`Chunk`, `KnowledgeUnit`, `SearchResult`) carry no logic, keeping stages decoupled.

## Part 5: Configuration

Settings use the `ODM_` prefix, read from env / `.env` (`config.py`). A subset is
**runtime-editable** from the web UI and persisted to `<data_dir>/settings.json`
(layered over env at load). Credentials and `data_dir` are deliberately **not**
runtime-editable. Credentials come from standard provider vars: `ANTHROPIC_API_KEY` /
`ANTHROPIC_BASE_URL` (any Anthropic-compatible endpoint works, e.g. OpenRouter),
`OPENAI_API_KEY`, `VOYAGE_API_KEY`. Flags beyond the README defaults:
`ODM_REVIEW_MODE` / `ODM_RETRIEVE_APPROVED_ONLY` (approval gating of retrieved
chunks), `ODM_MULTI_TENANT`, `ODM_AUTH_ENABLED` + `ODM_API_KEYS`, and
`ODM_GRAPH_DB_*` (MariaDB). See `.env.example` for the full documented list.

## Part 6: Extension seams

- **New embedder** → implement `Embedder` (`embed` + `dim`), register in `embedding/__init__.py:get_embedder()`.
- **New language** → add extension→language in `loader.py` and the grammar in `code_splitter.py:_GRAMMARS` (plus the tree-sitter wheel in `pyproject.toml`).
- **New extractor** → implement `extract(text, kind, language) -> KnowledgeUnit`, return from `get_extractor()`.
- **New MCP view/tool** → add a `ViewTool` entry to `views.VIEWS` (data, not a function).
- **New surface** → call `build_context()` and drive `ctx.pipeline` / `ctx.store`; you inherit ingestion, hybrid search, and RAG for free.
