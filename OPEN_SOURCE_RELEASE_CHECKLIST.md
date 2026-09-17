# 公開到 GitHub 的發布檢查表

這份清單把目前的開發工作樹整理成可公開的 reference repository。它刻意把公開程式碼、內部稽核證據與個人部署環境分開，避免「能在本機跑」被誤解成「可直接承擔 production」。

## 已完成的公開前準備

- [x] 建立 README、貢獻規範、安全政策、行為準則與 GitHub templates。
- [x] README 明確標示 reference implementation、部署前置條件與目前限制。
- [x] 準備只使用合成內容的 Desktop／Mobile 截圖。
- [x] 檢查目前已追蹤檔案與 reachable Git history：沒有偵測到常見 `nvapi-`、`sk-`、雲端 access token、GitHub token 或私鑰標記。
- [x] 確認本機 `.env` 未被 Git 追蹤；它含有本機設定，絕不能複製到公開包。
- [x] 公開包規則排除 `data/`、`storage/`、`uploads/`、`logs/`、`backups/`、`node_modules/`、測試結果、模型與 Cloudflare 本機資料。

## 發布前仍需由 owner 決定

- [x] 選定 Apache-2.0 並提交 `LICENSE`；若未來改採 AGPL-3.0，應在公開前另行替換並重新檢查第三方依賴。
- [x] 確認 GitHub repository owner、名稱、可見性與預設分支：`zzxx5124/storylingo`、public、`main`。
- [x] 已決定不保留原始 Git 歷史；公開 repository 使用乾淨的 orphan history，避免帶入私人網域、工作路徑或內部稽核紀錄。
- [x] 加入 Dependabot 設定；公開 repository 已由 GitHub 啟用 Secret scanning 與 push protection。
- [ ] 若帳號方案支援，從 GitHub Settings → Code security 開啟 Dependabot security updates、automated security fixes 與 Private vulnerability reporting。
- [ ] 依 owner 的維護習慣設定 GitHub branch protection 與 Private vulnerability reporting。
- [ ] 決定 Issues／Discussions 的維護範圍與聯絡方式。

## 建議發布流程

1. 在獨立的公開暫存目錄建立乾淨快照，只複製 source、tests、migrations、deploy 範例、公開文件與 `docs/screenshots`。
2. 排除 `.env`、runtime data、上傳檔案、log、測試結果、個人路徑、私人網域與外部服務的真實識別資訊。
3. 對暫存目錄再次執行 secret scan、`git diff --check`、單元／後端測試與必要的 E2E。
4. 加入 owner 確認的 `LICENSE`，初始化新的 Git 歷史，建立通過 CI 的參考標籤（目前為 `v0.1.2-reference`）。
5. 推送到空白 GitHub repository，先以 private／unlisted 方式檢查檔案、Actions、截圖與連結，再切換 public。
6. 啟用 branch protection、Dependabot、Secret scanning，並在 README 寫明第一個可公開版本與已知限制。

## 不應放入公開 repository 的內容

- 任何 `.env` 或非佔位符 secret。
- SQLite／Redis 資料、讀者進度、作者草稿、上傳檔案與生成音訊。
- 個人電腦路徑、內網位址、Cloudflare tunnel identity、私人網域或真實 mailbox。
- 只用於內部 qualification 的 production／staging 證據與第三方服務截圖。
- 未經同意的真實作品全文、封面、帳號資料或外部模型檔案。

公開後仍要把 Issue、PR、截圖與 CI artifact 當成公開資料處理；撤回檔案不會自動清除 Git 歷史或快取。
