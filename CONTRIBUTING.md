# 參與 StoryLingo

感謝你願意檢視或改進 StoryLingo。這個專案優先保護讀者資料、作者內容與可回復的編輯流程；小而可驗證的變更比一次大型重構更容易被審查。

## 開始前

請依序閱讀：

1. [`AGENTS.md`](AGENTS.md)：永久工程規則與安全不變量。
2. [`PROJECT_MAP.md`](PROJECT_MAP.md)：功能與模組位置。
3. [`ARCHITECTURE.md`](ARCHITECTURE.md)：目前實際架構。
4. [`PRODUCT_ENGINEERING_AUDIT.md`](PRODUCT_ENGINEERING_AUDIT.md)：已完成項目與剩餘風險。

## 修改原則

- 先描述使用者問題、影響範圍與可驗證的 acceptance criteria。
- 不削弱 authentication、CSRF、authorization、章節可見性、SSRF／path validation、secret boundary 或 DB/job truth source。
- 前端流程以瀏覽器實際行為為準；Desktop 與 Mobile 都要檢查，包含 reload、direct URL、失敗與空狀態。
- 不把 `.env`、資料庫、上傳檔案、log、模型、測試結果或私人網域加入 commit。
- 行為、API 或部署方式改變時同步更新 `ARCHITECTURE.md`；重要模組位置改變時同步更新 `PROJECT_MAP.md`。

## 本機驗證

```powershell
python -m pytest backend/tests -q
npm run test:unit
npm run test:e2e
node --check frontend/app.js
node --check frontend/platform.js
python -m compileall backend
```

若只修改文件，請至少執行 Markdown／連結檢查與 `git diff --check`。若環境缺少 Playwright browser，請在 PR 描述中記錄未執行的項目與原因。

## Pull request

請使用 PR 模板，說明：

- 具體問題與改變後的使用者行為。
- 觸及的 Desktop／Mobile、登入狀態與失敗狀態。
- 執行過的測試與任何已知限制。
- 是否需要 migration、環境變數或部署步驟。

維護者會要求 scope 清楚、沒有機密、沒有把既有安全保護改回簡化版本，並且能從測試或瀏覽器證據重現結果。
