## 變更內容

<!-- 用一兩句描述問題與改變後的使用者行為。 -->

## 影響範圍

- [ ] 後端／API
- [ ] Desktop
- [ ] Mobile
- [ ] 登入／權限／資料安全
- [ ] 部署／環境變數
- [ ] 文件

## 驗證

- [ ] `python -m pytest backend/tests -q`
- [ ] `npm run test:unit`
- [ ] `npm run test:e2e`
- [ ] `node --check frontend/app.js`
- [ ] `node --check frontend/platform.js`
- [ ] `python -m compileall backend`
- [ ] 已檢查 reload、direct URL、失敗與空狀態（適用時）

## 安全與資料

- [ ] 沒有加入 `.env`、secret、真實帳號、作品資料、log 或 runtime data
- [ ] 沒有削弱既有 authentication、CSRF、authorization 或資料一致性保護
- [ ] 若有 migration／部署影響，已在描述中說明

## 截圖／補充

<!-- UI 變更請附 Desktop 與 Mobile 證據；不要放含私人資料的截圖。 -->
