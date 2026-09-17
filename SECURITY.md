# 安全政策

StoryLingo 是參考型開源專案，公開 repository 不應包含任何真實帳號、作品資料、session、AI key、TTS key、OAuth secret、私鑰或部署環境的可識別資訊。

## 回報方式

請不要用公開 Issue 回報尚未修補的漏洞。若 GitHub repository 已啟用 Private vulnerability reporting，請使用該入口；否則先聯絡 repository owner，並只提供重現步驟、影響範圍與不含秘密的最小證據。請不要把 secret 值貼在訊息、截圖、log 或測試 fixture 中。

收到回報後，維護者會先確認影響範圍、建立修補分支、輪替可能暴露的憑證，再安排修補與公告。這個 repository 尚未承諾固定的回應時限；請在標題標記 `[SECURITY]`，避免把細節公開。

## 金鑰與環境變數

- `.env` 只存在於本機或 secret manager；提交前用 `git diff --cached` 與 secret scanner 檢查。
- `AI_API_KEY`、`OPENAI_API_KEY`、`TTS_SECRET_KEY`、OAuth secret、管理員密碼都只能由後端讀取。
- 一旦懷疑外洩，先撤銷／輪替，再清理 log、工件與公開 Git 歷史；刪除檔案不代表 Git 歷史已安全。
- `.env.example` 的值只能是空白、佔位符或本機 localhost 範例。

## 部署基線

正式環境應使用 HTTPS、限制 `PUBLIC_ORIGINS`、隔離資料與上傳目錄、限制管理員帳號、定期備份並驗證還原。外部 AI／TTS provider 的權限與配額應設定在 provider 端，不能依賴前端隱藏按鈕。

## 支援版本

目前只承諾檢視預設分支上的最新版本。這個專案的版本並非長期維護發行版；部署前請自行執行完整回歸與安全檢查。
