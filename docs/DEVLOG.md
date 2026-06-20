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
