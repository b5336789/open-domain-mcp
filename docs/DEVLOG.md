# OpenDomainMCP — 開發過程記錄（Development Log）

> 本文件詳細記錄本輪「完成 PRD 所有功能」開發衝刺的過程：目標、現況盤點、
> 並行排程、各 wave 的任務、驗證結果與決策。產品需求見 [PRD.md](./PRD.md)，
> 任務清單見 [TASKS.md](./TASKS.md)，架構見 [ARCHITECTURE.md](./ARCHITECTURE.md)。

---

## 0. 衝刺目標（2026-06-19）

- 完成 PRD 列出的**所有**功能。
- 開發過程詳細記錄（本檔）。
- 各種技術文件與系統功能詳細說明放上網頁（`docs/*.html` 自動生成 + 前端建置）。
- 無相依的任務盡量並行（每個 subagent 持有互斥檔案集，避免合併衝突）。

## 1. 起始現況盤點

測試基線：`pytest` → **187 passed, 3 skipped**（3 skipped 為需要 MariaDB 連線的圖儲存整合測試）。

TASKS.md 狀態欄已過時：實際上 Entity Graph（4.1/4.2/4.3）、Workflow Graph（4.6/4.7，
PR #14）、Graph API（4.9）、Metrics 骨架（5.4）、Evals（5.8）皆已併入。

經 grep 驗證，**真正未完成**的任務共 18 項：

| 任務 | 說明 | 主要檔案 |
|------|------|----------|
| 3.2 | Wiki export 擷取 | `ingest/wiki.py`(新)、`loader.py` |
| 3.3 | 舊資料 review 回填 CLI | `cli.py`、`store/` |
| 3.6 | Workspace 來源登錄 | `api/app.py`、`web/Dashboard.tsx` |
| 4.4 | 程式碼相依抽取 | `graph/deps.py`(新)、`code_splitter.py`、`pipeline.py` |
| 4.5 | trace_dependency 升級 | `views/__init__.py` |
| 4.8 | 圖瀏覽 UI | `web/pages/Graph.tsx`(新) |
| 5.1 | Advisor 彙整器 | `advisor/`(新) |
| 5.2 | Advisor MCP 工具 | `server.py` |
| 5.3 | Advisor API + UI | `api/app.py`、`web/pages/Advisor.tsx`(新) |
| 5.5 | Product 指標 | `metrics/`、`api/app.py` |
| 5.6 | Agent 指標 | `metrics/`、`api/app.py` |
| 5.7 | 指標儀表板 | `web/pages/Metrics.tsx`(新) |
| 6.1 | 動態 MCP 端點發布 | `server.py`、`api/app.py`、`McpBuilder.tsx` |
| 6.2 | 權限/角色（RBAC） | `api/app.py`、`config.py` |
| 6.3 | 多租戶隔離 | `context.py`、`store/` |
| 6.4 | E2E 前端測試 | `web/tests/`(新) |
| 6.5 | CLI Git/Zip 文件補強 | `cli.py` |
| 6.7 | 觀測性/日誌 | `api/app.py` |

## 2. 並行排程策略

熱點檔案 `api/app.py`（被 3.6/5.3/5.5/5.6/6.1/6.2/6.7 共用）、前端 wiring
（`App.tsx`/`api.ts`/`main.tsx`）無法安全並行。策略：

- **後端純新模組 + 互斥檔案** → 並行 subagent（共享工作樹、檔案集互斥）。
- **`api/app.py`、前端 wiring** → 由主控（我）序列整合，避免合併衝突。

### Wave 1（5 個並行 subagent，檔案集互斥）

| Agent | 任務 | 持有檔案 |
|-------|------|----------|
| A1 | 3.2 Wiki 擷取 | `ingest/wiki.py`(新)、`loader.py` |
| A2 | 4.4 + 4.5 相依圖 + trace 升級 | `graph/deps.py`(新)、`code_splitter.py`、`pipeline.py`、`graph/store.py`、`graph/models.py`、`views/__init__.py` |
| A3 | 5.1 + 5.2 Advisor + MCP | `advisor/`(新)、`server.py` |
| A4 | 5.5 + 5.6 指標邏輯 | `metrics/__init__.py` |
| A5 | 3.3 + 6.5 backfill + CLI 文件 | `cli.py`、`store/chroma_store.py` |

**Wave 1 結果（✅）：** 5 個 subagent 並行（檔案集互斥）完成 3.2 / 4.4+4.5 / 5.1+5.2 /
5.5+5.6 / 3.3+6.5，各自跑過相關測試。合併後全套件 **226 passed**（基線 187，+39 測試），零回歸。
commit `df8c9fa`。

### Wave 2（後端 API 熱點，5 個並行 subagent + 主控整合）

`api/app.py` 為多任務共用熱點，故先抽出共享相依 `api/deps.py`（`get_ctx`），再讓各功能
落在獨立的 router/helper 新模組，最後由主控統一在 `app.py` 串接（單一作者改熱點檔，避免衝突）。

| 模組（新檔） | 任務 |
|--------------|------|
| `api/auth.py` + `config.py` | 6.2 RBAC（API key→role→views，預設關閉） |
| `api/observability.py` | 6.7 結構化日誌、request middleware、強化 `/api/health` |
| `api/insight_routes.py` | 5.3 `/api/advise`、5.5/5.6 `/api/metrics` + 事件記錄 |
| `api/source_routes.py` + `store` | 3.6 `/api/sources`（list/delete） |
| `api/mcp_endpoints.py` | 6.1 掛載 `/mcp/{view}` SSE、`/api/mcp/endpoints` 發布登記 |

主控整合：`include_router`、`mount_mcp_apps`、`setup_logging`＋middleware、health、RBAC（`/api/simulate`
視圖授權）、search/ask/simulate 事件記錄。**263 passed**（+37）。再加 6.3 多租戶（`api/deps.py` 以
`X-Tenant` 命名空間化 collection，預設關閉）→ **268 passed**。commits `…`（wave2 後端、6.3）。

### Wave 3（前端，共享 wiring 由主控、頁面由 5 個並行 subagent）

主控先補齊共享 wiring：`api.ts`（型別＋方法）、`App.tsx`（導覽）、`main.tsx`（路由）、`icons.tsx`（3 圖示）。
再並行產出頁面（互斥檔案）：

| 檔案 | 任務 |
|------|------|
| `pages/Graph.tsx`（新） | 4.8 實體鄰接 + 工作流步驟瀏覽 |
| `pages/Advisor.tsx`（新） | 5.3 Pre-Execution Advisor |
| `pages/Metrics.tsx`（新） | 5.7 指標儀表板 |
| `pages/Dashboard.tsx`（改） | 3.6 來源登錄區塊 |
| `pages/McpBuilder.tsx`（改） | 6.1 發布/取消發布 HTTP 端點 |

驗證：`tsc --noEmit` 綠燈、`vite build` 成功（輸出至 `api/static`）。

### Wave 4（E2E + 文件）

- **6.4 E2E**：Playwright（`web/tests/`），以 route interception mock `/api/*`，不需後端即可跑；
  **7 passed**（chromium）。
- **文件上網頁**：擴充 `ARCHITECTURE.md`、新增 `FEATURES.md`（系統功能詳細說明），於 `docs/build.py`
  新增 `features.html` / `devlog.html` 頁，重新生成整個 docs 靜態站。

## 3. 最終驗證

- 後端：`pytest` → **276 passed, 3 skipped**（含 `test_integration_wiring.py` 對完整 `create_app` 的端到端接線驗證；3 skipped 為需 MariaDB 的整合測試）。基線 187 → 276，+89 測試、零回歸。
- 前端：`tsc` 綠燈、`vite build` 成功、Playwright E2E **7 passed**。
- 文件：`docs/*.html` 由 Markdown 重新生成，導覽含 功能說明 / 技術架構 / 任務 / PRD / 開發記錄。

---

## 4. 2026-06-20 — 瀏覽器全功能實測與前端修正

以 Playwright 驅動瀏覽器（先用內建 Chromium，使用者安裝 Chrome 後改用其本機 Chrome）對運行中的 `opendomainmcp-web`（`127.0.0.1:8088`）做全功能實測，並把整個專案拿來自我分析，端到端驗證擷取流程。

### 4.1 實測範圍與結果

- **12 個頁面全部渲染正常**、無 console error / network 失敗。
- **互動流程全通過**：Explore 混合搜尋（回傳評分與 concepts）、Ask SSE 串流問答（含 `[n]` 引用）、Advisor 五 facet、Simulator（Developer MCP view 命中 8 筆）、Graph 實體詳情、Metrics 聚合、Settings 存檔持久化（改 150→160 經後端確認再復原）。
- **完整生命週期走查**：新建 collection → 擷取 → 切段/萃取/向量化/存庫 → 分析 → 刪除。

### 4.2 自我擷取（dogfooding）

新建 `project_self` 知識庫，擷取 `src/opendomainmcp/`：

- **切段**：tree-sitter AST，**42 檔 / 296 chunks**（`node_type=block` + 行號；最大 `api/app.py` 44、`graph/store.py`/`ingest/pipeline.py` 各 20）。
- **萃取**：本地 LLM（LM Studio `qwen3-coder-30b`）產出 summary/concepts/knowledge_type，~82% 成功；少數回非法 JSON 由 pipeline fail-soft 處理（仍照常切段+向量化入庫）。
- **向量化**：`qwen3-embedding-0.6b`，**dim 1024**。
- **存庫**：Chroma collection `project_self`，count 296。
- **接地驗證**：Explore 命中 `ingest/pipeline.py`（"load→split→extract→embed→store … Fail Loud"）；Ask 對「本專案架構」「混合檢索如何用 RRF 融合 dense+BM25」皆給出有引用的正確答案；Metrics grounding hit rate **92.3%**。

### 4.3 發現與修正（PR #18，merge commit `22b7137`）

1. **新建 collection 對話框被遮蔽**（使用者回報）。根因：Modal 是 `fixed inset-0 z-50`，但被渲染在 `position: sticky` 側欄內，`sticky` 建立的 stacking context 把 `z-50` 困住，`<main>`（DOM 順序在後）整片蓋上。以 `elementFromPoint` 在對話框中心取到的是 dashboard 卡片而非 Modal，程式化確認。**修法**：`Modal` 改用 `createPortal` 掛到 `document.body`（`web/src/components/ui.tsx`），一次修好所有 Modal。
2. **UI 無法刪除 collection**。`api.deleteCollection` 與後端 `DELETE /api/collections/{name}` 都在，但 UI 從未呼叫。**修法**：側欄加 🗑 按鈕 + 確認框（`web/src/App.tsx`）；僅剩一個知識庫時停用、刪除後自動切換並重載。實測：建立 `ui_delete_test` → 刪除 → 消失，`project_self`/`domain_knowledge` 完好。
3. **更正前一輪誤判**：先前以 path-style URL（`/ingest`）測試而誤報「deep-link 會 404」；實際應用為 **HashRouter**（`#/ingest`），deep-link 與 F5 重新整理皆正常。
4. **已知小問題（未修）**：知識庫下拉的 chunk 數標籤需重載 console 才更新（純顯示）。

### 4.4 驗證

- `vite build` 成功（新 bundle）；修正後以獨立 headless 瀏覽器回歸：Modal portal 對話框 5 取樣點全落在 Modal 內（0 miss）、刪除流程通過、無 console error。
- PR #18 已併入 `main`，工作樹乾淨。

---

## 5. 2026-06-20～21 — 知識合成與文章系統（Phase 6）

把零散 chunk 升華為跨 chunk、具商業意義的**文章（Article）**，並讓文章參與檢索與瀏覽。三條主線循 brainstorm → spec → plan → 實作 → 測試 推進，逐 PR 併入。

### 5.1 知識合成編排器（PR #20）

`synthesis/` 新模組：`topics.py` 從 chunk metadata 的 concepts 探勘候選主題，套**結構閘門**（須 cross-validated 或 business_hits>1，避免把瑣碎詞變文章）；每主題以 hybrid 檢索取最多 8 段證據；`llm.py` 的 `ArticleWriter` 產出帶 `[n]` 引用的 JSON 文章，`ArticleCritic` 再評 `grounded` 與 `business_meaningful`，**兩者皆真才保留**（雙閘門對抗臆造與廢話）。文章存入 sibling collection `{collection}__articles`，`Article.id` 為內容雜湊故**冪等**。`Article` duck-type chunk 儲存介面，無需改動 store。

### 5.2 文章增強檢索（PR #21）

`retrieval/unified.py:search_unified` 在既有 chunk 檢索之上，以 **RRF（k=60）** 融合文章命中，讓高層次知識與細節證據並陳；`retrieve_include_articles` 旗標（EDITABLE，預設 on）可一鍵回退純 chunk。`/api/search`、`/api/ask` 改走統一路徑。`where` 過濾同步套用文章，故 `kind=code` 等過濾語意一致。

### 5.3 Articles 瀏覽頁（PR #22）+ 內部 collection 隱藏（PR #23）

新增 `GET /api/articles` 與 Web `Articles` 頁（唯讀：依 business_relevance 排序、搜尋、詳情 + 來源），並補 Playwright smoke（`cf77d8a`）。隨後修正 collection 列表會列出內部 `__articles` sibling 的問題（`b924b29`），避免使用者誤選。

### 5.4 Dashboard pipeline 真實資料（PR #24）

首頁 Pipeline 卡片原以寫死字串（files / AST·text / Claude / vectors / Chroma / hybrid）冒充「狀態」；改為各階段顯示 `/api/stats`+`/api/sources`+`/api/settings` 的真實值（來源數、chunk 數、extraction on/off、embedder、collection、search mode），未載入時顯示 skeleton。新增 Playwright 斷言並以實機 curl + 截圖驗證。

### 5.5 驗證

- 合成與檢索全離線測試覆蓋：`test_synthesis_topics`／`test_synthesis_article_model`／`test_synthesis_articles`（含冪等、dry-run、critic 拒絕）／`test_synthesis_llm`／`test_retrieval_unified`。
- Web E2E（Playwright，API mock）全綠，含新 Articles smoke 與 Dashboard pipeline 斷言。
- PR #20–24 均已併入 `main`。

---

## 6. 2026-06-27 — Enterprise Redesign Wave 1

依企業技術主管視角完成系統盤點與第一波重設計實作。完整藍圖見
`docs/superpowers/specs/2026-06-27-enterprise-redesign-blueprint-design.md`，
Wave 1 實作計畫見 `docs/superpowers/plans/2026-06-27-enterprise-wave-1-command-center.md`。

### 6.1 範圍

- **Command Center**：將首頁改為 knowledge base 生命週期總覽，呈現 readiness score、blockers/warnings、source/review/job/graph health 與下一步動作。
- **Workspace readiness API**：新增 `/api/workspace/readiness`，由 Chroma stats/source registry、review state、TaskStore、graph availability 彙整企業操作狀態。
- **Source Intake**：以新 `/intake` workspace 取代舊 Ingest 導覽，保留 `/ingest` legacy route，整合 server path ingest、upload ingest、source registry 與 delete source。
- **測試穩定化**：修正 direct pytest launcher 對 `tests.conftest` 的 import、Playwright API mocks、Task Center 對 malformed task list 的防禦。

### 6.2 Review Gate 與修正

- Readiness contract 經兩段 review 補齊 `stats`、`source_health`、`review_health.approved_ratio`、zero-filled `job_health`、`graph_health`。
- 修正 no-approved、rejected、unset、pending、active jobs、failed jobs 的狀態優先序，避免錯誤標示 `ready` 或引導重複 ingest。
- `/api/workspace/readiness` 改用 app-level `task_store`，依 active collection 篩選；task history 損壞時降級為 failed job signal，而不是 500。
- 補齊 graph exception、queued/running jobs、task-store list/row conversion failure、`create_app` mounted endpoint 的回歸測試。

### 6.3 驗證

- 後端 focused：`tests/test_workspace_readiness.py` → **20 passed**；`tests/test_integration_wiring.py tests/test_observability.py` → **13 passed**。
- 前端 focused：`npm run build` 成功；`npm run test:e2e -- tests/source_intake.spec.ts` → **2 passed**；`npm run test:e2e -- tests/smoke.spec.ts` → **3 passed**。
- Source Intake review：spec pass、quality approved。已知非阻塞風險：focused E2E 未覆蓋 foreground SSE ingest、upload 與 drag/drop；legacy `/#/ingest` alias 不會高亮 `/intake` nav。

---

## 7. 2026-06-27 — Enterprise Redesign Wave 2A

Wave 2A 承接企業重設計藍圖的「Quality Lab And Readiness Gates」，先建立可量測的品質證據層，不提前引入 publish override / audit decision 的重治理。設計與計畫見 `docs/superpowers/specs/2026-06-27-enterprise-wave-2a-quality-lab-design.md`、`docs/superpowers/plans/2026-06-27-enterprise-wave-2a-quality-lab.md`。

### 7.1 範圍

- **Readiness health 擴充**：`/api/workspace/readiness` 新增 `article_health` 與 `retrieval_health`，在沒有文章或 metrics 時維持 zero-filled contract。
- **Quality Evidence API**：新增 `/api/quality/evidence`，將 Coverage、Review、Articles、Retrieval、Graph、Jobs 六個 gate 彙整成穩定 evidence cards。
- **Quality Lab workspace**：新增 `/quality` 前端工作區，呈現 evidence score、gate 狀態、details、action 與跨工作區捷徑。
- **Knowledge Review article curation**：Review 頁新增 Article Curation 旁欄，列出 synthesized articles、relevance、cross-validation、source count，並可排程 synthesis task。
- **Local deployment hardening**：新增 env-only `graph_store_backend`，可用 `ODM_GRAPH_STORE_BACKEND=null` 在沒有 MariaDB 的環境啟動 dashboard；同時序列化 per-collection context 首次建立，避免瀏覽器並行 API 請求觸發 Chroma 初始化競態。

### 7.2 驗證

- 後端全測：`PYTHONPATH=src .venv/bin/python -m pytest -q` → **448 passed, 3 skipped**。
- 前端 build：`npm run build` 成功，Vite 產出 53 modules。
- Web E2E：`npm run test:e2e` → **14 passed**。
- Local deployment smoke：`ODM_GRAPH_STORE_BACKEND=null` 啟動 `opendomainmcp-web`，`/api/health` 與 `/api/quality/evidence` 回 200，前端 SPA 靜態入口正常載入。

---

## 8. 2026-06-27 — Enterprise Redesign Wave 3A

Wave 3A 啟動 publish governance 的第一個可交付切片：不替換既有 FastMCP SSE transport，而是讓 MCP publish/unpublish 從 transient toggle 變成可審計 decision record。設計與計畫見 `docs/superpowers/specs/2026-06-27-enterprise-wave-3a-publish-governance-design.md`、`docs/superpowers/plans/2026-06-27-enterprise-wave-3a-publish-governance.md`。

### 8.1 範圍

- **Publish decision store**：新增 `src/opendomainmcp/publish/decisions.py`，以 `settings.data_dir / "publish_decisions.json"` 持久化 publish/unpublish decision，支援 collection/view 的 latest/history 查詢。
- **Readiness-gated publish API**：`/api/mcp/endpoints` 回傳 `status`、`latest_decision`、`history`；當 Quality Evidence 不是 `ready/published` 時，publish 必須提供 `override_reason`，否則回 409。
- **MCP Publish workspace**：`/#/mcp` 從 MCP Builder 升級為 MCP Publish，顯示 Publish readiness gates、endpoint decision history，以及非 ready 狀態的 override modal。
- **Modal accessibility**：共用 Modal 補上 `role="dialog"`、`aria-modal` 與 label wiring，讓 override flow 可由 Playwright role/label 穩定驗證。

### 8.2 驗證

- 後端全測：`PYTHONPATH=src .venv/bin/python -m pytest -q` → **455 passed, 3 skipped**。
- 前端 build：`npm run build` 成功，Vite 產出 53 modules。
- Web E2E：`npm run test:e2e` → **14 passed**。

---

## 9. 2026-06-28 — Enterprise Redesign Wave 4A

Wave 4A 將 Simulator 從一次性試跑工具提升為可重跑的 MCP validation suite。目標是讓 publish governance 不只看靜態品質 evidence，也能看「代表性 agent 任務是否真的能接地並回傳工具結果」。設計與計畫見 `docs/superpowers/specs/2026-06-28-enterprise-wave-4a-validation-suite-design.md`、`docs/superpowers/plans/2026-06-28-enterprise-wave-4a-validation-suite.md`。

### 9.1 範圍

- **Validation scenario store**：新增 `src/opendomainmcp/validation/store.py`，以 `settings.data_dir / "validation_runs.json"` 持久化 scenario 與 run。scenario 依 collection/view 隔離；summary 以每個 scenario 最新一次 run 計算 `passed/failed/pass_rate/status/latest_run`。
- **Validation API**：新增 `/api/validation/scenarios`、`/api/validation/scenarios/{id}/run`、`/api/validation/run`、`/api/validation/summary`。`/api/validation/run` 會先執行 shared simulator runner，再把結果保存成 scenario + run。
- **Simulation quality gate**：Quality Evidence 新增 Simulation gate；沒有 run 時維持 `validating`，任一 latest run failed 則阻擋，全部 latest run passed 才計入 ready score。
- **MCP Publish validation summary**：`/api/mcp/endpoints` 每個 view 回傳 validation summary，前端在 endpoint row 顯示 `Validation passed/failed/validating` 與 passed/failed/scenario count。
- **Simulator workflow**：Simulator 新增 validation scenarios 區塊，可列出 saved scenarios、保存 current simulation、重跑 scenario，並在 UI 顯示 latest run 狀態。

### 9.2 驗證

- Store focused：`tests/test_validation_store.py` → **8 passed**。
- API focused：`tests/test_validation_api.py` + validation wiring → **9 passed**。
- Quality/MCP focused：`tests/test_quality_evidence.py tests/test_mcp_endpoints.py` → **14 passed**。
- Frontend focused：`npm run build` 成功；`npm run test:e2e -- tests/simulator.spec.ts tests/quality_lab.spec.ts tests/mcp_builder.spec.ts` → **4 passed**。
- 後端全測：`PYTHONPATH=src .venv/bin/python -m pytest -q` → **479 passed, 3 skipped**。
- 前端全測：`npm run build` 成功；`npm run test:e2e` → **15 passed**。

---

## 10. 2026-06-28 — Enterprise Redesign Wave 5A

Wave 5A 將既有 Task Center 與 in-process worker 硬化為可解釋、可復原、可重試的 job foundation。這一波不導入 Celery/Redis/RQ 等新 queue 依賴，而是先把 job contract、failure evidence 與 operator action 做穩。設計與計畫見 `docs/superpowers/specs/2026-06-28-enterprise-wave-5a-job-reliability-design.md`、`docs/superpowers/plans/2026-06-28-enterprise-wave-5a-job-reliability.md`。

### 10.1 範圍

- **Job status contract**：`Task` 新增穩定狀態常數、active/terminal/retryable status sets，並在 persisted `tasks.json` 載入與 status update 時 fail loud 驗證未知狀態。
- **Transition-aware TaskStore**：新增 `transition()`、`mark_recovered()`、`retry()`；terminal transition 自動記錄 `finished_at`，recovery 記錄 `recovery_count/recovered_at/last_transition`，retry 會建立新的 queued task 並保留 `retry_of` reference。
- **Worker evidence**：worker start 會累計 `attempts`；exception 會寫入 `error_type/error_message/error`；cancelled task 會留下可讀 result；process restart recovery 會走 `mark_recovered()`。
- **Retry API**：新增 `POST /api/tasks/{task_id}/retry`，unknown id 回 404，非 retryable state 回 409，成功後回傳新 queued task 並喚醒 worker。
- **Task Center UX**：task card 顯示 structured failure evidence、Recovered badge、Retry button；active jobs 的 Cancel 與 Clear finished 行為維持不變，card 加上 `role="group"` 讓 E2E 與輔助技術有穩定名稱。

### 10.2 驗證

- Backend focused：`tests/test_task_store.py tests/test_task_worker.py tests/test_task_api.py tests/test_workspace_readiness.py` → **47 passed**。
- Frontend focused：`npm run test:e2e -- tests/smoke.spec.ts` → **4 passed**。
- 後端全測：`PYTHONPATH=src .venv/bin/python -m pytest -q` → **492 passed, 3 skipped**。
- 前端 build：`npm run build` 成功，Vite 產出 53 modules。
- 前端全測：`npm run test:e2e` → **16 passed**。
