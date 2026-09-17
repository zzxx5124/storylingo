# 產品與工程重審 — 2026-09-09

本次為使用者授權的產品、UX/UI、功能與工程重審。範圍以目前 Platform repository 為準，保留原有未提交修改；不部署、不修改既有 data/storage、不 commit/push。實際操作使用獨立暫存資料庫與本機 8017/8018 服務，worker 關閉，沒有呼叫付費 AI/TTS。

## A. 主要問題與修正

| 優先級 | 已確認問題 | 本次結果 |
|---|---|---|
| P1 | 原文仍在載入時，儲存按鈕可把 loading 文字當正文送出；讀取失敗後也能編輯 | 載入與錯誤狀態禁止儲存，提供重試 |
| P1 | 快速新增／儲存沒有鎖定，可能送出多個 request | 編輯 session 統一控制 pending；輸入與提交鎖定 |
| P1 | 章節讀取晚回應會覆寫後來開啟的章節 | 綁定 Book／Account／route／session，過期回應不寫入 |
| P1 | 多分頁修改默默覆寫先前內容 | 新版編輯器帶 stable chapter key、hash、title 前置條件；transaction 檢查，409 保留輸入 |
| P1 | 關閉、刷新會丟失未儲存長文；release banner 沒有真正的 dirty 標記可讀 | 分頁內暫存、dirty 標記、離開提示、重開恢復；成功儲存不刪除較新的草稿 |
| P1 | 版本還原用不含原文的 Book summary 回填，可能清空編輯欄 | 還原後重新取得 chapter 正文，失敗時禁止後續覆寫 |
| P1 | 「管理章節」href 指向書架，click handler 又開作品，兩套路徑競爭 | canonical detail URL 單一路徑；reload、直接 URL 都一致 |
| P1 | Book load／poll／refresh 與公開作品回應會在離開後寫回舊頁 | 增加 route／Account／Book 守衛；公開留言完成後也不拉回已離開的作品 |
| P1 | 作品 metadata 編輯在載入、重複提交、刷新與多分頁衝突時缺少明確保護 | metadata 編輯沿用單次 session；載入失敗不可儲存，草稿可恢復，提交 pending 鎖定，409 保留輸入；PUT 以 metadata hash（並相容 updated timestamp）做 transaction optimistic lock |
| P1 | 個人設定頁只有單次按鈕鎖定，沒有載入 session、草稿恢復或多分頁衝突保護 | 個人設定以 account/route 綁定 session；載入失敗不可提交，可重試、放棄草稿、重新整理恢復；PUT 以 profile revision transaction 檢查，409 保留輸入 |
| P2 | 收藏只查私人清單第一頁，較早收藏的作品顯示未收藏；連按狀態互相翻轉 | Book DTO 直接查 current Account 關係；收藏／追蹤／留言防重複提交 |
| P2 | 排行快速切換顯示較舊結果、錯誤重試連到同 hash 無作用；空目錄仍引導閱讀第 0 章 | 排行只接受最後一次請求、原地重試；無章節顯示可收藏／追蹤的空狀態 |

P1 編輯問題有覆寫資料的風險；本次沒有證據宣稱既有使用者已發生資料遺失，也未新確認安全權限 P0。

## A-UX. 使用者角度 UI／UX 重審與本輪取捨

本輪以本機實際操作的首頁、搜尋、作品詳情、文字閱讀器、書架、作者工作台、作品建立／章節管理、設定與 390×844 行動版為準；另以 Playwright Desktop/Mobile 流程驗證 reload、空狀態、錯誤與直接 URL。依對主要任務的阻塞程度排序：

| 優先級 | 使用者問題 | 判斷與處理 |
|---|---|---|
| UX-1 | 作者工作台的作品卡在手機上把作品資訊壓成窄直欄，送審、編輯、管理章節、轉移、統計、刪除同時擠在一列，下一步不明顯 | 高信心、已實作：改成手機 grid；保留送審／管理章節／編輯／轉移等主要入口，低頻申請、統計與刪除收進「更多操作」；卡片新增 derived workflow 的「下一步」提示 |
| UX-2 | 長篇作品詳情把全部章節直接堆在頁面，讀者尚未選擇閱讀方式就要穿越數十列；手機尤其容易失去上下文 | 高信心、已實作：先顯示前 12 章，使用可鍵盤操作的原生展開區查看其餘章節；所有章節仍保留直接閱讀連結 |
| UX-3 | 首頁在 hero、搜尋、聆聽入口、分類、熱門、最新、完結之間平行堆疊；資料較少時出現大片留白，第一次使用者不容易判斷應先找書、開始閱讀或探索有聲內容 | 已完成：簡短說明後直接選書；登入回訪者先繼續閱讀。最新／熱門／完結共用最多六本的選書區；搜尋／分類為 Desktop 旁欄、Mobile 後段輔助入口；少量作品不重複分區，空與錯誤狀態可重試 |
| UX-4 | 作品詳情同時在 hero 提供「開始閱讀」，又以「你想怎麼享受這本書？」再次提供純文字／聽書／學習入口，決策被拆成兩個區塊 | 已於 UX-6 完成：主要開始／繼續閱讀入口集中，模式切換移入 Reader；無章節／部分有聲狀態已驗證 |
| UX-5 | 行動版導覽要先開啟選單，再展開「工作區」才能到達作者作品／申請紀錄；角色較多時入口層級不一致 | 已完成：Mobile 主入口直接切換、工作區單層、帳號區收斂、位置／父層與 active state 由 URL 決定；Desktop disclosure 保留，Reader 使用獨立導覽。新增 16 項與全套 370 E2E 通過 |
| UX-6 | 作品詳情的開始閱讀、閱讀模式與學習入口分散；已有進度仍只看到「開始閱讀」，Reader 內又沒有直接切換純文字／聽書的入口 | 高信心、已實作：詳情頁以「開始閱讀／繼續閱讀」單一主 CTA 決策，保留「從第一章開始」與可用的「聽書跟讀」替代入口；模式切換移入 Reader，學習工具只在進入 Reader 後出現，章節導覽與音訊控制在 Mobile 分層 |

本輪已依序收斂作者工作台、長篇目錄、作品詳情／Reader、首頁，最後處理 UX-5 全域 Mobile 導覽。後續停止新增大型 UI surface，維持已完成頁面的內容組織。

## B–D. 實作與重要設計決策

- **長文編輯工作區**：由 560px 小型彈窗改成寬版編輯器；手機使用整個可視高度。正文可獨立捲動，底部儲存／字數／回饋固定，版本歷史預設收起。目的在於讓長文編輯有足夠空間，同時不需找尋儲存按鈕。
- **完整可恢復狀態**：清楚呈現載入、失敗重試、待儲存、暫存、儲存中、衝突與還原。錯誤訊息留在編輯器，不只依賴消失的 toast。提供捨棄暫存、重新讀取目前版本的明確動作。
- **正文忠實保存**：不再 trim 作者正文首尾空白；只用 trim 檢查是否空白。
- **可靠目的地**：管理操作使用真正的 detail URL，頁面狀態由路由驅動。非同步結果不能決定使用者現在應在哪一頁。
- **公開操作完整性**：收藏以 relationship 真源判斷，pending 時鎖定操作；公開 projection 只補目前帳號的收藏／追蹤，不暴露管理資料。沒有章節就不提供假的開始閱讀入口；首末章的返回連結改標「返回目錄／返回作品」，不再錯稱上一章／下一章。
- 沒有建立第二套後端草稿、發布、generation 或身份模型。新增 API 欄位採相容方式，不移除既有 caller。

## E. 根因與工程邊界

### 全域 Mobile 導覽 surface

- 根因：跨區入口都依賴選單，作者區還有第二層；登入後名稱、通知、設定與登出擠在頂部，讀者／作者缺少穩定的位置提示。
- 首頁／搜尋／我的書架固定於底部；既有角色允許時顯示我的作品。搜尋是新訪客找書的直接入口，書架是回訪的目的地，作者日常管理不再需要兩次展開。排行／有聲書與申請／後台保留單層選單，沒有把每個功能塞進底部列。
- 頂部保留通知／個人設定，帳號名稱／登出移入選單；首頁命名統一。Desktop 恢復原有 workspace disclosure。重用相同 DOM 與事件，只讀取既有 app.js role-hidden 狀態，不以 CSS 或新資料欄位取代授權。
- 章節管理返回我的作品；其他一般區域可回首頁，direct URL／refresh 的父層可預期。Escape 回到選單按鈕，route／breakpoint／外部點擊關閉。Mobile Reader 不顯示全域列，保留既有作品／章節／目錄導覽。
- 審查補修：Reader 隱藏頁首會連同訪客書籤觸發的登入 dialog 隱藏。現在僅在 Mobile Reader 將同一 dialog 移到 body，離開或切回桌機後歸位，不另建登入流程。
- 實際暫存 DB 演練：320px 訪客首頁／登入；讀者首頁→書架→續讀→Reader refresh→作品頁；作者我的作品→章節管理 refresh→返回我的作品。390px／1440px breakpoint、所有角色、logout、錯誤返回與 dialog 由 E2E 補足。
- 保留限制：Reader 載入失敗目前提供回首頁，不提供回該作品；這是原有安全返回行為，未重畫 Reader 錯誤頁。真機軟鍵盤／瀏海 safe-area 仍需 release gate，瀏覽器窄版不等於真機驗證。

### 首頁資訊架構 surface

- 根因：首屏把品牌、搜尋、有聲與分類當同等主角，作品排在後面；資料少時同一批作品又被不同書牆分割，資料多時頁面持續向下堆疊。
- 新訪客／登入無紀錄：以「免費小說閱讀」和一個簡短說明解釋平台，接著顯示作品標題、簡介與「查看作品」。有聲／學習是閱讀延伸，不另外要求選模式。
- 登入有紀錄：最近一本優先，顯示章節、模式與百分比並直接繼續；最多兩本其他近期作品以文字入口補充。進度來源是 current Account 的 DB，章節已隱藏／不存在時只回到作品目錄，不產生無效續讀連結。訪客首頁仍以探索為主；既有作品詳情的訪客進度不變。
- 最新／熱門／完結共用一個最多六本的清單；`collection` URL 保留切換、Back/Forward 與 refresh。每份清單去重，完整內容由搜尋／排行展開。Desktop 搜尋與前六個分類在旁欄，Mobile 作品優先，上方「探索全部作品」仍可直接搜尋。既有 banners 保留為最多五個站內精選連結；不再使用大型自動輪播。
- 實際操作：先操作舊首頁 Desktop/Mobile 搜尋，再於獨立暫存 DB 操作新版登入 → 繼續閱讀 → Reader → 返回作品 → 首頁 → refresh。另檢查 320／390／1440px，第一本作品在手機首屏可見，無橫向溢位。少量／大量／空／錯誤／慢載入由 browser fixtures 補足。
- 限制：清單沿用現有最新／排行榜／完結來源，不宣稱個人化推薦。這次未改 production 或既有使用者資料。


核心根因是多個 async callback 共用可變的 `state.book`、章節 seq 與全域 DOM，且缺少讀取版本條件；按鈕可用性與 load/save lifecycle 沒有共同管理。此次用單次編輯 session、request snapshot 與既有 DB 交易修正，沒有換框架。

`can_view_chapter`、owner/role、CSRF、provider secret、DB generation queue 與審核流程均保留。原始的 service/api wrapper 繼續作為前端 mutation 唯一入口。

## F. 刻意保留

- 保留公開平台現有紙本閱讀視覺；首頁改動集中於閱讀順序、內容組織與下一步，不改全站視覺系統。
- 保留五角色、發布審核、有聲核准及 provider 管理：這些是已定義的產品規則，不因 UI 簡化而取消權限。
- 保留管理播放器／公開 Reader 的獨立控制器，未合併有高度風險的播放狀態。
- 不改正式資料、既有音訊與分析產物、schema 或部署；真實 AI/TTS 供應商測試另列驗證限制。
- 保留既有 unrelated dirty：AGENTS.md、author-generate-single.spec.js、style.css 手機章節列修正、book_service.py 空白修正，以及開始時存在的未追蹤文件／skill／`=`。

## G. 驗證證據

| 驗證 | 結果 |
|---|---|
| 後端全套 `python -m pytest backend/tests -q` | 715 passed、1 skipped；本次 AUD-17 完整回歸通過（579.10s） |
| 作品 metadata 編輯新增驗證 | backend `test_product_audit.py`：snapshot 409、hash 型別錯誤；product-audit E2E：Desktop/Mobile 載入失敗、重複提交、reload 草稿、衝突保留、離開後晚回應 |
| 個人設定編輯新增驗證 | backend `test_identity_profile.py`：profile revision 409、格式錯誤；identity-profile E2E：Desktop/Mobile 載入失敗、重複提交、reload 草稿、衝突保留、離開後晚回應 |
| 最終聚焦後端與公開可見性／分頁／平台測試 | 30 passed（含新增 8 項）；驗證 409、輸入、跨頁收藏，以及 public header 不洩漏 owner 的隱藏章節 |
| 版本歷史相關測試（與初版聚焦測試合跑） | 13 passed |
| 前端單元 `npm run test:unit` | 17 files、94 tests passed；本次重新執行通過 |
| 首頁 API 權限與進度 | `test_home_resume_is_account_scoped_and_requires_public_chapter`：真實 DB 進度、跨帳號隔離、owner 隱藏章節、不存在章節、未公開作品均通過 |
| 首頁 Browser 狀態 | `home-architecture.spec.js` Desktop/Mobile 14 項涵蓋訪客、登入無紀錄／有紀錄、失效紀錄、少量／大量、empty/error/retry、loading／晚回應、鍵盤、Back/Forward／refresh；另以暫存 DB 真實登入與 Reader 流程驗證 |
| E2E desktop/mobile 全套 | 394 passed（3.6m）；含排行 tabs 新增 2 項、私人書架 retry 4 項、搜尋／分類 retry 6 項、有聲書 retry 6 項、排行連結 6 項、Mobile 導覽 16 項、首頁 14 項與既有編輯保護、作者／目錄、開始／繼續閱讀。最終完整結果見本機 `logs/luna-aud17-regression-e2e.txt` |
| 私人書架錯誤與分頁 | `platform-pagination.spec.js` 新增 2 個情境、Desktop/Mobile 4/4；驗證收藏與歷史各自保留 page、重試 loading、reload/direct URL，以及離開後晚失敗不覆蓋首頁 |
| 排行 tabs 鍵盤與 ARIA | `ranking-links.spec.js` 新增 1 個情境、Desktop/Mobile 2/2；驗證 roving tabindex、左右／Home／End、Enter／Space／點擊手動啟用、tabpanel 關聯、320px 與既有排行 retry/競態 |
| Mobile 導覽驗證 | `mobile-navigation.spec.js` 16/16；角色、首頁／搜尋／書架／作者父層、direct URL／refresh、320／390／1440px 切換、Escape、logout、Reader 登入與 403 返回；實際 reader/author journey 使用暫存 DB |
| 本輪 UI／UX 流程驗證 | product-audit Desktop/Mobile 新增作者卡片資訊層級、長篇目錄漸進揭露、開始／繼續 CTA、Reader 模式切換、進度／重新整理與窄版無橫向溢位測試 8/8 |
| 語法與 Python compile | app.js、platform.js、compileall backend 通過 |
| build／typecheck／lint | package.json 無對應 script；原生 JS 靜態服務，無 bundler production build。未把語法檢查冒稱為 lint 或 typecheck |
| Browser 實際操作 | 本機登入、建立作品／第一章、編輯、儲存、reload、真實雙分頁 409、公開作品詳情→開始／繼續→純文字／聽書、章節返回與 390×844 手機布局已操作 |

既有全套 E2E 包含首頁、搜尋、登入／OAuth fake flow、作者申請／建立、單／多語者、發布與審核、Reader、書架、通知、權限、empty/error、direct URL 與 mobile。多數使用 mocked API；它們不能證明正式 mail、Google OAuth、外部 AI/TTS、真機背景播放或真實供應商延遲。此輪未宣稱 production readiness。

實際畫面：[桌面編輯器](logs/product-audit-editor-desktop.png)、[手機編輯器](logs/product-audit-editor-mobile.png)。本輪 UX 操作截圖為本機暫存產物，不含既有使用者資料。

前兩輪故障證據：原本 prologue-setting 流程暴露管理連結競爭，修正產品根因；新寫的 mobile 慢回應測試曾未展開手機導覽，修正測試操作方式，沒有放寬斷言。

首頁回歸曾發現書卡無障礙名稱遺漏，已補回；原先多書牆與舊靜態版本的斷言已更新為單一清單切換及新版資源契約。首頁 AUD-11 已完成驗收。本次 Mobile 測試更新舊「必須看到巢狀 details」的斷言為角色與單層入口驗證，未放寬權限；新版靜態 cache identity 隨 shell 同步更新。

Mobile 完整 E2E 首次有 4 項測試失敗：作者介紹定位撞到新的位置標示、手機登出未先開選單、轉移操作測試檢查導覽後未關閉選單。改為精確定位既有作者簡介、按新版流程開啟／關閉選單，原有角色、資料與結果斷言保留；相關 46/46 通過後，重跑完整 370/370 通過。最終 715 backend／94 unit／370 E2E、語法、compile 與 diff check 全部通過。AUD-12 完成，本輪停止新增大型 UI surface。

## H–I. 後續順序與不要改回的決策

後續較便宜模型依序處理：

1. **編輯保護階段完成**：作品 metadata 與個人設定均已完成 session、草稿、pending、409 與晚回應保護。本階段停止繼續擴張同類型編輯保護，後續回到產品 UI／UX 與主要流程設計。
2. **P1/P2：穩定章節 identity 延伸**至排序／刪除／歷史還原與舊 caller。新 PUT 前置條件可省略以相容，因此不能宣稱所有 caller 都已有多分頁保護。現有歷史仍以 book/seq 查詢；進一步改動應先獨立設計與測試，不直接改 schema。
3. **P2：作者工作台資訊層級**：將管理員的生成控制與作者日常內容管理繼續分清；目前不同角色已分權，但共用大檔案仍容易混淆文案。優先處理一個實際工作流程，不一次換皮全站。
4. **P2：公開頁資訊架構**：首頁、作品詳情／Reader 與全域 Mobile 導覽已實作。維持單一有界選書區、回訪優先、主要閱讀 CTA 與 Reader 沉浸式導覽；停止新增大型 redesign。
5. **P2：公開頁次要問題**：排行獨立連結、原地重試與 tabs 鍵盤／ARIA 行為均已完成；停止新增大型 UI surface。後續若有變更，仍需補鍵盤與窄版測試。
6. **P2：工程可維護性**：按有測試的邊界逐步拆出 editor/workspace；不要只因 app.js 大就重寫 router、引入框架或建立第二套 state。
7. **獨立 release gate**：有適當憑證後測真實 mail/OAuth/TTS、真機鍵盤與背景播放、正式環境部署與還原。不要用 mocked E2E 的綠燈替代。

不可隨意改回：單一 detail URL、late-response/principal guard、載入失敗不可儲存、409 不覆寫、原文不 trim、草稿保留原始條件、舊成功回應不可刪新草稿、收藏 entity relationship 判定，以及 DB／權限／審核／generation invariant。

暫存限制：只在本機同分頁使用 sessionStorage；關閉分頁、瀏覽器限制儲存或清除資料後不保證恢復，不是跨裝置／永久備份。若產品需要長期自動儲存草稿，應獨立定義權限、保存期限、版本衝突與配額，不能偷偷將此暫存升格為後端真源。

## J. 本輪最終產品決策與低成本接手邊界

1. 編輯以資料安全優先：session／principal fencing、409、原始草稿條件、載入失敗不可儲存與固定儲存列保留；不擴張另一套編輯系統。
2. 作者工作台以作品、下一步與管理章節為主；低頻操作收進更多操作，Mobile 保持可閱讀的卡片資訊層級。
3. 作品詳情以開始／繼續閱讀為主決策；第一章與聽書保留適當替代入口，模式／學習在 Reader 適時呈現，長目錄漸進展開。
4. 首頁先回答平台價值與下一步，回訪先續讀；單一有界選書區取代平行書牆，少量內容也成立。
5. Mobile 跨區入口直接可見，工作區單層，位置與父層依 URL；Reader 保持沉浸，Desktop 使用原有 disclosure。名稱與權限不因 layout 分裂。

本次依使用者成本要求，由主代理負責設計／整合／實際操作，Luna 僅負責新導覽 E2E 與有界 review；先聚焦測試再完整回歸，發現 Reader dialog 問題時中止尚未完成的 E2E，修正後重新跑最終全套。沒有為節省成本省略 required gate。

本輪已界定的獨立小單（均已完成；不再視為大型 redesign 授權）：

| 順序 | 小單 | 完成條件 |
|---|---|---|
| 1 | 排行列巢狀互動核對與單點修正（已完成） | article 內獨立原生連結，保持資料順序與 request fencing；鍵盤、滑鼠、320px、原生另開分頁／direct URL／reload 新增 6/6，全套 gate 通過 |
| 2 | 其餘同 hash 重試入口 | 有聲書、搜尋／分類與私人書架均已完成；保留目前 query/page 與 route/principal fencing |
| 3 | 已完成 surface 的驗收文件與實際測試名稱核對 | 只修證據對應與過期描述，不宣稱未測的真機／正式供應商已完成 |

穩定章節 identity、排序／刪除／歷史還原、跨頁 editor state 拆分仍屬高風險工程項目，應先由主代理定義資料契約與回歸矩陣，不能當作 Luna 的簡單清理工作。正式 mail/OAuth/TTS／備份還原仍走獨立 release gate。

### 小單 AUD-13：排行榜獨立連結

使用者於 2026-09-10 授權評估下一步並繼續。選擇已列出的排行互動問題，沒有開始大型 surface。實際 Browser 確認原本整列是無 href 的 button；內含作者連結，只靠 propagation handler 避免誤入作品。改為 article 包獨立作品／作者連結，保留排名、封面、標題與作者資訊；無公開作者仍為文字，長文字可換行。作品不再綁 data-book，支援原生鍵盤與分頁行為。排行榜排序、分類切換、request fencing、error/retry 及其餘頁面邏輯不變。

主代理完成來源核對、實際 1440／320px 操作、實作與整合；Luna 僅新增 `ranking-links.spec.js`，經主代理 review 補實際 Control-click 新分頁驗證。靜態資源版本同步更新。聚焦排行、既有編輯／Reader 保護與 cache 測試 48/48 通過；最終 715 backend（1 skipped）／94 unit／376 E2E、syntax／compile／diff check 全部通過。AUD-13 已完成，本次停止在這一項小單，不擴張大型 redesign。

後續需要使用者配合的驗證：準備一部 iPhone／Android 與非正式環境測試帳號，依序檢查登入→找書→閱讀→鎖屏／背景播放→返回，並在章節編輯時開啟軟鍵盤確認儲存列可用。外部 mail／Google OAuth／AI／TTS 應使用測試環境已配置的服務執行實際流程，憑證只放安全設定，不貼進對話；備份還原先在隔離副本演練，不能以正式資料試跑。以上仍屬待驗證，不是 mocked E2E 已通過的項目。

### 小單 AUD-14 與 Luna 交接

實際 Mobile Browser 重現：無 query 有聲書收到 503 後點「重新載入」，API 次數仍為 1，URL 沒改，因此沒有重新請求。原 link 在有 query 時又會跳回無篩選首頁。現改 button 呼叫目前 hash 的 renderAudiobooks，沿用 route/query fencing，保留 query 與 loading；沒有改搜尋或私人書架來擴張本項。新增 Desktop/Mobile 六項測試覆蓋無 query／複合篩選與 page=2／refresh／離開後慢重試；聚焦有聲書與 cache 18/18、unit 94/94、完整 715 backend（1 skipped）／382 E2E、syntax／compile／diff check 通過。AUD-14 已完成。

已依使用者要求建立 `docs/LUNA_HANDOFF.md`，包含接手順序、三個有界小單、具體 source／測試、不可改回的產品決策、高風險工作排除及真機／外部服務／備份還原 gate。平台目前是「主要流程已重審且有回歸基線」，不是「全面完善」；穩定章節 identity 的全 caller／歷史還原範圍與正式 release gate 仍未完成。

### 小單 AUD-15：搜尋／分類原地重試

搜尋／分類頁原本以固定 `#/search` link 重試：相同 hash 時不保證發請求，且分類與複合條件會被清除。現在採與 AUD-14 相同的局部模式，按下 retry 用當前 hash、category 與 route token 重新 render，新的 query generation 仍會阻擋過期回應。空搜尋的「清除篩選」維持原語意，沒有被改成 retry。

新增 Desktop/Mobile 六項驗證：一般搜尋與分類頁各保留 query／page=2、重試 loading、reload/direct URL，以及離開後慢回應不覆蓋首頁。完整 E2E 首輪暴露既有公開搜尋測試仍將 retry 視為固定首頁 link；更新為檢查無 server detail 洩漏、button 可重試並實際發第二次 request，沒有移除安全斷言。聚焦 22/22、unit 94/94、完整 715 backend（1 skipped）／388 E2E、syntax／compile／diff check 通過。AUD-15 已完成。

### 小單 AUD-16：私人書架保留頁碼的原地重試

實際重現私人書架依賴請求失敗後，原 catch 會顯示固定 `#/shelf` 連結；同 hash 點擊不保證重新請求，且收藏／歷史 page query 會遺失。實作改為沿用既有 route、query、principal snapshot：錯誤只在目前 snapshot 顯示，retry 以原 query 重新 render 並先回到 loading。修正同時補上歷史 endpoint 分頁 query 的根因：沒有既有 query 時必須使用 `?page=...`，不能形成錯誤的 `/api/me/history&page=...`。

新增 Desktop/Mobile 4 項測試，覆蓋收藏與歷史各自保留 page=2/3、retry loading、第二次請求成功、reload/direct URL，以及 retry 後離開頁面時晚失敗不覆蓋首頁。聚焦 `platform-pagination.spec.js` 18/18、unit 94/94、完整 backend 715 passed／1 skipped（444.96s）、完整 E2E 392 passed（3.4m）、syntax／compile／diff check 通過。AUD-16 已完成。

### 小單 AUD-17：排行榜 tabs 鍵盤與 ARIA 行為

實際 Browser 核對發現排行分類原本雖有 role=tab 與 aria-selected，卻沒有 roving tabindex、方向鍵／Home／End 焦點管理，也沒有 tabpanel 關聯；鍵盤使用者必須逐一 Tab，且方向鍵無法在分類間快速移動。實作保留四種類別、手動載入、latest-request-wins、error/retry 與 AUD-13 的作品／作者原生連結，只補穩定 tab id、aria-controls／aria-labelledby、tabpanel、roving tabindex 與手動啟用。

新增 Desktop/Mobile 2 項測試（`ranking-links.spec.js` 聚焦 10/10）：驗證一般 Tab 入口、左右方向鍵循環、Home／End、Enter／Space／點擊各只載入一次，未啟用前不改分類，並保留 320px 無橫向溢位與既有排行競態／重試。同步將 `platform.js` 更新為 v16、Service Worker 更新為 v27，HTML／SEO／cache contract 一致。完整 unit 94/94、backend 715 passed／1 skipped（579.10s）、E2E 394 passed（3.6m）、syntax／compile／diff check 全部通過。AUD-17 已完成，本輪停止新增大型 UI surface。

### 小單 AUD-18：公開 GitHub 快照準備

使用者希望把平台開源供他人參考。這不是新增產品 surface，而是獨立的 release／維運工作：建立公開說明、貢獻與安全政策、Issue／PR 模板、CI 與合成 UI 截圖，並把內部稽核證據與個人部署環境隔離。

- 已新增 `README.md`、`CONTRIBUTING.md`、`SECURITY.md`、`CODE_OF_CONDUCT.md`、`OPEN_SOURCE_RELEASE_CHECKLIST.md`、`.gitattributes` 與 `.github/` templates/workflow。
- 已產生 `docs/screenshots/` 的首頁、作品詳情、Reader、作者工作台 Desktop／Mobile 合成截圖；截圖不使用真實帳號、作品資料或 secrets。
- 已掃描目前已追蹤檔案與 reachable Git history 的常見 NVIDIA/OpenAI/AWS/GitHub token、私鑰與 secret assignment pattern：未偵測到實際值。忽略的本機 `.env` 仍含本機設定，不能加入公開快照。
- 已建立 `<workspace-root>\StoryLingo-open-source-20260917` 的乾淨公開快照：排除 Git history、`.env`、資料庫、runtime data、上傳檔案、log、模型與私人 qualification evidence，並將私人網域／本機路徑泛化為 example 佔位值。
- 已建立並推送至公開 repository [`zzxx5124/storylingo`](https://github.com/zzxx5124/storylingo)，預設分支為 `main`，採用 Apache-2.0；目前通過 CI 的參考版本標籤為 `v0.1.2-reference`（commit `b1247af`）。
- GitHub 已確認 Secret scanning 與 push protection 開啟；Dependabot 設定已提交，但 Dependabot security updates／automated fixes 仍需 owner 在 GitHub Settings 依帳號方案確認。這個限制已記錄於公開檢查表，不把 API 404 誤報成已啟用。

AUD-18 狀態為 **公開快照已發布、後續維運設定待 owner 確認**。公開快照使用 orphan history，排除私人環境與內部 evidence；這項工作不改回既有編輯保護、Reader、首頁或 Mobile 導覽設計，也不宣稱平台已經是 turnkey production SaaS。

本次公開準備後的驗證：GitHub Actions CI run [35196049028](https://github.com/zzxx5124/storylingo/actions/runs/35196049028) 在 commit `b1247af` 通過；後端 715 passed／1 skipped、前端 17 個測試檔 94 passed、語法／compile 通過，E2E 單 worker 392 passed。本機 Playwright 清單列出 394 個案例；遠端以實際 CI summary 為準，沒有失敗 job。公開截圖產生 spec 1/1、受影響的登入／公開流程 60/60、Reader Desktop/Mobile 18/18 亦已通過。CI 使用 Node.js 22.22.2 以符合目前 jsdom／undici 的 engine 要求。
