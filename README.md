# openDomainMcp

A general-purpose **domain knowledge workflow platform**. Drop in documents *or*
source code of almost any type; the system extracts domain knowledge with Claude,
embeds it, and stores it in a vector database you can query from a CLI, an MCP
server, or a web dashboard.

Code is chunked with **AST analysis** (tree-sitter) at function/class/method
boundaries — borrowing the core idea from the open-source `claude-context`
project — while documents are split with a recursive text splitter.

## Pipeline

```
load → split (AST for code / recursive for text) → extract domain knowledge (Claude)
     → embed → store (Chroma) → search
```

All four surfaces (CLI, MCP, web API) run on the **same** pipeline and store
(`opendomainmcp/context.py`).

## Components

| Area | Module |
| --- | --- |
| File loading & type detection | `ingest/loader.py` |
| AST code chunking | `ingest/code_splitter.py` |
| Recursive text chunking | `ingest/text_splitter.py` |
| Domain-knowledge extraction | `extract/knowledge.py` |
| Embeddings (pluggable) | `embedding/` (local fastembed by default) |
| Vector store | `store/chroma_store.py` |
| Orchestration | `ingest/pipeline.py` |
| CLI / MCP / Web | `cli.py` / `server.py` / `api/app.py` |

Supported code languages (AST): Python, JavaScript, TypeScript/TSX, Java, Go,
Rust, C, C++, C#, Ruby, Bash. Unsupported languages fall back to line-window
chunking. Documents: txt, md, pdf, docx, html, json, csv, and any UTF-8 text.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # adjust as needed
```

The local embedder downloads a small model from HuggingFace on first use.
Knowledge extraction uses the Anthropic API (`ANTHROPIC_API_KEY` /
`ANTHROPIC_BASE_URL`); set `ODM_EXTRACT_KNOWLEDGE=false` to disable it.

## Usage

### CLI

```bash
opendomainmcp ingest ./path/to/code-or-docs        # add --sync to prune deleted files
opendomainmcp search "how is retrieval implemented" --top-k 5 --language python
opendomainmcp ask "how does hybrid search work"     # cited answer (needs API key)
opendomainmcp collections                           # list knowledge bases
opendomainmcp --collection my_project ingest ./src  # use a specific knowledge base
opendomainmcp stats
opendomainmcp clear
```

Search is **hybrid** (dense + BM25 with Reciprocal Rank Fusion) by default and supports
`--kind`, `--language`, `--symbol`, and `--source` filters. Set `ODM_SEARCH_MODE=vector`
for dense-only.

### MCP server

```bash
opendomainmcp-server          # stdio; tools: ingest_path, search_knowledge, ask,
                              #               get_stats, list_collections
```

### Web dashboard

```bash
opendomainmcp-web             # http://127.0.0.1:8000
```

A console to view database status, ingest with live progress (and optional sync),
hybrid search with filters, ask cited questions, browse & edit stored items,
switch between knowledge bases, and change settings.

## Configuration

All settings use the `ODM_` prefix (see `.env.example`). Editable-at-runtime
settings (embedder, extraction model, chunk sizes) can also be changed from the
web UI; they persist to `<data_dir>/settings.json`.

## Tests

```bash
pytest
```

Tests run fully offline using a deterministic fake embedder and a mocked
extractor (no network, no model download).
