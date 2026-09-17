# AGENTS.md — AI 協作工作指引

給在此專案工作的 AI 代理（Codex、OpenCode 等）的簡短規則。

## 閱讀順序

所有任務（不分類型）一律依序閱讀：

1. `AGENTS.md`（本文件：開發規則）
2. `PROJECT_MAP.md`（功能在哪裡）
3. `ARCHITECTURE.md`（系統現在怎麼運作）

然後再讀任務涉及的實際 source files 與 tests。不要每次掃描整個 repo。

---

## 1. 專案語言
- 本專案文件與產品介面為**繁體中文**。
- 文件、留言、錯誤訊息維持繁體中文。
- 程式碼識別符沿用現狀（部分簡中變數／測試 fixture 名稱保留）。

## 2. 動工前必讀
- 依「閱讀順序」讀完三份核心文件後再動手。
- 改動某功能前，先讀該功能的現有實作與測試，不要只憑文件或命名猜測。
- 若文件、壓縮後記憶與 repository state 不一致，以 repository state 為準。
- Bug 先 root cause 再修改，不要用 workaround 掩蓋問題。

## 3. 不擅自重構
- 不進行未經當前任務明確授權的大規模重構。
- 不因「順便整理」而重寫 framework、替換技術棧或改動無關模組。
- 小步修改；改動範圍以任務目標為限。

## 4. 資料真源不可混淆
- 業務資料真源是 `backend/db.py`（SQLite）；`storage/books/{bid}` 是生成產物。
- 生成任務必須走 DB 佇列（`backend/jobs.py`），**不要**改回 FastAPI BackgroundTasks。
- 不得讓同一業務狀態同時由多套互不一致的欄位／table／cache 成為「真源」。
- V4 canonical 資料模型（`ai_providers`／`chapter_analyses`／`audio_generations`／`tts_providers`）以 `backend/v4_contracts.py` 與實際 schema 為準。

## 5. 已知易錯區域（見 ARCHITECTURE §6）
- `app.js` 與 `platform.js` 雙前端並存，改 UI 時兩邊都要檢查。
- 章節讀取／音訊 endpoint 已統一走 `book_service.can_view_chapter`（含 `publish_status` 檢查）；改動時務必保留此權限檢查，別退化成只查整書。
- 前端試聽一律走 `frontend/services/api.js`（NovelApi，自動帶 CSRF header），不要新增裸 fetch。
- 既有安全 invariant 不得因任何理由被移除或弱化。

## 6. 安全與機密
- 不把 `.env`、session key、AI key、TTS API key 提交或寫入文件／log。
- 不把敏感值放進前端。
- 修改認證／CSRF／CORS／rate-limit 時，務必同時確認 dev 與 production 兩種模式的行為。
- 不得為了讓測試通過而放寬 authorization、CSRF、chapter visibility、secret boundary、SSRF／path validation 等安全檢查。
- destructive DB operation 前必須確認目標是 development environment；若無法確認，停止執行並詢問。
- 不得恢復 legacy analyze/tts endpoints 或 legacy generation pipeline jobs（已移除，見 ARCHITECTURE §4.2）。

## 7. 測試
- 改動後需跑相關測試：
  - 後端：`python -m pytest backend/tests -q`
  - 前端單元：`npm run test:unit`
  - E2E：`npm run test:e2e`（需要本機 Python 環境，Playwright 會自動起 webServer）
  - 語法：`node --check frontend/app.js`、`node --check frontend/platform.js`、`python -m compileall backend`
- 新增／修改功能時，依現有測試風格補測試；測試 fixture 與隔離方式見 `backend/tests/conftest.py`。
- 跨層行為變更需補 integration regression，而不是只測新增 helper。
- 不得宣告任務完成，除非 completion criteria 與 regression tests 都成立。

## 8. 文件同步
- 專案主要 Markdown 只有三份：`AGENTS.md`（規則）、`PROJECT_MAP.md`（功能位置）、`ARCHITECTURE.md`（現況架構）。
- 改動行為、API、部署方式時，同步更新 `ARCHITECTURE.md`；新增／移動／刪除重要模組時更新 `PROJECT_MAP.md`。
- 不過度文件化：只有影響理解或維運的變更才寫文件。
- `ARCHITECTURE.md` 只描述「目前實際已經完成到哪裡」，不得提前把未實作設計寫成現況。
- 版本歷史交給 Git history，不要另建 Markdown 歷史文件。

## 9. 提交與 Git 工作規則
- 除非被明確要求，否則**不**執行 git commit／push／merge。
- commit 前先檢視 `git status` 與完整相關 `git diff`；只 stage 任務相關檔案。
- commit message 簡潔描述任務與主要結果；commit 不得包含機密。
- 不自動 push remote、不自動 merge 到 main／master、不自動 deploy。
- 大型多步驟任務可拆分為多個可獨立理解、測試與回退的 commit；不得把多個任務混成單一大型 commit。
- 若 commit 失敗或 hooks 拒絕，修正後建立新 commit，不 amend 已失敗的 commit。

## 10. 子代理（subagent）規則
- 使用子代理加速分析時，**子代理必須使用與主代理相同的模型**；本環境中子代理預設模型無法使用，會回傳空結果（這是先前 explore 子代理回空的原因）。
- 若子代理回傳空／失敗，改由主代理直接執行，不要重複空轉。
- 子代理產出需由主代理驗證後才寫入檔案。
- 子代理不得自行改變架構決策或跨越當前任務 scope。

## 11. 文件治理與 Context Recovery

### 文件治理
- 三份核心文件（AGENTS／PROJECT_MAP／ARCHITECTURE）是唯一授權的 current 文件。
- 已刪除的歷史文件（V4 plans、design docs、progress、reports、audits）不代表現況；不得因記憶中的舊文件內容重新推導架構。
- Git history 是唯一的版本歷史；不要重建 ARCHIVE／HISTORY／SUMMARY 等替代歷史文件。

### Context compaction / compression recovery
每次模型發生 context compaction、compression、summary recovery 或重新開始 session 後，不得只依賴壓縮後的對話記憶。

依序恢復工作狀態：

1. 重新閱讀 `AGENTS.md`
2. 重新閱讀 `PROJECT_MAP.md`
3. 重新閱讀 `ARCHITECTURE.md`
4. 執行並檢查 `git status`
5. 查看最近相關 `git log`
6. 檢查目前相關 `git diff`
7. 重新閱讀任務相關 source files 與 tests
8. 必要時重新執行最小 regression tests
9. 再繼續實作

原則：

> Never trust compressed conversation memory over repository state.

若壓縮摘要、模型記憶與 repository state 不一致，以 repository state 為準。

## 12. 永久 AI 協作規則

- 先讀已批准的 OpenSpec proposal/spec/design/tasks，再修改 application code；產品需求放 OpenSpec，永久工程規則放本文件，工作流程放 `docs/AI_WORKFLOW.md`。
- 只處理明確批准的 scope；recommendation、OPEN QUESTION、DECISION REQUIRED 不得偷偷變成實作。
- 不連 production、不 deploy、不修改 production data；不得使用 `git add .`；既有 unrelated dirty 必須保留並在交付前重新確認。
- 需求必須能追到 task、acceptance/test、release gate；測試通過不等於 requirement 完成。未經 requirement audit、adversarial review、適用的 browser flow、regression 與 git hygiene，不得標 DONE。
- 使用者可操作的前端流程必須 browser-first 驗證；前後端、權限、錯誤路徑、reload/persistence 與 backward compatibility 不得只測 happy path。
- 不得弱化 authentication、CSRF、authorization、chapter visibility、secret boundary、SSRF/path validation、audit integrity 或 DB/job truth source invariant。
- 失敗、漏測或小型 regression 必須先查 root cause、在 scope 內修正並重測；只有互斥產品決策、缺少必要 secrets、不可逆 production/destructive operation、明確衝突 invariant/spec 等情況才能以 `BLOCKED_BY_USER_DECISION` 停止，並列出 blocker、選項、推薦與後果。
- 主 Agent 是 integrator；subagent 只能在有限 scope 內工作，且需使用相同模型、不得自行改架構決策，產出必須由主 Agent 以 repository evidence 驗證。若 delegation 不可用，主 Agent 依序模擬 review roles。
- 除非明確要求，不自動 commit/push/merge；需提交時只 stage 任務相關檔案，先檢查完整 diff、secrets、placeholder、TODO 與 dirty scope。
