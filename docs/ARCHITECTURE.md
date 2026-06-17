# OpenDomainMCP — 技術架構文件

> 本文件描述系統的實際實作架構（截至 Phase 2 合併後）。所有路徑相對於 repo 根目錄。產品需求見 [PRD.md](./PRD.md)，任務進度見 [TASKS.md](./TASKS.md)。

---

## 1. 系統總覽

OpenDomainMCP 採「單一真實來源（single wiring point）+ 三個可互換入口」設計：

```
   CLI            MCP Server         Web Dashboard (FastAPI)
opendomainmcp   opendomainmcp-      opendomainmcp-web
 (cli.py)        server/-view        (api/app.py)
                 (server.py)
        \             |             /
         \            |            /
            build_context()   ← 唯一接線點 (context.py)
                   |
        Context { settings, store, pipeline }
                   |
   ┌───────────────┼────────────────┐
 Pipeline       ChromaStore        Settings
(ingest/)     (store/)            (config.py)
```

- **三個入口都透過 `build_context()`** 取得相同的 `settings / store / pipeline`，確保行為一致。
- 相依元件（store、extractor、embedder、reranker）皆以工廠函式注入，測試可用 fake 全離線執行。

---

## 2. 三層架構（對應 PRD）

| Layer | 內容 | 實作 |
|-------|------|------|
| **Layer 1 — Ingestion** | Sources → Parsing → Chunking → Extraction → Embedding | `ingest/`、`extract/`、`embedding/` |
| **Layer 2 — Knowledge Store** | Vector + Structured Domain + Metadata | `store/chroma_store.py`（Chroma：向量 + 扁平 metadata；BM25 lexical index） |
| **Layer 3 — MCP View Layer** | 同一知識庫產生多個 MCP | `views/__init__.py`、`server.py` |

---

## 3. 擷取流程（Ingestion Pipeline）

5 個主要階段 + 2 個維護階段，由 `ingest/pipeline.py` 的 `Pipeline.ingest_path()` 編排：

```
LOAD → SPLIT → EXTRACT → EMBED → STORE   (+ PRUNE, SYNC)
```

| 階段 | 說明 | 模組 |
|------|------|------|
| **LOAD** | 型別偵測與文字擷取（code/text/api） | `ingest/loader.py` → `load_file()` → `LoadedDoc` |
| **SPLIT** | code 走 AST、api 走 OpenAPI、text 走遞迴切分 | `code_splitter.py` / `openapi.py` / `text_splitter.py` |
| **EXTRACT** | LLM 萃取領域知識（已預分類者跳過） | `extract/knowledge.py` |
| **EMBED** | 以 enriched text（chunk + summary + concepts）產生向量 | `embedding/` |
| **STORE** | Chroma upsert（依 content hash 冪等） | `store/chroma_store.py` |
| **PRUNE** | 檔案編輯後移除過期 chunk | `pipeline._ingest_file()` |
| **SYNC** | `sync=True` 時移除已刪除檔案的 chunk | `pipeline._sync_deletions()` |

### 來源解析（Phase 2 M4）

`Pipeline.ingest_path()` 先呼叫 `ingest/sources.py` 的 `prepared_source(spec, data_dir)`（context manager）：

- **Git**（`is_git_spec`：`git@` / `git+` / `ssh://` / `.git` / github|gitlab|bitbucket）→ `git clone --depth 1` 到 `data_dir/.sources/<uuid>/`，結束後清理。
- **Zip**（`is_zip_spec`：本機 `.zip` 檔）→ 安全解壓（zip-slip 防護：解析每個成員目標，拒絕 `..` 與絕對路徑逃逸）到暫存目錄。
- **一般路徑** → 直接沿用，`allowed_root` 機制限制擷取範圍。

### LOAD 型別路由（`load_file`）

| 副檔名 | kind | 處理 |
|--------|------|------|
| `LANGUAGE_BY_EXT`（.py/.js/.ts/.go/.rs/.java/.c/.cpp/.cs/.rb/.php/.swift/.kt/.scala/.sh/.lua…） | `code` | tree-sitter（11 種具 AST wheel；無 wheel 者如 php/swift/kt/scala/lua 退回 line 切分） |
| `.pdf` / `.docx` | `text` | pypdf / python-docx |
| `.html` / `.htm` | `text` | HTMLParser（去除 script/style） |
| `.json` / `.yaml` / `.yml` 且 `looks_like_openapi()` | `api` | OpenAPI 解析，language="openapi" |
| 其他 TEXT_EXTENSIONS（.md/.txt/.rst/.csv/.log/.ini/.toml/.cfg/.xml/.css…） | `text` | 原樣讀取 |
| 未知副檔名 | `text`（若可 UTF-8 解碼） | 否則 `UnsupportedFileError`（fail loud） |

### OpenAPI/Swagger 解析（`ingest/openapi.py`）

- `parse_spec(text)`：先試 JSON 再試 YAML（`yaml.safe_load`）。
- `looks_like_openapi(data)`：含 `openapi`/`swagger` 鍵且有 `paths` dict。
- `split_openapi(text, source)`：**每個 HTTP operation 一個 Chunk**：
  - `text` = method + path + summary + description + 參數 + 回應碼（`_operation_text()`）
  - `symbol` = `operationId` 或 `"METHOD path"`
  - 預先分類 `KnowledgeUnit`：`knowledge_type="API"`、`audience=["engineering"]`、`confidence=1.0`
- 預分類 chunk 在 `pipeline._extract_one()` 會**跳過 LLM 擷取**（節省成本）。

---

## 4. 知識萃取（Knowledge Extraction）

`extract/knowledge.py`：

- **`_SYSTEM` prompt**：要求模型只回傳一個 JSON 物件，鍵包含
  `summary`、`concepts`、`relations`、`knowledge_type`（限定 `KNOWLEDGE_TYPES`）、
  `audience`（限定 `AUDIENCES`）、`confidence`(0–1)、`tags`、`permissions`、`references`。
  （`version`、`review_status` **不**由 LLM 萃取。）
- **`_parse(raw)`** 防禦性正規化：
  - `confidence` clamp 到 [0,1]
  - `knowledge_type` 以 `_norm_choice` 對 `KNOWLEDGE_TYPES` 做大小寫不敏感比對，不符回 `""`
  - `audience` 逐項白名單過濾
  - list 欄位以 `_str_list` 去空白
- **`ClaudeExtractor`**：呼叫 Anthropic（`extraction_model`，預設 `claude-sonnet-4-6`），帶 timeout/retries。
- **`NullExtractor`**：`extract_knowledge=False` 時回傳空 `KnowledgeUnit`。
- **`get_extractor(settings)`** 工廠選擇上述兩者。

### Review 狀態（Phase 2 M3）

`pipeline._extract_one()`：

- 若 `settings.review_mode == True`：新擷取 chunk 設 `review_status="pending"`。
- 否則沿用 `KnowledgeUnit` 預設 `review_status="approved"`（向後相容，舊資料無此欄位視為可見）。

---

## 5. 資料模型（`models.py`）

```python
KNOWLEDGE_TYPES = ("Feature","Workflow","API","Permission","Constraint","Error",
                   "Troubleshooting","Architecture","Code","Glossary","Runbook","FAQ")
AUDIENCES = ("product_manager","solutions_architect","operations","engineering","support")
```

### `KnowledgeUnit`（領域知識）

| 欄位 | 型別 | 預設 |
|------|------|------|
| `summary` | str | `""` |
| `concepts` | list[str] | `[]` |
| `relations` | list[str] | `[]` |
| `knowledge_type` | str | `""` |
| `audience` | list[str] | `[]` |
| `confidence` | float | `0.0` |
| `version` | str | `""` |
| `permissions` | list[str] | `[]` |
| `tags` | list[str] | `[]` |
| `references` | list[str] | `[]` |
| `review_status` | str | `"approved"` |

### `Chunk`（待嵌入儲存單位）

`text, source, kind("text"/"code"/"api"), language, node_type, symbol, start_line, end_line, knowledge`

- `content_hash` / `id`：`sha256(source:start-end + text)`，冪等 upsert 用。
- `embedding_text()`：text + `Summary:` + `Concepts:`，讓檢索貼近語意。
- `metadata()`：扁平化成 Chroma 友善 dict（list → `", "` 或 `" | "` join；丟棄 None/空值）。

### `SearchResult`

`id, text, score, metadata`

---

## 6. 知識儲存（`store/chroma_store.py`）

- **向量 + 結構化 + metadata**：皆存於 Chroma（PersistentClient，cosine）。metadata 為扁平 scalar。
- **Lexical index**：記憶體內 BM25（`retrieval/lexical.py`），lazy-build、upsert/delete 後標記 dirty。
- **過濾**：`build_where(filters)` 支援 `_FILTER_FIELDS = ("kind","language","symbol","knowledge_type","review_status")`；單條件回 `{k:v}`，多條件回 `{"$and":[...]}`。
  - `audience` **不在** Chroma 過濾欄位（存成 join 字串），改在 view 層 client-side 後過濾。
- **CRUD / 管理**：`upsert / search / get_items / get_item / update_metadata / delete_item / get_ids_for_source / delete_ids / get_all_sources / list_collections / create_collection / drop_collection / stats / clear`。
- **Resilience**：`_retry()` 對 transient 失敗指數退避重試（`max_retries`）。

---

## 7. 檢索引擎（Retrieval）

`store.search(query, top_k, where, mode, source_contains)`：

1. **Vector**：dense embedding cosine（`mode="vector"`，預設）。
2. **Hybrid**：dense + BM25，以 **RRF** 融合（k=60，over-fetch ×5 再裁切）。
3. **Filters**：`where`（Chroma）+ `source_contains`（後過濾）。
4. **Re-rank**（選用）：`retrieval/rerank.py` cross-encoder（`Xenova/ms-marco-MiniLM-L-6-v2`），給所有候選統一分數。

---

## 8. RAG 問答（`query/rag.py`）

- `answer_question(query, store, settings, top_k)`：檢索 top-k → 組成編號 sources → Claude 合成帶 `[n]` 引用的答案 → 回 `{answer, citations}`。
- `answer_question_stream(...)`：先 yield `delta` token 事件，最後 yield `citations` 事件（SSE）。
- 無檢索結果則短路（不捏造）；缺 `ANTHROPIC_API_KEY` 拋 `AnswerError`（fail loud）。

---

## 9. MCP 層（`server.py` + `views/__init__.py`）

### 通用 server（預設）

`ingest_path`、`search_knowledge`、`ask`、`get_stats`、`list_collections`。

### 角色視圖（Phase 2 M2）

- `ViewTool(name, description, filters, default_top_k=5)`、`ViewSpec(name, title, purpose, tools)`，宣告於 `VIEWS` dict。
- `build_view_server(view_name)`：依 `VIEWS` 動態註冊每個工具，工具實作呼叫 `run_view_tool()`。
- `get_server(view)`：`"generic"/""` 回通用 server，否則回對應視圖。
- `main()`：解析 `--view`（或 `ODM_MCP_VIEW` 環境變數，預設 `generic`）。

### `run_view_tool()` 行為

- 從 filters 取出 `audience` 做 client-side 後過濾（over-fetch ×3）。
- 若 `settings.retrieve_approved_only`：注入 `review_status="approved"`。
- 其餘 filters 經 `build_where()` 套用。

### 工具 → 過濾條件對照

| View | Tool | Filter |
|------|------|--------|
| product | get_feature | knowledge_type=Feature |
| product | get_workflow | knowledge_type=Workflow |
| product | get_constraint | knowledge_type=Constraint |
| product | search_product_knowledge | audience=product_manager |
| operations | get_runbook | knowledge_type=Runbook |
| operations | get_troubleshooting | knowledge_type=Troubleshooting |
| operations | get_incident_response | knowledge_type=Runbook, audience=operations |
| operations | get_rollback_procedure | knowledge_type=Runbook |
| developer | search_code | kind=code |
| developer | get_class | kind=code |
| developer | get_function | kind=code |
| developer | trace_dependency | kind=code |
| developer | get_api_implementation | knowledge_type=API |
| support | get_known_issue | knowledge_type=Error |
| support | get_error_explanation | knowledge_type=Error |
| support | get_resolution_steps | knowledge_type=Troubleshooting |
| support | search_faq | knowledge_type=FAQ |
| architecture | get_component | knowledge_type=Architecture |
| architecture | get_dependency | knowledge_type=Architecture |
| architecture | get_dataflow | knowledge_type=Architecture |
| architecture | search_architecture | audience=solutions_architect |

> 註：`get_class/get_function/trace_dependency` 目前皆僅以 `kind=code` 過濾，尚未做 symbol 精準對應（列為改進待辦）。

---

## 10. Web API（`api/app.py`，FastAPI）

| Method | Path | 說明 |
|--------|------|------|
| GET | `/api/health` | 健康檢查 |
| GET | `/api/stats` | 統計 |
| POST | `/api/search` | 混合搜尋（套用 approved-only 政策） |
| POST | `/api/ask` | RAG 問答 |
| GET | `/api/ask/stream` | RAG SSE 串流 |
| POST | `/api/upload` | 串流上傳（大小上限） |
| GET | `/api/ingest/stream` | 擷取 SSE 串流 |
| GET | `/api/items` | 列出 chunk（`?limit&offset&kind&review_status&knowledge_type`） |
| POST | `/api/items` | **手動新增知識**（`ItemCreate`，自動 approved） |
| GET | `/api/items/{id}` | 取單筆 |
| PATCH | `/api/items/{id}` | 編輯 metadata |
| DELETE | `/api/items/{id}` | 刪除 |
| POST | `/api/items/{id}/approve` | **核准** → review_status=approved |
| POST | `/api/items/{id}/reject` | **拒絕** → review_status=rejected |
| GET | `/api/settings` | 讀設定 |
| PATCH | `/api/settings` | 改設定（`{values}`，限 editable） |
| GET | `/api/views` | **列出 5 個 MCP 視圖與工具** |
| POST | `/api/simulate` | **Agent Simulator**：跑某視圖工具，回 grounding |
| GET/POST/DELETE | `/api/collections[...]` | 知識庫 CRUD |

- Collection 經 `?collection=` 或 `x-collection` header 選擇；SPA 由 `api/static/` 提供。
- `/api/simulate` 回傳 `{view, tools:[{tool, results}], grounding:{hits, avg_score, knowledge_types}}`。

---

## 11. Web 前端（`web/`，React 18 + Vite + Tailwind）

- **路由**（`main.tsx`，hash router）：`/`(Dashboard)、`ingest`、`explore`、`ask`、`browse`、`review`、`mcp`、`simulator`、`settings`。
- **新頁面**（Phase 2 M5）：`Review.tsx`（審核佇列：tabs + 核准/拒絕/編輯 + 手動新增 Modal）、`McpBuilder.tsx`（視圖/政策設定 + 發布指令片段）、`Simulator.tsx`（任務輸入 + grounding 統計）。
- **共用**：`components/ui.tsx`（Button/Card/Modal/Badge/Input/Select/Toast…）、`components/icons.tsx`（新增 IconReview/IconBuilder/IconSimulator）、`api.ts`（新增 `approveItem/rejectItem/addItem/views/simulate` 與型別、`KNOWLEDGE_TYPES`/`AUDIENCES` 常數）。

---

## 12. CLI（`cli.py`）

```
opendomainmcp [--collection NAME] <command>
  ingest PATH [--sync]      # 擷取檔案/目錄/Git/Zip/OpenAPI
  search QUERY [--top-k --kind --language --symbol --source]
  ask QUERY [--top-k]
  stats
  clear
  collections
```

---

## 13. 設定（`config.py`，env 前綴 `ODM_`）

可在 web UI 編輯的欄位 `EDITABLE_FIELDS`：
`embedder_backend, embedder_model, extract_knowledge, extraction_model, chunk_size, chunk_overlap, code_max_chunk_chars, extract_concurrency, search_mode, rerank_enabled, answer_model, review_mode, retrieve_approved_only`

Phase 2 新增設定：
- `review_mode: bool = False` — 新擷取標為 `pending`，需審核。
- `retrieve_approved_only: bool = False` — 檢索僅回 `approved`。

其餘：storage、security（`ingest_root`、`max_upload_mb`）、embedding、chunking、retrieval、RAG、resilience（`request_timeout`、`max_retries`）。憑證（`ANTHROPIC_API_KEY` 等）僅由 env 讀取，不可由 UI 編輯。

---

## 14. 入口點與相依（`pyproject.toml`）

**Console scripts**：
```
opendomainmcp        → cli:main
opendomainmcp-server → server:main        # 通用 MCP（或 --view）
opendomainmcp-view   → server:main        # 角色視圖 MCP
opendomainmcp-web    → api.app:main       # Web Dashboard
```

**主要相依**：`chromadb`、`fastembed`、`rank-bm25`、`tree-sitter`(+11 語言 wheel)、`pypdf`、`python-docx`、`pyyaml`、`anthropic`、`mcp`、`fastapi`、`uvicorn`、`sse-starlette`、`pydantic-settings`、`python-multipart`。Python `>=3.11`。

---

## 15. 測試策略（`tests/`）

- **106 個測試 / 21 檔**，全離線：`FakeEmbedder`（64 維 bag-of-words）、`FakeExtractor`（已延伸回傳 `knowledge_type/audience/confidence`）、Chroma `EphemeralClient`。
- 涵蓋：loader、code/text splitter、pipeline（含 review_mode/sync/security/concurrency）、extract（含分類正規化）、models（含分類欄位扁平化與向後相容）、store/hybrid（含 knowledge_type/review_status 過濾）、retrieval/RRF、rerank、rag/streaming、collections、resilience、api（含 review/simulate/views 端點）、views、openapi、sources（含 zip-slip）、config、cli。
- 原則：offline-first、deterministic、fail-loud、idempotent upsert、security-first、backward-compatible。

---

_最後更新：2026-06-17_
