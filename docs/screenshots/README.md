# 公開截圖

這些圖片是給 README 與設計討論使用的 reference evidence。它們由 Playwright 以合成 API 回應產生，沒有使用真實帳號、作品全文、讀者進度、外部 provider 或任何 secret。

## 圖片索引

| 檔案 | Viewport | 說明 |
| --- | --- | --- |
| `home-desktop.png` | Desktop 1440px | 訪客首頁：平台定位、作品入口、搜尋與分類 |
| `home-mobile.png` | Mobile 390px | 窄版首頁：單手導覽與底部主要區域 |
| `book-detail-desktop.png` | Desktop 1440px | 作品詳情：開始閱讀優先、模式說明與目錄 |
| `book-detail-mobile.png` | Mobile 390px | 窄版作品詳情：主要 CTA 與目錄順序 |
| `reader-mobile.png` | Mobile 390px | Reader：沉浸式閱讀、模式切換、章節導覽 |
| `author-workbench-desktop.png` | Desktop 1440px | 作者工作台：下一步提示與作品管理操作 |

## 更新原則

這批圖片在發布前由一次性的 Playwright 合成 fixture 產生，該 fixture 不混入產品 regression suite，也不隨快照提交。若要更新圖片，請用相同的合成回應與 Desktop／Mobile viewport 重新產生，並在提交前確認沒有把真實 API、登入 cookie、資料庫、作品全文或環境變數寫入圖片與測試輸出。
