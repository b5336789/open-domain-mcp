# 設計文件：Workflow Graph（Phase 3 子專案③）

> 狀態：已通過 brainstorming 設計討論，待寫 implementation plan。
> 對應任務：TASKS.md 4.6（工作流步驟抽取）、4.7（工作流查詢）。
> 產品脈絡：PRD §13 Roadmap Phase 3（Workflow Graph）、Phase 4 Pre-Execution Advisor 的前置。

## 1. 背景與範圍

Phase 3「知識圖譜」的子專案③。建立在已完成的 **Entity Graph 基礎**（子專案①，PR #12：MariaDB `GraphStore`、依 collection 隔離、隨 chunk 增量同步）之上。

**目標**：從 `Workflow`/`Runbook`/程序類型的知識中，抽出**有序步驟**與**前置條件**，存成結構化資料，提供機器可讀、已排序的查詢 —— 供 agent 與未來 Phase 4 Pre-Execution Advisor 回答「做某動作前該按什麼順序做、需要哪些前提」。

### 與現有檢索的區別
現有 Operations/Product 視圖的 `get_runbook`/`get_workflow` 是**向量檢索**，回傳整段原始文字 chunk；本子專案新增的查詢回傳**結構化、已排序的步驟 + 前置條件**，兩者互補並存。

### 範圍內
- 在每-chunk 知識萃取的單一 LLM 呼叫中，疊加抽取 `workflow` 結構（name / prerequisites / 有序 steps，每步可帶 precondition）。
- 以 MariaDB 兩張新表持久化，與向量同生命週期、依 collection 隔離。
- 提供 `get_workflow_steps` / `list_workflows` 的 MCP 工具與 API 端點。

### 範圍外
- 「平台建置流程本身的工作流」（追蹤/編排/視覺化）—— 那是**另一條獨立 initiative（B）**，本 spec 不含，之後另開 spec→plan。
- 前端工作流視覺化（屬子專案④）。

### 路線圖備註
本子專案是 PRD/TASKS.md 既有的 4.6/4.7，保留於路線圖。另有一條新 initiative（建置流程工作流：run 追蹤→視覺化→編排）將另行排程，不取代本子專案。

## 2. 關鍵決策（brainstorming 結論）

| 決策 | 選擇 | 理由 |
|------|------|------|
| 步驟抽取方式 | **併進現有單次 LLM 呼叫** | 與 Entity Graph 一致；一個 chunk 仍只一次呼叫、token 不加倍 |
| 跨 chunk 工作流 | **依名稱合併 + chunk 順序** | runbook 常跨多 chunk；以 `chunk_index` 提供正確跨 chunk 排序 |
| 步驟儲存 | **專用 `workflow_steps` / `workflow_prereqs` 表** | 步驟有序、帶文字/前置條件，不適合塞進 entity/edge 模型 |
| 前置條件 | **每步 precondition + 工作流級 prerequisites** | 最支援 Phase 4 Advisor 的「做 X 前該知道什麼」 |
| 儲存後端 | 沿用既有 **MariaDB `GraphStore`**（collection-scoped） | 與 Entity Graph 同庫、同生命週期 |

## 3. 資料模型

- **`KnowledgeUnit.workflow: dict`**（預設 `{}`，疊加、向後相容）。當 chunk 為 Workflow/Runbook/程序時，LLM 回傳：
  ```json
  {"name": "短標題",
   "prerequisites": ["開始整個工作流前需具備的條件", ...],
   "steps": [{"order": 1, "text": "步驟描述", "precondition": "此步前需成立的條件（可空）"}, ...]}
  ```
  其他類型留空 `{}`。
- **`Chunk.chunk_index: Optional[int]`**（文件內序號）。**不可**納入 `content_hash`/`id`（純位置 metadata，重排不得改變 chunk id 與增量同步）。由 pipeline 在切分後依文件順序 `enumerate` 賦值給該檔所有 chunk。
- **`graph/models.py` 新增** `WorkflowStep(workflow_name, chunk_id, chunk_index, step_order, text, precondition)` dataclass。

## 4. 萃取變更（`extract/knowledge.py`，併進現有呼叫）

- `_SYSTEM` prompt 增加一個條件式 `workflow` key（描述如 §3）。
- `_parse` 新增 `_parse_workflow(data)`：name 缺失則整段忽略；`order` 轉 int、無效則依出現序遞補；丟棄 `text` 為空的步驟；`prerequisites`/`precondition` 正規化為字串清單/字串；沿用既有防呆風格（不 fail-loud）。
- `KnowledgeUnit` 加 `workflow` 欄位。`is_empty()`/`metadata()` 不變（疊加）。
- `FakeExtractor`（conftest）延伸：當 kind 對應程序時回傳一個小工作流，供 pipeline/查詢測試。

## 5. 純邏輯建構（`graph/workflow.py`，新）

`build_workflow(knowledge: KnowledgeUnit, chunk_id: str, chunk_index: int) -> tuple[list[WorkflowStep], list[str], str]`
→ 回傳 `(steps, prerequisites, workflow_name)`。把 `knowledge.workflow` 轉成 `WorkflowStep` 清單（帶入 chunk_id/chunk_index）、prerequisites 清單、工作流名稱。空 workflow 回傳空結果。確定性、易測。

## 6. 儲存與生命週期（MariaDB，collection-scoped）

兩張新表，皆以 `chunk_id` 關聯以利精準 prune：

```
workflow_steps (
  collection, workflow_name, chunk_id, chunk_index, step_order, text, precondition,
  PRIMARY KEY (collection, workflow_name, chunk_id, step_order)
)
workflow_prereqs (
  collection, workflow_name, chunk_id, prerequisite,
  PRIMARY KEY (collection, workflow_name, chunk_id, prerequisite(191))
)
```

`GraphStore`（Protocol / MariaGraphStore / NullGraphStore / FakeGraphStore 全部鏡像）新增：
- `upsert_workflow(workflow_name, prerequisites, steps, chunk_id, chunk_index)`（或等價簽章）—— 寫入兩表，沿用 `self._collection` 與 `INSERT ... ON DUPLICATE KEY` / `INSERT IGNORE`。
- `get_workflow(name) -> dict | None`：合併同 collection 內同名工作流的所有 chunk，步驟依 `(chunk_index, step_order)` 排序、prerequisites 去重，回傳 `{"workflow_name", "prerequisites": [str], "steps": [{"order", "text", "precondition", "chunk_id"}]}`；查無回 `None`。
- `list_workflows(q=None, limit=50) -> list[dict]`：該 collection 內 distinct 工作流名稱（`q` 子字串過濾、limit clamp 1–500），回 `[{"name": ...}]`。
- `delete_for_chunks` / `delete_collection` 擴充，一併刪兩張 workflow 表。

`FakeGraphStore` 須與 MariaGraphStore 語義一致（collection 過濾、跨 chunk 合併排序、prereq 去重、回傳形狀）。

## 7. 查詢層（4.7）

純讀、無 LLM，走 `ctx.graph`。

### MCP 工具
- `get_workflow_steps(name)` → §6 `get_workflow` 的結構（加進 Operations 視圖；亦加進 Product 視圖）。
- `list_workflows(q?, limit=50)` → 工作流名稱清單（Operations 視圖）。

### API 端點（既有 envelope）
- `GET /api/graph/workflow/{name}` → `get_workflow` dict；查無回 404 envelope `{"error": "..."}`。
- `GET /api/graph/workflows?q=&limit=` → `{"items": [{"name": ...}]}`。

## 8. 接線

- `pipeline._ingest_file`：切分後 `enumerate` 賦值 `chunk.chunk_index`；在 `_write_graph(chunks)` 之後呼叫 `_write_workflow(chunks)`（對有 workflow 的 chunk 呼叫 `build_workflow` + `graph.upsert_workflow`）。
- prune/刪除路徑（`_ingest_file` stale、`_sync_deletions`、API `delete_item`/`delete_collection`）已呼叫 `graph.delete_for_chunks` / `delete_collection`；store 端擴充後自動涵蓋 workflow 表。
- `context.py` 無需改動（`graph` 已接線）。

## 9. 測試策略（保 166 綠燈基線、≥80% 覆蓋；`ODM_EXTRACT_KNOWLEDGE=true`）

- `_parse_workflow`、`build_workflow` → 純單元測試，零外部服務。
- `FakeGraphStore` workflow 方法 → 單元測試（collection 隔離、跨 chunk 合併排序、prereq 去重）。
- pipeline 測試：擷取跨 2 chunk 的同名 runbook（fixtures + FakeGraphStore），`get_workflow` 回傳合併且依 `(chunk_index, step_order)` 排序的步驟。
- 查詢 API（TestClient）/ MCP 工具測試：`get_workflow_steps` 結構、404、`list_workflows` 過濾/limit。
- MariaDB 整合測試（`@pytest.mark.integration`，無 `GRAPH_DB_HOST` 時 skip）：workflow 兩表 round-trip + collection 隔離 + delete。

## 10. 任務切分（每個 ≤Medium，注意檔案互斥）

1. `models.py`（`KnowledgeUnit.workflow`、`Chunk.chunk_index`）+ `graph/models.py`(`WorkflowStep`) + `extract/knowledge.py`(prompt/`_parse_workflow`) + `conftest.py`(FakeExtractor)。
2. `graph/workflow.py` builder（純邏輯）+ 單元測試。
3. `graph/store.py`：兩表 + `upsert_workflow`/`get_workflow`/`list_workflows` + delete 同步 + Protocol/Null/Fake 鏡像 + MariaDB 整合測試。
4. `pipeline.py`：賦值 `chunk_index` + `_write_workflow` + prune 同步 + pipeline 測試。
5. `api/app.py` 端點 + `views/__init__.py`/`server.py` MCP 工具 + 查詢測試。

> 熱點檔互斥：`extract/knowledge.py`（任務1）、`graph/store.py`（任務3）、`pipeline.py`（任務4）、`api/app.py`+`views`（任務5）—— 序列化或分批。

## 11. 成功定義

- 擷取一份（可跨 chunk 的）runbook 後，MariaDB `workflow_steps`/`workflow_prereqs` 有對應資料。
- `GET /api/graph/workflow/{name}` 與 MCP `get_workflow_steps` 回傳依 `(chunk_index, step_order)` 正確排序、合併、去重 prereq 的結構。
- 刪除/更新來源或 collection 後，工作流資料隨之 prune。
- 工作流資料依 collection 隔離（與 Entity Graph 一致）。
- 全套件測試綠燈（≥ 既有 166 + 新測試），新邏輯覆蓋 ≥80%；MariaDB 整合測試在有 env 時通過。

---

_建立：2026-06-19（superpowers:brainstorming）。前置：Entity Graph 基礎（PR #12）。_
