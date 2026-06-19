# 設計文件：Entity Graph 基礎（Phase 3 子專案 ①）

> 狀態：已通過 brainstorming 設計討論，待寫 implementation plan。
> 對應任務：TASKS.md 4.1（實體抽取與正規化）、4.2（圖儲存層）、4.3（實體查詢 API/MCP）。
> 產品脈絡：PRD §13 Roadmap Phase 3（Entity / Dependency / Workflow Graph）。

## 1. 背景與範圍

Phase 3「知識圖譜」共 9 任務、4 個子區塊，整體規模遠大於單一 spec，故拆為 4 個獨立子專案：

| 子專案 | 任務 | 邊界 |
|--------|------|------|
| **① Entity Graph 基礎（本文件）** | 4.1 + 4.2 + 4.3 | 圖儲存層是整個 Phase 3 的命脈，解鎖 ②③④ |
| ② Dependency Graph | 4.4 + 4.5 | 從 AST import/call 建相依邊 |
| ③ Workflow Graph | 4.6 + 4.7 | 從 Runbook/Workflow 抽有序步驟 |
| ④ 圖視覺化 | 4.8 + 4.9 | 前端圖元件 + `/api/graph` 視覺化 |

本子專案**只做 ①**。各子專案各自 spec → plan → 實作。

### 範圍內
- 在每-chunk 知識萃取呼叫中，疊加抽取**有型別的實體與關係**。
- 以 **MariaDB** 持久化 nodes/edges，與向量（仍在 Chroma）同生命週期增量同步。
- 提供實體查詢的 **API 端點**與 **MCP 工具**（純讀、無 LLM）。

### 範圍外（後續子專案）
- 前端圖視覺化（④）、AST 相依抽取（②）、工作流步驟抽取（③）。
- 把 Chroma 向量/metadata 遷移到 MariaDB（未來可能，本子專案不做，YAGNI）。

## 2. 關鍵決策（brainstorming 結論）

| 決策 | 選擇 | 理由 |
|------|------|------|
| 圖儲存後端 | **MariaDB** | 使用者指定。可查詢鄰居、事務式 upsert/prune、並發讀取。 |
| 依賴範圍 | **全平台必需** | 啟動時連線並 `ensure_schema()`，連不上即 fail-loud。 |
| 實體建構方式 | **新增 LLM 抽取（有型別）** | 比解析自由文字精準，產出 typed entities/relations。 |
| 抽取接法 | **併進現有萃取呼叫** | 一個 chunk 仍只一次 LLM 呼叫（token 不加倍、DRY）。 |
| Python driver | **PyMySQL** | 純 Python、免裝系統函式庫、好攜帶。 |
| 向量儲存 | **維持 Chroma** | 本子專案不遷移。 |

## 3. 架構

沿用單一 `build_context()` 真實來源原則：

```
ingest pipeline ──┬──> ChromaStore   (vectors, 不變)
                  └──> GraphStore     (MariaDB, 新增) ── nodes/edges
                                            ▲
        API /api/graph/* ── MCP get_entity / list_related_entities ── 查 GraphStore
```

圖與向量**同生命週期**：`store.upsert(chunks)` 之後寫 nodes/edges；prune/`delete_ids(stale)` 之後 `graph.delete_for_chunks(stale)`。

## 4. 模組設計（`graph/`，多小檔、高內聚）

- `graph/models.py` — `Entity`(id, normalized_name, display_name, type, aliases, chunk_ids)、`Edge`(src_id, dst_id, relation_type, chunk_id, confidence) dataclasses（無業務邏輯）。
- `graph/normalize.py` — 純函式：大小寫/空白正規化、別名合併、`normalized_name` 計算。確定性、易測。
- `graph/store.py` — `GraphStore`（MariaDB repository）：`connect` / `ensure_schema` / `upsert_entities` / `upsert_edges` / `delete_for_chunks` / `get_entity` / `neighbors`。定義 `GraphStoreProtocol` 介面供測試注入 fake。
- `graph/builder.py` — 把單一 chunk 的 `KnowledgeUnit.entities/typed_relations` 經 normalize 轉成 nodes/edges 並寫入 store。

### 型別詞彙（單一真實來源放 `models.py`，與 `KNOWLEDGE_TYPES` 並列）
- `ENTITY_TYPES`：`Component`, `Service`, `Function`, `Class`, `API`, `Concept`, `Person/Team`, `Resource`
- `RELATION_TYPES`：`depends_on`, `calls`, `owns`, `part_of`, `uses`, `related_to`（fallback）
- 未知值 clamp 到 fallback（`related_to` / `Concept`），不 fail-loud（沿用既有 `_norm_choice` 模式）。

### MariaDB schema
- `entities(id PK, normalized_name UNIQUE, display_name, type, aliases JSON, confidence)`
- `edges(id PK, src_id FK, dst_id FK, relation_type, chunk_id, confidence, UNIQUE(src_id, dst_id, relation_type, chunk_id))`
- `chunk_id` 對應 Chroma 的 `Chunk.id`（content_hash），讓增量 prune 精準刪圖。
- `ensure_schema()` 冪等自動建表。

## 5. 萃取變更（`extract/knowledge.py`）

`_SYSTEM` prompt 新增兩個 key、`_parse` 增加正規化解析、`KnowledgeUnit` 增加兩欄位（全帶預設、舊索引相容）：

```json
"entities": [{"name": "...", "type": "<ENTITY_TYPES 之一>"}],
"typed_relations": [{"src": "...", "dst": "...", "type": "<RELATION_TYPES 之一>"}]
```

- 一個 chunk 仍只一次 LLM 呼叫。`max_tokens` 由 600 略上調以容納結構化欄位。
- 舊的自由文字 `concepts`/`relations` **保留不動**（embedding 仍用、向後相容）；新欄位疊加。
- 沿用白名單 clamp，未知 type 落 fallback，不 fail-loud。

## 6. 查詢層（4.3）

### MCP 工具（加進 Developer 與 Architecture 視圖，沿用 `views/__init__.py` 宣告式定義）
- `get_entity(name)` → 實體 + 型別 + 別名 + 來源 chunk 摘要。
- `list_related_entities(name, relation_type?, depth=1)` → 鄰居節點與邊。depth 預設 1、**上限 2**，避免爆量。

### API 端點（`api/app.py`，沿用既有 response envelope）
- `GET /api/graph/entity/{name}` → 單一實體 + 直接鄰居。
- `GET /api/graph/entities?type=&q=&limit=` → 列表/搜尋，分頁，預設 `limit=50`。
- **不做**前端圖視覺化（屬子專案 ④）。

查詢純讀、無 LLM，走 `GraphStore.get_entity` / `neighbors`。

## 7. Config、啟動、Context 接線

### Config（`config.py`，env/.env 可覆寫，pydantic-settings 一致）
```
graph_db_host
graph_db_port = 3306
graph_db_user
graph_db_password
graph_db_name
```
`.env.example` 補上範例。

### 啟動（全平台必需）
`build_context()` 啟動時連線 MariaDB 並 `ensure_schema()`，連不上即 fail-loud（清楚錯誤訊息）。

### Context 接線
- `Context` dataclass 加 `graph: GraphStore`。
- `Pipeline` 注入 `graph`，在 `store.upsert(chunks)` 後寫 nodes/edges，在 prune/`delete_ids(stale)` 後 `graph.delete_for_chunks(stale)`。

## 8. 測試策略（保住 133 綠燈基線、≥80% 覆蓋）

- `graph/normalize.py`、`graph/builder.py`、`_parse` 擴充 → **純單元測試**，零外部服務。
- `GraphStore` → 抽 `GraphStoreProtocol` 介面，pipeline/views/api 測試注入 **in-memory FakeGraphStore**（比照 `FakeExtractor` / `conftest.py`）。
- MariaDB **整合測試**標記 `@pytest.mark.integration`，需 `graph_db_*` 環境變數才跑；CI 預設略過或用服務容器。
- `FakeExtractor` 延伸回傳新的 `entities`/`typed_relations`（比照 task 2.6）。

## 9. 資料遷移/回填

- 新 schema 由 `ensure_schema()` 冪等自動建表。
- 既有 Chroma chunk 無結構化實體 → 提供 CLI `graph rebuild` 重跑萃取回填圖。**次要、可選補強**；核心是新擷取走完整路徑。

## 10. 任務切分（每個 ≤Medium，可比照 Wave 並行；注意檔案互斥）

1. `models.py` 詞彙（`ENTITY_TYPES`/`RELATION_TYPES`）+ `KnowledgeUnit` 欄位 + `graph/models.py`。
2. `extract/knowledge.py` prompt/parse 擴充 + `conftest.py` FakeExtractor 延伸。
3. `graph/normalize.py` + `graph/builder.py`（純邏輯 + 單元測試）。
4. `graph/store.py` MariaDB repo + `GraphStoreProtocol` + `FakeGraphStore` + `config.py` 欄位 + `.env.example`。
5. pipeline 接線（upsert/prune 同步圖）+ `context.py` 接線。
6. `api/app.py` 端點 + `views/__init__.py` / `server.py` MCP 工具。

> 檔案熱點：`extract/knowledge.py`（任務 2）、`pipeline.py`+`context.py`（任務 5）、`api/app.py`+`views`（任務 6）彼此互斥，需序列化或分批。

## 11. 成功定義

- 擷取一份含明確實體/關係的來源後，MariaDB `entities`/`edges` 有對應資料。
- `GET /api/graph/entity/{name}` 與 MCP `get_entity` / `list_related_entities` 回傳正確鄰居。
- 刪除/更新來源後，圖隨向量同步 prune。
- 全套件測試綠燈（≥ 既有 133），新邏輯覆蓋 ≥80%；MariaDB 整合測試在有環境變數時通過。
- 未設定 `graph_db_*` 時平台 fail-loud 並給出清楚錯誤。

---

_建立：2026-06-19（superpowers:brainstorming）_
