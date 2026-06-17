# OpenDomainMCP 專案文件

本資料夾彙整 OpenDomainMCP 從專案起始至今的完整文件，作為產品、技術與開發進度的單一真實來源（single source of truth）。

## 文件索引

| 文件 | 內容 | 對象 |
|------|------|------|
| [PRD.md](./PRD.md) | 產品需求文件（Product Requirements）：願景、問題、使用者角色、範圍、功能規格、知識模型、MCP Views、成功指標、Roadmap | PM、Stakeholder |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 技術架構文件：三層架構、資料流、模組地圖、資料模型、擷取流程、檢索引擎、MCP、API、CLI、設定、測試策略 | 工程師、SA |
| [TASKS.md](./TASKS.md) | 開發任務清單：依 effort（low/medium）分類，已完成（✅）與未完成（⬜）任務全列，含內容與大致修改位置 | 工程團隊、PM |

## 既有 HTML 文件（保留）

- `guide.html` — 使用者操作指南（繁中）
- `reference.html` — 技術參考（繁中）
- `index.html` / `screenshots.html` — 入口與畫面截圖

> 上述 Markdown 文件為主文件，HTML 為早期面向使用者的說明，兩者並存。

## 版本與進度概要

| 階段 | 內容 | 狀態 |
|------|------|------|
| **Phase 1** | 文件/程式碼擷取、向量+混合檢索、單一 MCP、Web Console | ✅ 已完成 |
| **Phase 2** | 知識類型分類、Knowledge Review、多 MCP Views、Git/Zip/OpenAPI 擷取、Review/Builder/Simulator UI | ✅ 已完成（PR #6 已併入 main） |
| **Phase 3** | Workflow / Dependency / Entity Graph | ⬜ 未開始 |
| **Phase 4** | Agent Pre-Execution Advisor | ⬜ 未開始 |

詳細任務拆解與勾稽請見 [TASKS.md](./TASKS.md)。

_最後更新：2026-06-17_
