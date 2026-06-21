# OpenDomainMCP — 系統功能詳細說明（System Functions）

本文件是 OpenDomainMCP 的**功能參考手冊**，逐一說明每項主要能力：它做什麼、如何使用（CLI 指令 / HTTP 端點 / MCP 工具 / Web 頁面）、輸入輸出，以及相關設定旗標。內容皆對照實際原始碼（`src/opendomainmcp/`、`web/src/`）整理。

> 產品需求脈絡見 [PRD.md](./PRD.md)；技術實作與資料流見 [ARCHITECTURE.md](./ARCHITECTURE.md)。本文件偏重「怎麼用、有哪些行為」，架構細節請交叉參照 ARCHITECTURE.md 的對應章節。

四個使用入口共享同一個 `build_context()`（`settings / store / pipeline / graph`），所以同一份知識在 CLI、MCP server、Web API 與 Web Dashboard 上行為一致：

| 入口 | 啟動方式 | 用途 |
|------|----------|------|
| **CLI** | `opendomainmcp <command>` | 擷取、搜尋、問答、管理 |
| **MCP Server（通用）** | `opendomainmcp-server`（或 `--view generic`） | 給 agent 的通用工具 |
| **MCP View（角色視圖）** | `opendomainmcp-view --view <name>`（或 `ODM_MCP_VIEW`） | 5 個角色專屬 MCP |
| **Web Dashboard** | `opendomainmcp-web`（FastAPI + 內建 SPA） | 視覺化操作（12 個頁面） |

---

## 1. 知識擷取（Ingestion）

把文件、原始碼、規格、Wiki 轉成可檢索的知識 chunk。流程：`load → split → extract → embed → store`（+ 維護階段 prune/sync），細節見 [ARCHITECTURE.md §3](./ARCHITECTURE.md)。

### 1.1 支援的來源與格式

| 來源／格式 | 偵測方式 | 切分方式 | 備註 |
|------------|----------|----------|------|
| **程式碼** | 副檔名（`LANGUAGE_BY_EXT`：.py/.js/.ts/.go/.rs/.java/.c/.cpp/.cs/.rb/.php/.swift/.kt/.scala/.sh/.lua…） | tree-sitter AST（11 種有 wheel；其餘退回 line 切分） | `kind="code"`，帶 `node_type`/`symbol` |
| **文件** | .pdf / .docx / .html(.htm) / .md / .txt / .rst / .csv / .tsv / .log / .ini / .toml / .cfg / .css … | 遞迴文字切分 | pypdf / python-docx / HTMLParser |
| **Git repo** | URL scheme（`git@`/`git+`/`ssh://`/`git://`）、結尾 `.git`、或已知主機（github/gitlab/bitbucket） | `git clone --depth 1` 後遞迴擷取 | 結束後清理暫存 |
| **Zip 套件** | 本機 `.zip` 檔 | 安全解壓（zip-slip 防護）後擷取 | 拒絕 `..` 與絕對路徑逃逸 |
| **OpenAPI/Swagger** | .json/.yaml/.yml 且 `looks_like_openapi()` | **每個 HTTP operation 一個 chunk** | 預分類 `knowledge_type="API"`，跳過 LLM |
| **GraphQL SDL** | .graphql / .graphqls / .gql | **每個頂層定義一個 chunk**（root 型別逐 field） | 預分類 `knowledge_type="API"`，跳過 LLM |
| **Wiki（MediaWiki XML）** | .xml 且 `looks_like_mediawiki()` | 展平成 `= Title =` per-page 區段後當文件切分 | 跳過 redirect / 空頁；XML 損毀 fail loud |

> 說明：Wiki 的 Confluence HTML heuristic（`looks_like_confluence_html`）已實作，但 loader 目前**只接線 MediaWiki XML** 分支。GraphQL chunk 的 `kind` 實際為 `"text"`、`language="graphql"`、`knowledge_type="API"`（見文末「程式碼與設計差異」）。

### 1.2 如何使用

**CLI**
```
opendomainmcp [--collection NAME] ingest PATH [--sync]
```
- `PATH` 可為單檔、目錄、Git URL、.zip、OpenAPI 或 GraphQL spec。
- `--sync`：對目錄擷取時，移除已被刪除檔案對應的 chunk。

**Web — Ingest 頁（`/ingest`）**
- 從伺服器路徑擷取，或上傳檔案。
- 即時串流各階段日誌（load/split/extract/embed/store/prune/skip）並顯示最終 report。
- 對應 API：`POST /api/upload`（FormData，受 `max_upload_mb` 限制）、`GET /api/ingest/stream?path=&sync=`（SSE 事件 `load/split/extract/embed/store/prune/skip/error/done/report`，`api.ts: ingestStream`）。

**輸入／輸出**
- 輸入：路徑或檔案。
- 輸出：依 content hash 冪等 upsert 的 chunk（重複擷取不重複寫入）；report 含各階段計數。

### 1.3 相關設定
`chunk_size`、`chunk_overlap`、`code_max_chunk_chars`、`extract_knowledge`、`extraction_model`、`extract_concurrency`、`review_mode`（新擷取標 `pending`）、`ingest_root`（限制擷取範圍）、`max_upload_mb`。

---

## 2. 知識萃取與分類（Extraction & Classification）

擷取時由 LLM（Anthropic，`extraction_model`，預設 `claude-sonnet-4-6`）萃取結構化領域知識；已預分類者（OpenAPI/GraphQL）跳過以節省成本。`extract_knowledge=False` 時用 `NullExtractor` 回空知識。

每個 `KnowledgeUnit` 欄位：`summary`、`concepts`、`relations`、`knowledge_type`、`audience`、`confidence`、`version`、`permissions`、`tags`、`references`、`review_status`。

### 2.1 知識類型（12 種）
`Feature`、`Workflow`、`API`、`Permission`、`Constraint`、`Error`、`Troubleshooting`、`Architecture`、`Code`、`Glossary`、`Runbook`、`FAQ`

### 2.2 對象（Audience，5 種）
`product_manager`、`solutions_architect`、`operations`、`engineering`、`support`

- LLM 回傳值經防禦性正規化：`confidence` clamp 到 [0,1]、`knowledge_type` 大小寫不敏感白名單比對、`audience` 逐項過濾。
- `version`、`review_status` **不**由 LLM 萃取。

---

## 3. 混合檢索與重排（Hybrid Retrieval + Rerank）

`store.search(query, top_k, where, mode, source_contains)`。

| 模式 | 行為 |
|------|------|
| **vector** | dense embedding cosine |
| **hybrid**（預設） | dense + BM25，以 RRF 融合（k=60，over-fetch ×5 再裁切） |

- **Metadata 過濾**（`build_where`）：`kind / language / symbol / knowledge_type / review_status`；`audience` 不在 Chroma 過濾欄，改在 view 層 client-side 後過濾。
- **Re-rank**（選用，`rerank_enabled`）：cross-encoder（`Xenova/ms-marco-MiniLM-L-6-v2`）對所有候選給統一分數。

**如何使用**
- CLI：`opendomainmcp search QUERY [--top-k --kind --language --symbol --source]`
- HTTP：`POST /api/search`（body `{query, top_k, ...filters}`）
- MCP（通用）：`search_knowledge(query, top_k, kind, language, symbol)`
- Web：**Explore 頁（`/explore`）** — 混合搜尋 + kind/language/source 過濾，顯示排名、metadata、概念。

**設定**：`search_mode`、`rerank_enabled`、`rerank_model`、`embedder_backend`、`embedder_model`、`retrieve_approved_only`。

---

## 4. 引用式問答（Cited Ask / RAG）

`query/rag.py::answer_question`：檢索 top-k → 組編號 sources → Claude 合成帶 `[n]` 引用的答案 → 回 `{answer, citations}`。無檢索結果則短路（不捏造）；缺 `ANTHROPIC_API_KEY` 拋 `AnswerError`（fail loud）。

**如何使用**
- CLI：`opendomainmcp ask QUERY [--top-k]`
- HTTP：`POST /api/ask`（`{query, top_k}`）；串流 `GET /api/ask/stream?query=&top_k=`（SSE：`delta` token → 最後 `citations`，`api.ts: askStream`）
- MCP（通用）：`ask(query, top_k=6, collection=None)`
- Web：**Ask 頁（`/ask`）** — 串流答案 + 編號引用，含複製鈕。

**設定**：`answer_model`、`search_mode`、`retrieve_approved_only`、`request_timeout`、`max_retries`。

---

## 5. MCP 視圖與工具（5 Views）

同一知識庫產生 5 個角色專屬 MCP，每個工具皆是「帶過濾的檢索」。啟動：`opendomainmcp-view --view <name>`（或 `ODM_MCP_VIEW`），通用 server 為 `opendomainmcp-server`。

### 5.1 通用 server 工具
`ingest_path`、`search_knowledge`、`ask`、`what_should_i_know_before`（見 §9）、`get_stats`、`list_collections`。

### 5.2 角色視圖工具 → 過濾條件

| View | Tool | 過濾條件 |
|------|------|----------|
| **product**（Product MCP） | get_feature | knowledge_type=Feature |
| | get_workflow | knowledge_type=Workflow |
| | get_constraint | knowledge_type=Constraint |
| | search_product_knowledge | audience=product_manager |
| **operations**（Operations MCP） | get_runbook | knowledge_type=Runbook |
| | get_troubleshooting | knowledge_type=Troubleshooting |
| | get_incident_response | knowledge_type=Runbook + audience=operations |
| | get_rollback_procedure | knowledge_type=Runbook |
| **developer**（Developer MCP） | search_code | kind=code |
| | get_class | kind=code + node_type ∈ class/struct/enum/interface/trait/type_alias |
| | get_function | kind=code + node_type ∈ function/method/constructor |
| | trace_dependency | 先查相依圖 `imports` 邊，無結果回退 kind=code |
| | get_api_implementation | knowledge_type=API |
| **support**（Support MCP） | get_known_issue / get_error_explanation | knowledge_type=Error |
| | get_resolution_steps | knowledge_type=Troubleshooting |
| | search_faq | knowledge_type=FAQ |
| **architecture**（Architecture MCP） | get_component / get_dependency / get_dataflow | knowledge_type=Architecture |
| | search_architecture | audience=solutions_architect |

- `run_view_tool()` 行為：`audience` 做 client-side 後過濾（over-fetch ×3）；`node_types` 後過濾；`retrieve_approved_only` 開啟時注入 `review_status="approved"`。
- 視圖可透過 Web 發布成 HTTP/SSE 端點（見 §13）。

---

## 6. 知識審核工作流（Knowledge Review）

當 `review_mode=True`，新擷取知識標為 `pending`，須核准後才視為已審；`retrieve_approved_only=True` 時檢索/視圖僅回 `approved`。

**Web — Review 頁（`/review`）**
- 三個分頁：pending / approved / rejected。
- 對每筆知識可**核准 / 拒絕 / 編輯**，亦可**手動新增**知識（hand-authored，自動 approved）。

**對應 API**
| 動作 | 端點 |
|------|------|
| 列出（可過濾 review_status/knowledge_type/kind） | `GET /api/items?limit&offset&kind&review_status&knowledge_type` |
| 手動新增 | `POST /api/items`（`ItemCreate`，自動 approved） |
| 取單筆 / 編輯 metadata / 刪除 | `GET|PATCH|DELETE /api/items/{id}` |
| 核准 | `POST /api/items/{id}/approve` → review_status=approved |
| 拒絕 | `POST /api/items/{id}/reject` → review_status=rejected |

> **Browse 頁（`/browse`）** 也用 `/api/items`：分頁瀏覽 + 內嵌編輯 metadata + 刪除（可依 kind 過濾），偏向一般瀏覽而非審核佇列。

**設定**：`review_mode`、`retrieve_approved_only`。

---

## 7. 知識圖：實體 / 工作流 / 相依（Knowledge Graphs）

圖資料存於 MariaDB（`MariaGraphStore`）；未配置時 pipeline 以 `NullGraphStore` 退化，向量流程不受影響。擷取時 `_write_graph`（實體/邊）、`_write_workflow`（工作流步驟）、`_write_deps`（程式碼 `imports` 邊）同步寫入。

### 7.1 實體圖（Entity Graph）
從知識的 `concepts/relations` 建實體與關係邊。
- HTTP：`GET /api/graph/entity/{name}`（鄰居，404 if 無）、`GET /api/graph/entities?type=&q=&limit=`。

### 7.2 工作流圖（Workflow Graph）
從 Workflow/Runbook 知識建「前置條件 + 排序步驟」。
- HTTP：`GET /api/graph/workflow/{name}`、`GET /api/graph/workflows?q=&limit=`。

### 7.3 程式碼相依圖（Dependency Graph）
`graph/deps.py::extract_dependencies` 從程式碼 import 建 `module` 節點與 `imports` 邊（Python / JS/TS；其他語言退化為空）。與實體/工作流圖**共用 entities/edges 表**。詳見 [ARCHITECTURE.md §16](./ARCHITECTURE.md)。
- 查詢介面：Developer MCP 的 `trace_dependency`（先查圖、無則回退檢索）。

**Web — Graph 頁（`/graph`）**
- Entities 模式：依名稱/類型搜尋實體，顯示進/出鄰居關係。
- Workflows 模式：搜尋工作流，顯示前置條件與排序步驟。

---

## 8. 來源登錄（Source Registry）

掌握「哪些來源已被擷取」並可整批刪除某來源。

| 動作 | 介面 |
|------|------|
| 列出來源（每個含 chunks 數、kinds、review 狀態分佈） | `GET /api/sources` |
| 刪除某來源（連同其圖切片） | `DELETE /api/sources`（body `{source}`；未知來源 404、空 source 400） |

- 底層：`chroma_store.list_sources()` / `delete_by_source()`；刪除時先 `get_ids_for_source` → `graph.delete_for_chunks` → `store.delete_ids`。
- **Web — Dashboard 頁（`/`）** 顯示來源登錄與 review 狀態桶（`api.ts: sources()`）。
- 路由掛載時帶 `auth_dependency`（見 §11）。

---

## 9. Pre-Execution Advisor（執行前顧問）

回答「執行動作 X 前我該知道什麼」。**純聚合、無 LLM**。聚合五個 facet：

| Facet | 來源 knowledge_type |
|-------|---------------------|
| workflow | Workflow、Runbook |
| risks | Error、Troubleshooting、Constraint |
| permissions | Permission |
| dependencies | 圖 `imports`/`depends_on` 鄰居 + Architecture 知識 |
| constraints | Constraint |

另回 `graph_workflow`（best-effort 前置條件 + 步驟）與 `summary`（每 facet 計數 + 觀察到的 knowledge_type）。實作細節見 [ARCHITECTURE.md §17](./ARCHITECTURE.md)。

**如何使用**
- MCP（通用）：`what_should_i_know_before(action, top_k=5, collection=None)`
- HTTP：`POST /api/advise`（`{action, top_k}`；空 action → 422）
- Web：**Advisor 頁（`/advisor`）** — 輸入動作描述，分五個 facet 區塊 + summary strip 顯示。

**設定**：`retrieve_approved_only`（套用同檢索政策）、`search_mode`。

---

## 10. 指標（Metrics）

`metrics/__init__.py`：將 search/ask 事件記成 append-only JSONL（`metrics.jsonl`），並聚合產品/agent 指標。

| 類別 | 指標 |
|------|------|
| **Product** | published_mcps（=`len(VIEWS)`）、knowledge_objects（=store count）、indexed_sources（distinct source 計數） |
| **Agent** | grounding_hit_rate、retrieval_precision、avg_hits、avg_score、total_events |

- 事件記錄：search/ask/simulate handler 透過 `record_retrieval`（best-effort，失敗只記 warning，不中斷請求）寫入。
- 損毀 JSONL 行讀取時 fail loud（拋 `ValueError`）。

**如何使用**
- HTTP：`GET /api/metrics` → `{product, agent}`
- Web：**Metrics 頁（`/metrics`）** — 顯示產品與 agent 指標儀表。

詳見 [ARCHITECTURE.md §18](./ARCHITECTURE.md)。

---

## 11. RBAC / API 金鑰（Access Control）

預設關閉（`auth_enabled=False`）；開啟後以 `X-API-Key` header 認證，並可限制金鑰可存取的視圖。

- principal：`{role, views, key}`；`views` 為 `*` 或明確清單。
- 金鑰格式（env `ODM_API_KEYS`）：`key:role:views`，逗號分隔多筆，`views` 為 `*` 或 `|` 分隔，如 `secret1:admin:*,secret2:dev:developer|architecture`。格式錯誤 fail loud。
- 強制點：
  - `POST /api/simulate` — 呼叫前 `require_view_access(principal, view)`（無權 403）。
  - `/api/sources`、`/api/mcp/endpoints` router 整體掛 `auth_dependency`（缺/錯金鑰 401）。
- **env-only**：`auth_enabled`、`api_keys` 不可由 UI 編輯。

詳見 [ARCHITECTURE.md §19](./ARCHITECTURE.md)。

---

## 12. 多租戶（Multi-tenancy）

opt-in（`multi_tenant=True`，env-only）。開啟後每個請求須帶 `X-Tenant` header，集合命名空間化為 `<tenant>::<collection>`，向量與圖資料隨命名空間隔離。

- 缺/空白 `X-Tenant` → 400（fail loud，絕不靜默退回共享 default）。
- 關閉時行為與單租戶完全相同。

詳見 [ARCHITECTURE.md §21](./ARCHITECTURE.md)。

---

## 13. 動態 MCP 端點（Dynamic MCP Endpoints）

把每個角色視圖掛成實際 HTTP/SSE 端點 `/mcp/{view}`，並提供發布登錄。

| 動作 | 端點 |
|------|------|
| 列出視圖端點（path/published/絕對 url） | `GET /api/mcp/endpoints` |
| 標記發布 | `POST /api/mcp/endpoints`（`{view}`；未知 404） |
| 取消發布 | `DELETE /api/mcp/endpoints/{view}` |

- 掛載失敗的單一視圖只記 log 不中斷啟動；published 狀態存於 `app.state.published_mcps`。
- **Web — MCP Builder 頁（`/mcp`）**：設定檢索政策（approved-only / rerank / search_mode）、檢視視圖規格、發布/取消發布端點並複製 URL。
- router 帶 `auth_dependency`。

詳見 [ARCHITECTURE.md §22.1](./ARCHITECTURE.md)。

---

## 14. 可觀測性（Observability）

- **結構化日誌**：`setup_logging()`（冪等；`ODM_LOG_LEVEL` 可覆寫 level）。
- **請求日誌中介層**：`RequestLoggingMiddleware` 每請求記 `method path -> status (Xms)`。
- **健康檢查**：`GET /api/health` → `{status:"ok", collection, documents, embedder, graph, version}`；`graph` 以一筆輕量查詢探測，降級回 `"unavailable"` 而非丟例外。

詳見 [ARCHITECTURE.md §20](./ARCHITECTURE.md)。

---

## 15. Agent Simulator（模擬器）

對某視圖跑所有工具，回傳 grounding 品質統計，用來在發布前驗證接地效果。

- HTTP：`POST /api/simulate`（`{view, query, top_k}`）→ `{view, tools:[{tool, results}], grounding:{hits, avg_score, knowledge_types}}`；未知視圖 404；受 RBAC 保護。
- 同時透過 `record_retrieval` 記錄指標事件。
- **Web — Simulator 頁（`/simulator`）**：輸入任務 + 選視圖，顯示每工具結果與 grounding 統計。

---

## 16. 知識庫管理（Collections）

多個獨立知識庫，可由 `?collection=` 查詢參數或 `X-Collection` header 選擇（多租戶時再加 `<tenant>::` 前綴）。

| 動作 | 介面 |
|------|------|
| 列出 / 作用中 | CLI `collections`；`GET /api/collections`；Web 側欄下拉切換器 |
| 建立 | `POST /api/collections`（`{name}`）；Web 側欄 ➕「New knowledge base」對話框 |
| 切換 | `?collection=` / `X-Collection`；Web 側欄下拉（切換後重載 console） |
| 刪除（連同圖） | `DELETE /api/collections/{name}`；Web 側欄 🗑「Delete knowledge base」對話框 |
| 清空 | CLI `clear` |
| 統計 | CLI `stats`；`GET /api/stats`；MCP `get_stats` |

> **Web 知識庫切換器**（`web/src/App.tsx` 的 `CollectionSwitcher`）：下拉切換 + ➕ 建立 + 🗑 刪除（含確認對話框）。刪除防護：僅剩一個知識庫時停用刪除鈕，刪除後自動切換到其餘知識庫並重載。建立／刪除對話框經 React portal 掛到 `document.body`，避免被 `position: sticky` 側欄的 stacking context 遮蔽（見 [DEVLOG.md](./DEVLOG.md) 2026-06-20 修正）。

---

## 17. 設定（Settings）

env 前綴 `ODM_`，可選 `.env`；UI 可編輯子集存於 `data_dir/settings.json`。

**UI 可編輯（`EDITABLE_FIELDS`）**：`embedder_backend`、`embedder_model`、`extract_knowledge`、`extraction_model`、`chunk_size`、`chunk_overlap`、`code_max_chunk_chars`、`extract_concurrency`、`search_mode`、`rerank_enabled`、`answer_model`、`review_mode`、`retrieve_approved_only`。

**env-only（不可由 UI 編輯）**：`auth_enabled`、`api_keys`、`multi_tenant`、`graph_db_*`、`ingest_root`、`max_upload_mb`、`data_dir`、`request_timeout`、`max_retries`、憑證（`ANTHROPIC_API_KEY` 等）。

- HTTP：`GET /api/settings`、`PATCH /api/settings`（`{values}`，限 editable，否則拋錯）。
- **Web — Settings 頁（`/settings`）**：編輯可變設定；collection / embedder backend / data_dir 唯讀顯示。

---

## 18. Web Dashboard 頁面總覽

React 18 + Vite + Tailwind，hash router（`web/src/main.tsx`）。所有頁面透過 `web/src/api.ts` 呼叫上述 API。

| 頁面 | 路由 | 功能 | 主要 API |
|------|------|------|----------|
| **Dashboard** | `/` | 知識庫統計 + pipeline 視覺化 + 來源登錄 | `stats`、`sources` |
| **Ingest** | `/ingest` | 路徑/上傳擷取 + 串流進度 | `upload`、`ingestStream` |
| **Explore** | `/explore` | 混合搜尋 + 過濾 | `search` |
| **Ask** | `/ask` | RAG 串流問答 + 引用 | `askStream` |
| **Browse** | `/browse` | 分頁瀏覽/編輯/刪除 chunk | `items`、`updateItem`、`deleteItem` |
| **Articles** | `/articles` | 合成文章唯讀瀏覽（依商業相關度排序 + 搜尋 + 詳情） | `articles` |
| **Review** | `/review` | 審核佇列（核准/拒絕/編輯/新增） | `items`、`approveItem`、`rejectItem`、`addItem` |
| **Graph** | `/graph` | 實體 / 工作流圖瀏覽 | `graphEntities/Entity/Workflows/Workflow` |
| **Advisor** | `/advisor` | Pre-Execution Advisor 五 facet | `advise` |
| **MCP Builder** | `/mcp` | 檢索政策 + 發布 MCP 端點 | `views`、`get/patchSettings`、`mcpEndpoints`、`publish/unpublishMcp` |
| **Simulator** | `/simulator` | 跑視圖工具 + grounding 統計 | `views`、`simulate` |
| **Metrics** | `/metrics` | 產品 + agent 指標儀表 | `metrics` |
| **Settings** | `/settings` | 執行期設定編輯 | `getSettings`、`patchSettings` |

---

## 19. 知識合成與文章（Knowledge Synthesis & Articles）

從已索引的 chunk **自動合成「文章（Article）」**——跨多個 chunk、具商業意義的高層次知識單元，經 LLM 撰寫並通過評審把關後，存入與主 collection 並列的 sibling collection `{collection}__articles`。文章不是擷取階段產物，而是事後按需執行的合成步驟。

### 19.1 合成流程（`synthesis/`）

1. **主題探勘（`topics.py:gather_topics`）**：從每個 chunk 的 `metadata["concepts"]` 蒐集候選主題，套用**結構閘門**——主題須 `cross_validated`（同時出現在程式碼與文件）或 `business_hits > 1`（多次帶商業 `knowledge_type`／受眾）才入選。圖譜實體可透過 `extra_topics` 加入候選，但仍須有 chunk 支撐才過閘。
2. **證據檢索**：每個入選主題以 `store.search(topic, top_k=8, mode="hybrid")` 取最多 8 段證據 chunk，組成帶 `[n]` 編號的引用區塊。
3. **文章撰寫（`llm.py:ArticleWriter`）**：LLM 產出 JSON `{title, body（內含 [n] 引用）, business_relevance 0–1}`。
4. **評審閘門（`llm.py:ArticleCritic`）**：LLM 評 `grounded`（無臆造）與 `business_meaningful`（真知識而非瑣事）；**兩者皆為真才保留**，否則記入 `report.rejected`。
5. **儲存**：通過者寫入 `{collection}__articles`。`Article.id = sha256(topic + sorted(source_chunk_ids))`，故合成**冪等**，重跑不產生重複。

合成結果回傳 `SynthesisReport`（`topics_gated`／`articles_written`／`stored`／`rejected`／`errors`），全程 Fail Loud。

### 19.2 如何使用

- **CLI**：`opendomainmcp synthesize [--limit N] [--dry-run]`——`--limit` 控制處理主題數（成本控制），`--dry-run` 只計數不寫入。按需執行，**不在擷取階段、無自動排程**。
- **HTTP**：`GET /api/articles?limit=200&offset=0` → 文章清單（`id`、`title`、`topic`、`business_relevance`、`cross_validated`、`sources`、`body`）。
- **Web**：Articles 頁（`/articles`）唯讀瀏覽，左側依 `business_relevance` 由高至低排序、可搜尋標題／主題／內文，右側顯示全文與來源清單。
- **檢索**：合成後的文章自動參與搜尋與問答（見 §20）。

### 19.3 相關設定

- `extraction_model`：文章撰寫與評審共用的 LLM 模型（runtime-editable）。
- `retrieve_include_articles`：檢索時是否納入文章（預設 on，runtime-editable）——見 §20。
- `llm_backend`：`anthropic`（預設）或 `openai`。

## 20. 文章增強檢索（Article-Augmented Retrieval）

搜尋與問答時，將合成文章與原始 chunk **一起檢索並以 RRF 融合**，使高層次知識與細節證據並陳。

- **機制（`retrieval/unified.py:search_unified`）**：先檢索 chunk；若 `retrieve_include_articles` 為 on 且 `{collection}__articles` 非空，再以相同參數檢索文章，兩份排名以 `rrf_fuse`（k=60）融合取 top_k，文章與 chunk 平等競爭。
- **過濾**：文章 `metadata["kind"] == "article"`，`where` 過濾同樣套用於文章（如 `kind=code` 會排除文章）。
- **接線**：`/api/search`、`/api/ask` 皆走 `search_unified`；關閉 `retrieve_include_articles` 即回退為純 chunk 檢索。

---

## 程式碼與設計差異（Discrepancies）

依「Fail Loud」原則，記錄目前程式碼與註解/文件意圖不一致處：

1. **`metrics/__init__.py` 模組 docstring 過時**：寫「目前未接線到 API 或 pipeline」，但 `MetricsRecorder`/`record_retrieval` 已實際接線到 `/api/search`、`/api/ask`、`/api/simulate` 與 `/api/metrics`（`api/app.py`、`api/insight_routes.py`）。功能正常，僅 docstring 需更新。
2. **GraphQL chunk 的 `kind`**：`loader.load_file` 對 GraphQL 回 `LoadedDoc(kind="api")`，但 `split_graphql` 產生的 `Chunk.kind` 為 `"text"`（`language="graphql"`、`knowledge_type="API"`）。後果：Developer MCP 的 `get_api_implementation`（依 `knowledge_type=API`）可命中 GraphQL；但 `search_code`（依 `kind=code`）不會。OpenAPI 同樣不是 `kind=code`，行為一致，惟與「API spec 屬程式碼面」的直覺略有出入，使用者依 `knowledge_type` 過濾即可。
3. **Confluence Wiki 尚未接線**：`wiki.py` 已有 `looks_like_confluence_html`，但 `loader.load_file` 的 `.xml` 分支只處理 MediaWiki；Confluence HTML 目前走一般 HTML 文件路徑，未做 per-page 展平。
4. **PRD 對照**：PRD 將 Metrics 儀表、Wiki、GraphQL、Dependency Graph、Advisor、RBAC 列為待辦/未實作；本 sprint 後這些皆已落地（對應 Web 頁面與 API 端點如上）。以本文件與 ARCHITECTURE.md 為實作面真實來源。
5. **知識庫下拉的 chunk 數標籤**：`CollectionSwitcher` 於載入時抓一次 `GET /api/collections` 的 count 並顯示於下拉（如 `project_self (296)`），擷取/刪除後該數字要重載 console 才更新（純顯示，不影響實際資料；Dashboard 的 INDEXED CHUNKS 即時正確）。

---

_最後更新：2026-06-21（新增 §19 知識合成與文章、§20 文章增強檢索；Web 頁面總覽補上 Articles）_
