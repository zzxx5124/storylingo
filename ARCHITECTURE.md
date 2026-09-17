# 架構總覽 ARCHITECTURE

> 描述資料流、模組邊界、安全機制與已知的高耦合/易出 Bug 區域。變更任何功能前請先對照本文件。
> **V4 RELEASE READY**；legacy generation caller = 0。Live UAT 已證明：真實 OpenAI multi 分析、真實本地 TTS multi 合成、多語者使用不同 voice、author/reader playback 全部成功。

## 1. 高階結構

```
Browser
 ├─ index.html + services/bootstrap.js (初始 visibility gate) ─┐
 ├─ platform.js (公開平台 SPA)  ──────────────────────────────┤
 └─ app.js (管理/播放器/後台)   ──────────────────────────────┤ → services/api.js (NovelApi.request)
                                 │         ├─ 統一 fetch + CSRF header + credentials
                                 └─────────▼
                              FastAPI (backend/main.py)
                                 │
        ┌────────────┬───────────┴───────────┬────────────────┐
        ▼            ▼                       ▼                ▼
   routers/      settings/auth/security    db.py (SQLite)    jobs.py (queue + worker)
        │            │                        │                 │
        └────────────┴───────────┬────────────┤                 ├─ analysis_executor.py (AI)
                                 ▼            │                 ├─ generation_pipeline.py (TTS)
                            storage/books/{bid}                 └─ tts_adapter.py (remote provider)
                           (analyze/audio/timing/cover)
```

**初始載入生命週期**：`index.html` 初始將所有產品 view 設為 hidden，只保留 `#app-bootstrap` neutral gate。`app.js` 與 `platform.js` 以同一個 `frontend/services/bootstrap.js` controller 協作，依序記錄 `BOOTSTRAPPING → ROUTE_RESOLVED → VIEW_VISIBLE`；平台 route 在 auth bootstrap 完成後揭示自己的 loading shell，page fetch 的 loading/error 留在該 route 內，管理／安全預設 route 在權限判定後揭示。controller 也會保證任何 owner 直接揭示 view 時先完成 route resolution。auth 失敗仍由目前 route 呈現 guest-safe 或 route 內錯誤，不回退成前一個 view。

**Web release/cache contract**：`backend/main.py` 對 SPA shell、Service Worker、manifest、versioned static asset 與 API 分別設定 cache policy；`/`、`/index.html`、`/sw.js` 使用 `no-cache, must-revalidate`，含 `?v=`／`?release=` 的 immutable asset 才可長期 cache，未版本化 static asset 必須 revalidate，API 預設 `private, no-store`（route 自己宣告的 reviewed image policy 不被覆蓋）。每個 response 帶安全的 `X-StoryLingo-Release`，`/api/health/live` 同時回傳 `releaseId`。

Service Worker (`frontend/sw.js`) 不再 cache-first serve `/`；navigation 先取 network，只有離線 fallback 才使用 cached `/index.html`，且以明確 cache identity 清除舊 cache。`frontend/release.js` 以 no-store live check 偵測 open SPA 的 release mismatch，顯示可操作的 reload banner；標記 dirty/unsaved 時先警告，絕不清除 localStorage/sessionStorage。部署時須更新 release identity 與變更 static asset 的 query version，避免新舊 shell 混用。

## 2. 資料真源（Single Source of Truth）

| 資料 | 真源 | 備註 |
|---|---|---|
| 書籍/章節/使用者/追書/分類/庫藏/進度/通知/公告/任務/稽核證據 | `backend/db.py`（SQLite `data/app.db`） | schema 由 `init_db()` 建表 + 可重跑 migration；稽核 writer 與 append-only guard 由 `services/audit.py`／DB trigger 保護 |
| 生成產物（analysis JSON、mp3、timing、cover） | `storage/books/{bid}/` | 從 DB 的欄位（如 `chapter_analyses.artifact_path`）關聯 |
| V4 執行期契約（domain 名稱、enum、API 形狀） | `backend/v4_contracts.py` | 本章 §4.4 為其摘要 |

**規則**：讀寫業務資料一律走 `db.py`；`storage.py` 只處理檔案產物。不要把兩者混為同一份資料。

## 3. 模組依賴與邊界

```
routers (HTTP/權限層)
   └─ 直接使用 db.py、storage.py、auth/security
   └─ 生成類 endpoint → enqueue_job()（jobs.py），回傳 job id，不直接跑長任務
services (可重用邏輯)
   ├─ book_service / chapter_service / category / voice   → 業務規則 + db
   ├─ tts_provider（遠端 TTS 客戶端：SSRF 防護、機密加密、legacy 與 Provider API v1 binary transport）
   ├─ netsec（共用 URL/SSRF 驗證：validate_http_url / is_private_host）
   ├─ ai_provider（V4 AI provider：Fernet 機密、單一 default、SSRF 驗證）
   ├─ analysis（schemaVersion 4 source-faithful writer、v3 compatibility reader、stable identity、emotion fallback、chapter_analyses repo）
   ├─ analysis_executor（V4 analyzer job：綁定 AI provider、source hash pointer）
   ├─ character_resolution（book-level stable character registry、alias/candidate、revision-safe reconciliation、merge/split、voice conflict、逐書 migration）
   ├─ audio_generation（V4 generation domain：deterministic key、active pointer）
   ├─ generation_orchestration（operation/job/attempt、typed service queue、provider snapshot、retry/cancel/revision policy）
   ├─ generation_pipeline（V4 audio_single/multi/all jobs、輸出驗證後 atomic 啟動）
   ├─ audit（R18 canonical audit writer、event-class/policy metadata、secret redaction、correction lineage）
   ├─ tts_adapter（V4 adapter_key + capabilities 快取/emotion 支援偵測；cosyvoice_http 只透過獨立 Provider API v1）
   ├─ emotion（V4 emotion→TTS mapping、best_effort fallback、非機密 summary）
   ├─ workflow（V4 derived author workflow state）
   ├─ admin_console（Admin bounded read models、pagination、safe DTO、overview alerts、announcement list）
   ├─ notifications（backend-owned event allowlist、Account recipient、dedupe、immutable snapshot、private read model）
   ├─ announcements（plain-text announcement aggregate、audience/schedule、acknowledgement、safe delivery）
    ├─ profile（Public Profile／Author Profile validation、public serialization、作者頁與 avatar isolation）
   └─ distributed（Redis 分散式鎖；無 Redis 時 fallback process-local）
jobs.py：DB 持久化佇列，保留單一 canonical `generation_jobs`，但以 AI/TTS service-specific consumer
分流；每次 claim 建立 execution attempt、lease 與 fencing token。provider capacity、retry schedule、
cancel request、source/config snapshot 與 bounded failure category 都保存在 DB read model，
`generation_orchestration.py` 負責 operation/job 建立、依賴與錯誤分類。worker 啟動會登錄
`worker_instance_id`、`worker_version`、build SHA、service types 與 heartbeat；PID 只作診斷。
`WORKER_ENABLED=false` 可啟動 web-only instance。SQLite claim guard、build guard、lease recovery
與 token-fenced finalize 會拒絕舊 runtime 或 stale worker 覆寫狀態；`/api/health/ready` 回報 worker
identity 與 AI/TTS consumers。長 provider call 不持有 DB transaction，heartbeat 只延長目前 token 的 lease。
```

### 3.1 V4 domain model

| Domain | DB 表 | Service | 重點 |
|---|---|---|---|
| 內容申請 | `content_requests`／`content_request_events` | `content_requests.py` | publish/unpublish/audiobook typed aggregate、六狀態 atomic transition、append-only event history、submitted revision、active duplicate guard |
| 所有權轉移 | `ownership_transfer_requests`／`ownership_transfer_events` | `ownership_transfer.py` | current owner → exact target acceptance → Reviewer/Admin review → atomic `books.owner_id` CAS；7 天 expiry、active-generation guard、補償式反向移交與 Super Admin emergency history |
| AI provider | `ai_providers` | `ai_provider.py` | Fernet 加密 secret、單一 default（部分唯一索引）、SSRF 驗證、後台 UI 管理 |
| 分析 | `chapter_analyses` | `analysis.py` | 新作者分析預設 schemaVersion 4 source-faithful writer；v3 僅 compatibility reader/明確 legacy path；stable character_id/speaker_id、emotion neutral fallback、source hash gate；ready 後才交給 identity registry |
| 角色 identity | `character_registry`／`character_aliases`／`character_resolution_events` | `character_resolution.py` | book-scoped stable identity、scoped alias、proposal gate、append-only reconciliation、registry revision |
| 音訊生成 | `audio_generations` | `audio_generation.py` | deterministic generation_key、active pointer |
| Generation operation | `generation_operations` | `generation_orchestration.py` / `db.py` | audiobook authorization linkage、source snapshot、derived status |
| Generation job | `generation_jobs` | `jobs.py` / `db.py` | AI/TTS typed queue、provider binding、lease、retry、cancel、progress |
| Execution attempt | `generation_job_attempts` | `db.py` | append-only worker/provider outcome、bounded diagnostics、retry lineage |
| TTS adapter | `tts_providers`(+capabilities) | `tts_adapter.py` | adapter_key、capabilities 快取/hash |
| 工作流 | （read model） | `workflow.py` | 依 canonical state 推導 next-step |
| Admin Console read model | （read model） | `admin_console.py` | bounded list/overview projection、stable pagination、safe provider/job/audit DTO；不建立第二套業務真源 |
| Canonical audit evidence | `audit_logs` | `audit.py`／`db.py` | allowlisted action、event class、policy version、bounded redaction、opaque deleted actor、append-only DB triggers；domain events、generation attempts、notifications 分離 |
| Platform announcement | `announcements`／`announcement_audience_roles`／`announcement_acknowledgements` | `announcements.py`／`routers/announcements.py` | UTC active filtering、fixed audience、config/display version、archive、safe acknowledgement；不等同 banner/notification |
| Notification | `notifications` | `notifications.py`／`routers/platform.py` | Account-owned typed event、immutable title/body snapshot、dedupe/source refs、bounded read/count/read mutations；private/no-store，與 Announcement、Audit 分離 |

**不變安全規則**：章節文字/音訊一律走 `book_service.can_view_chapter`（含 `publish_status`），任何重構不得弱化。

### 3.2 Roles / review workflow

目前 runtime 使用固定五角色：`reader`、`author`、`reviewer`、`admin`、`super_admin`。`backend/services/policy.py` 是 backend capability canonical layer，以 explicit capability mapping 取代 numeric role rank；frontend role checks 只控制 UX，不能授予安全權限。Phase 1 `admin` 保留明確的 review/generation capability，以避免尚未建立 Reviewer 帳號時的平台 operational lockout。

`super_admin` 是唯一 protected role truth，不另設 `is_super_admin`。`backend/db.py` 以 transaction 保護 ordinary Admin/Reviewer/Author/Reader 的角色邊界、最後一個 active Super Admin 的 `LAST_SUPER_ADMIN_PROTECTION`、`session_version` freshness 與 role grant/revoke/bootstrap audit。`scripts/bootstrap_super_admin.py` 只接受既存 Account 的 exact `--account-id`，在 production 直接拒絕執行。

`content_requests` 保存 `publish`、`unpublish`、`audiobook` 的 requester/reviewer、submitted revision、決策與 lifecycle；`content_request_events` 保存 append-only `submitted`、`review_started`、`approved`、`rejected`、`cancelled`、`invalidated` 與適用的 `privileged_override`。Request state、Book publication state 與 DB generation job state 是分離真源。Reviewer approval 經 expected-state + atomic transaction，publish/unpublish 才同步套用 Book visibility；audiobook approval 只表示 `AUTHORIZED_FOR_GENERATION`，後續 operation 另行建立/連結 DB job。

Author 的 content/metadata/owned-book editing 保留；publish、unpublish、audiobook 改走 typed request。Reviewer 可 claim/approve/reject/read approved follow-up，但不能編輯 Author content、改 ownership、管理 roles/provider secrets 或 emergency hide。Public API 仍由 canonical Book/chapter visibility predicate 控制，legacy published works 與 existing analysis/audio 不建立偽造 workflow history。

Ownership Transfer 已落地為獨立 aggregate：`backend/services/ownership_transfer.py` 與 `backend/routers/ownership_transfer.py` 提供 owner 發起、exact target 本人接受／拒絕、Reviewer/Admin claim/approve/reject、7 天過期、owner 於 target action 前取消，以及 Super Admin recent-auth、reasoned emergency path。普通完成與 emergency 都以 SQLite `BEGIN IMMEDIATE`、source revision、current owner CAS 與 canonical `generation_jobs` non-terminal guard 保護；只更新 `books.owner_id`，不改 `author_profile_id`、Book/chapter/analysis/audio/storage IDs、讀者關係或統計。`ownership_transfer_events` 是 append-only business history，security audit 仍使用既有 `audit_logs`；已完成轉移不提供 destructive undo，反向移交必須建立新的 audited request。

### 3.3 Notification system

`backend/services/notifications.py` 是 in-app Notification 的 single typed persistence/read boundary。既有 `notifications` table 以 additive migration 增加 concrete `recipient_account_id`、backend-owned `event_type/category`、immutable title/body snapshots、safe target/source references 與 deterministic `dedupe_key`；legacy `add_notification` 只作 compatibility adapter，不形成第二套 writable truth，且 migration 不回填歷史事件。

Author application、content request decision/invalidation、generation canonical terminal finalization 與既有 follower chapter publication 由各自 domain writer 解析 concrete Account 後寫入 typed service。generation ready 只有在 artifact/current pointer 完成後才通知；retry、heartbeat、progress、attempt/stale-worker 與 owner self-action 不產生 inbox spam。通知 failure 不回滾已完成 generation artifact，terminal recovery 會以相同 dedupe identity 補寫。

`/api/notifications`、`/unread-count`、`/{id}/read` 與 `/read-all` 只接受 current authenticated Account，list/filter/count 有 bounded SQL contract，mutation 經 CSRF 且 IDOR-safe；所有 response 設 `private, no-store`，Service Worker 不 cache `/api/`。frontend `services/notifications.js` 是 app/platform 共用 coordinator，提供 non-blocking badge、bounded popover、`#/notifications` center、explicit read semantics、safe internal target click-time authorization、route snapshot fencing 與 logout/account-switch principal fencing；通知讀取完成後只有在原始 hash 與 principal generation 仍一致時，才可套用 target route，因此 Back/Forward/reload 或帳號切換不會被 late response 覆寫。Announcement 仍使用自己的 aggregate、acknowledgement 與 popup，不互相消費 read state。通知 v1 不包含 email/push/SMS/WebSocket、broadcast、delivery receipt、device subscription 或 retention cleanup。

### 3.4 Design system foundation（目前已實作範圍）

有聲書清單錯誤使用 button 原地重試目前 hash，保留搜尋／分類／語言／排序／頁碼，並沿用 route/query generation；不以固定 #/audiobooks link 或全頁 reload 清掉查詢。重試立刻顯示 loading，離開後的晚回應不回寫。

私人書架的收藏與閱讀紀錄以同一個 route/query/principal snapshot 載入；loading、錯誤與 retry 都留在目前頁面，重試保留 favorite_page／history_page。收藏與歷史分頁會各自形成正確的 query（沒有既有 query 時使用 ?），錯誤 catch 也受同一 current guard 約束，因此離開、登出或切換帳號後的晚成功／晚失敗不能回寫私人畫面；未登入仍只顯示登入入口，不發送私人請求。

排行榜列使用非互動 article，作品標題／封面／名次位於原生作品連結，作者頁入口為獨立連結；無公開作者頁則為純文字。沒有巢狀 button/link 或作品 data-book 攔截，Tab／Enter 與另開分頁沿用瀏覽器行為；既有排行 request fencing 與錯誤重試維持。

排行榜分類 tabs 使用單一 roving tabindex；一般 Tab 進入目前 tab，左右方向鍵與 Home／End 只移動焦點，Enter／Space 或點擊才啟用分類。每個 tab 以 aria-controls 關聯同一個 tabpanel，panel 以 aria-labelledby 指向目前選定 tab；載入、empty、error/retry 與 latest-request-wins 保持原有行為，Mobile 320px 不產生橫向溢位。

全域 Mobile 導覽由 `services/public-navigation.js` 管理：760px 以下提供首頁／搜尋／我的書架及依原有角色允許的我的作品底部連結，標示 canonical route 的 active state；章節管理提供返回我的作品，其餘區域保留回首頁。手機工作區為單層，帳號名稱／登出移入選單，通知／個人設定留在頂部；Desktop 恢復原有 workspace disclosure 與帳號區。重用既有 DOM、事件與 app.js 權限 hidden 狀態，不新增授權真源。Escape、外部點擊、換 route／breakpoint 關閉選單。Mobile Reader 隱藏全域列，沿用自身作品／章節導覽；其書籤登入 dialog 暫移 body，離開 Reader 或切回 Desktop 後歸位，避免被隱藏的頁首遮住。

首頁目前採「簡短平台說明 → 最近閱讀（登入且有紀錄）→ 單一選書區」資訊架構。`#/home?collection=latest|popular|completed` 切換最新／熱門／完結，每份清單去重後最多六本，保留搜尋／排行的完整清單入口；無 query 時優先使用有內容的最新清單。Desktop 搜尋、前六個分類與閱讀方式說明置於旁欄，Mobile 先呈現故事再呈現輔助探索；既有 banners 改為最多五個站內精選連結，不再自動輪播搶佔首屏。作者的我的作品入口為次要位置，既有全域導覽與作者申請不變。

`GET /api/home` 保留既有 collection 欄位，僅在 current Account 的 `continueReading` card 加入選用 `readingProgress`（chapterSeq/title、percent、lastMode）。進度從既有 DB 推導；即使使用者是作品 owner，也先以公開章節權限確認才投影章節標題與續讀入口。失效紀錄只顯示返回作品，訪客不呈現私人紀錄。首頁 response 綁定 route/query/principal generation，loading、全空、單份清單空與 error/retry 各自呈現；不新增推薦引擎、私人資料快取或第二套進度真源。

`frontend/components/` 保留原生 JavaScript SPA 與既有 `NovelUI` compatibility entry，並提供可漸進採用的 `Button`、`IconButton`、`Field`、`Modal`、`ConfirmDialog`、`StatusBadge`、`StatusState`、`PageHeader`、`FilterBar`、`Pagination`、`Toast` 與 `BookCard`。`frontend/style.css` 增加 semantic token aliases、focus、status、form、dialog 與窄版 layout contract；`components-preview.html` 是 noindex proof surface，並不代表全站已完成 UI migration。

Foundation 的 Dialog focus/ESC/restore、表單 label/error association、loading/disabled state、status text semantics 與 320/375/390/768/1024/1440 寬度 evidence 已由 focused unit/browser review 覆蓋。此 child 不改 route、API、authorization、CSRF、schema、Service Worker、Reader/player 或整體 framework。

`public-experience-ui-convergence` 已將這套 foundation 漸進套用到 public shell 與首頁、搜尋／分類、排行、有聲書、Book Detail、公開 Author Profile、書架及其 loading/empty/error states。`frontend/services/public-navigation.js` 管理公開目的地、authenticated workspace disclosure、active route、Escape 與窄版 focus 順序；`platform.js` 保留 NovelApi、server pagination、visibility、audiobook playable predicate 與 private collection truth。Public Book card 使用真正的 primary link，作者與聆聽 action 保持非巢狀互動邊界；這些是呈現層收斂，不是新的 authorization 或 domain truth。此 child 已通過 320/375/390/768/1024/1440 responsive evidence、Vitest 與 252/252 deterministic E2E；routine human UAT 依專案政策 waived，正式 release 另記 `FINAL HUMAN RELEASE UAT = PASS | WAIVED | PENDING`。

2026-09-04 quality sprint 在上述 foundation 上完成第一波 `Editorial Ledger / 紙上書庫` public convergence：`frontend/style.css` 以 `body.platform-mode` 限定 paper surface、serif content hierarchy、rule rhythm、flat collection entry 與 square controls；`frontend/platform.js` 的 Book Detail 使用單一 entity `h1`，不再同時渲染 page header 與重複作品標題。此 slice 不改 backend、schema、data、API、authorization、Reader/player、Admin/Reviewer business semantics 或 Service Worker；品牌改名、logo、正式字型資產與全站 redesign 仍是 future/user-review gate。

User Real UAT remediation 的目前行為：登入後的通知入口位於 Account identity 同層的 bell/link，仍由 `frontend/services/notifications.js` 共用 coordinator 驅動，workspace disclosure 不再是主要入口。公開條款與隱私政策可由頁尾及既有 hash route 存取，頁面標示 `2026-09-04` 基本產品基線；內部法務狀態為 `BASIC PRODUCT BASELINE — JURISDICTION-SPECIFIC LEGAL REVIEW PENDING`，法域、營運者、正式聯絡方式、供應商與保存期限仍待確認，未臆定法定權利或固定保存天數。`source_faithful.py` 的目前 deterministic segmentation contract 為 `paragraph-quote-sentence-v2`；Reader API 發現 schemaVersion 4 artifact 使用舊 segmentation version 時，保留 immutable 舊 artifact 但回傳目前來源句子 spans、標記 `stale` 並省略不相容 timing。Reader 會顯示來源句子分段，直到重新生成相容分析／音訊前不宣稱可同步播放。這些修正不改寫既有資料或歷史產物；routine human UAT 依專案政策 waived，正式 release 另記 `FINAL HUMAN RELEASE UAT = PASS | WAIVED | PENDING`。

## 4. 資料流與 API 契約

### 4.1 書籍上傳 → 章節建立（作者內容流程）

章節編輯器以 route／Account／Book 綁定單次開啟 session；載入完成前不可儲存，失敗可重試，儲存 pending 時禁止重複提交。未儲存文字與原始版本條件暫存於同分頁 sessionStorage（Account/Book/seq 分隔），reload 後重新開啟可找回；這不是跨裝置草稿或伺服器備份。儲存成功只清除該請求開始時的草稿，晚到回應不可覆寫或清除後續編輯內容。還原歷史版本後重新 GET chapter 正文，不能用不含原文的 Book summary 填入編輯器。

Manager 的 chapter GET 額外回傳 `chapterKey`／`textHash`；PUT 可帶 `expectedChapterKey`／`expectedTextHash`／`expectedTitle`，在 SQLite immediate transaction 中檢查，衝突回 409 且不寫入。舊 caller 省略條件時保持相容；不能宣稱所有外部 caller 已有 optimistic concurrency。新編輯器不 trim 正文，只以 trim 判斷是否空白。Book DTO 的 `favorite` 直接以 current Account 的 `library_items` 關係判定，不從收藏清單第一頁推導。
作品 metadata 編輯沿用同一個 route／Account 綁定的 session；GET 失敗時不可提交，pending 時鎖定表單並以 sessionStorage 保存同分頁草稿。`GET /api/books/{bid}` 與作者列表 DTO 回傳 `metadataHash`；`PUT /api/books/{bid}` 可帶 `expectedMetadataHash`（舊 caller 仍可用 `expectedUpdatedAt`），在 book lock + SQLite atomic transaction 中檢查，衝突回 409 且保留前端輸入。成功只清除本次請求開始時的草稿，晚到 response 不可關閉或改寫已離開的編輯器。
個人設定頁使用 account／route 綁定的 editor session；設定 GET 失敗時不可提交，可重試或明確放棄草稿，未儲存的公開名稱、簡介與 email 以 account-scoped sessionStorage 暫存。`GET /api/auth/profile` 回傳 opaque `profileRevision`；`PATCH /api/auth/profile` 可帶 `expectedProfileRevision`，在 SQLite immediate transaction 中檢查，衝突回 409 且保留輸入。頭像上傳仍是獨立的 media mutation，既有 recent-auth、email verification、OAuth 與 author profile 權限不變。

作品管理連結統一使用 `#/detail/{bid}`，由 route 開啟管理畫面；Book load／poll／refresh 與公開 detail 的 late response 不得跨 route/principal 寫回。公開排行以最後一次 filter request 為準，錯誤重試在原頁重新請求；作品無公開章節時呈現空目錄並移除無效閱讀連結。公開收藏／追蹤／留言在 pending 時鎖定送出。`X-StoryLingo-Public` 仍以 visitor projection 過濾章節與管理資料，只附上 current Account 的收藏／追蹤布林關係；Reader 首末章導覽使用「返回目錄／返回作品」描述實際目的地。

1. `POST /api/books` → 建立書籍 + 章節（`splitter.split_chapters` 拆章；支援一般 `第N章` 與傳統戲曲常見的 `第N齣` 邊界）。對較舊 persistent SQLite 章節表，`init_db()` 以 additive、可重跑 migration 補齊 V4 finalizer 所需的 `created_at`／`updated_at` 欄位，不改寫既有內容或產物。
2. `POST /api/books/manual` → 建立空書，作者以 `POST /books/{bid}/chapters` 撰寫章節（`chapter_key` 產生；第一章由後端分配 `seq=0`）。書籍可保存 `settings.hasPrologue` 章節編號設定；舊書未設定時維持「序章」顯示，新書選擇無序章時仍保留零起始 `seq`、章節 identity、分析與音訊路徑，只將 `seq=0` 顯示為第 1 章並順移後續顯示編號。
3. 內容後續：章節編輯／重排／發布走既有章節 CRUD（`can_view_chapter` 權限不變）。

### 4.2 V4 canonical generation flow（唯一 live 作者生成路徑）

1. **音訊設定**：`PUT /api/books/{bid}` 接受 `audioMode`（single/multi）與 `defaultVoiceId`；任何改變生成輸出的設定都 increment `audio_settings_version`。
2. **分析**：`POST /api/books/{bid}/chapters/{seq}/analysis` → enqueue `speaker_analysis` job → `analysis_executor.py` 以 enqueue 時綁定的 AI provider 快照執行。新作者 Analyze/Re-analyze 與批次分析預設 schemaVersion 4；v3 僅保留 compatibility reader 與明確 legacy fixture/rollback path。v4 固定走 **source → deterministic segmentation → chunk/group → structured annotation → segment reference validation → entity resolution → canonical v4 artifact → normalize/validate → current source hash → save ready → register identity view → derive roster**。v4 canonical text/span 永遠由 backend source slice 建立，`_offset_for()` 只保留 legacy v1/v2/v3 compatibility path；v4 gate 失敗不得改寫或切換既有 v3 ready artifact。AI reconciliation 只能產生 proposal；confidence threshold 與 deterministic safety checks 通過後才可接受，substring 不得決定 canonical identity。跨章修正透過 append-only event，不改寫 ready artifact。player/TTS 由 v3/v4 artifact 經 compatibility projection 後沿用 existing pipeline；stable-ID-native TTS 留給後續 change。既有 `books.voices`／`speaker_info`／`speaker_chapters` 是由 validated ready artifact 加 accepted registry mapping 產生的 name-keyed compatibility projection；roster projection 失敗可從既有 ready artifact retry，不重新呼叫 AI。作者可透過 `/api/books/{bid}/chapters/{seq}/analysis/cancel` 取消單章，或 `/api/books/{bid}/analysis/batch/cancel` 取消全書批次；取消中的 job 以 `cancelled` 結束，所屬 active analysis 以 `analysis_cancelled` failure 收斂，已完成章節與歷史紀錄保留且重新分析建立新 job。
3. **生成**：`POST /api/books/{bid}/chapters/{seq}/audio-generations` → **preflight 聲線驗證**（single 需已設聲線且於 provider catalog 內；multi 需所有語者聲線皆在 catalog；缺失 → 422 產品化訊息，不建立注定失敗的 job）→ `audio_generation.py` 計算 deterministic generation_key（同 key 冪等重用）→ enqueue `audio_single`/`audio_multi` job → `generation_pipeline.py` 經 `tts_adapter.py` 合成至 generation-specific 路徑（`storage/books/{bid}/generations/{id}/audio.mp3`、`timing.json`）→ 輸出驗證後 atomic 設定 `chapters.active_audio_generation_id`。長文字先由平台依句界拆成 bounded synthesis units；每個 unit 的 provider 輸出可寫入 private `storage/tts_partials` deterministic cache，重試/重啟只重送 cache miss，provider 未宣告容量時維持串行，僅在 capabilities 明確宣告 `limits.maxConcurrent` 時採用受限並行。**worker 端二次驗證**：聲線不在 provider catalog 時以明確錯誤失敗（不把 Edge fallback id 送給遠端 provider）。single 模式以 `default_voice_id`（章節覆寫優先）注入旁白聲線，不需 analysis；multi 需 matching ready analysis，語音對映沿用 `books.voices`（speaker→voice）。**mode-stale**：切換朗讀方式後，舊 mode 的 ready generation 不再視為可播放（workflow/playback 均檢查 `gen.mode == effective_mode`）。
4. **批次**：`POST /api/books/{bid}/analysis/batch`、`POST /api/books/{bid}/audio-generations/batch` 以 persisted child jobs 執行，支援 partial success。
5. **播放**：`GET /api/books/{bid}/chapters/{seq}` 回傳 **V4 canonical analysis**（`chapter_analyses` ready＋source hash 相符時含逐句 segments，供播放器字幕/同步）與 active ready generation 的 timing；`/audio/{seq}` 回傳 active generation 音檔。權限一律 `book_service.can_view_chapter`（含 `publish_status`）。legacy `chapters.status/audio` 欄位由 canonical 紀錄投影。
6. **工作流狀態**：`book_service.get_book_legacy` 對 owner/admin 附加 derived `workflow`（一律由 chapter_analyses／audio_generations 推導，含 `audio_failed` 失敗狀態與錯誤訊息，nextAction 提供重試）；前端 `services/workflow.js`（`NovelWorkflow`）驅動作者 UI 的 next-step；**章節動作／批次按鈕（分析剩餘、生成剩餘）／filter 一律以同一 workflow state 為判定來源**（single 模式不顯示「分析剩餘」）。provider outage 呈現為 `provider_unavailable` 平台狀態。

7. **Platform announcements**：Admin 透過 `/api/admin/announcements` 管理獨立 plain-text Announcement aggregate；`GET /api/announcements/active` 由 backend 依 archived/enabled/UTC/audience/current Account/ack eligibility 過濾，回傳 bounded safe DTO 並設定 `private, no-store`。`frontend/services/announcements.js` 接在 bootstrap `VIEW_VISIBLE`，只建立一個 accessible `dialog`、每次 entry 最多三則，並 defer critical modal/audio；authenticated once-per-version ack 寫入 server，guest markers 留在 bounded browser storage。公告不寫 notification、banner 或 governance audit display/ack telemetry。
8. **Generation orchestration**：approved audiobook request 先建立 `generation_operations`（`AUTHORIZED_FOR_GENERATION`），再以一張 `generation_jobs` 表表達 AI/TTS typed executable work；`generation_job_attempts` 保存每次實際 provider execution。AI/TTS consumers 各自依 service、provider capacity、`next_attempt_at` 與 dependency claim，互不因另一 service 的容量或 busy 而阻塞。claim/finalize/progress/heartbeat/retry/cancel 全部使用 expected-state + lease/fencing token；lease 過期會記錄 `worker_lost` 並 bounded requeue 或 terminal failure。
9. **Generation operational surfaces**：Reviewer 可從已核准 audiobook request 進入 operation/job detail，看到 safe provider label、progress、attempt/error、retry/cancel；Admin 可查看 AI/TTS summary、active workers、provider capacity、queue age 與 failed jobs。這些 endpoint 只回傳 sanitized operational DTO，provider secret、raw response 與 credential 永不進入 Reviewer/attempt payload。Author 只看 workflow/request 到 generation status 的橋接，不取得 infrastructure mutation authority。

### 4.3 讀者流程（公開平台）

首頁 `/api/home`（banners/continue_reading/rankings/latest/completed）→ 搜尋 `/api/search`(+suggest) → 詳情 `/api/books/{bid}` → 閱讀 `GET /books/{bid}/read/{seq}`（只回公開章節）→ 進度 `PUT /api/me/progress` → 書架 `/api/me/library`、歷史 `/api/me/history`、通知 `/api/notifications`。讀者聽書走 `#/read/{bid}/{seq}`；公開 reader 以 `frontend/platform.js` controller 管理 native `<audio>`、bounded auto-next、文字/audio progress、bookmark、fullscreen、Wake Lock 與 Media Session progressive enhancement，音訊仍播 `/api/books/{bid}/audio/{seq}`。

**公開可見性統一規則（P1）**：公開瀏覽面（首頁、搜尋、排行、推薦、suggest、書籍詳情、閱讀）一律只回「`status='approved' AND published_at IS NOT NULL`」的作品，與呼叫者角色（含 admin/dev）無關；草稿／待審核／拒絕／下架作品絕不透過公開瀏覽洩漏。作者以 `mine=1` 管理自己的非公開作品，管理員以 `/api/admin/*` 管理全部。書籍詳情對非公開作品對非 owner/admin 一律 404。

### 4.3.2 Platform collection pagination（目前已實作範圍）

Public search/category、explicit paged My Works、private favorite/followed/history/bookmarks 與 public author works 使用共同 page-based envelope：`items`、`page`、`page_size`、`total`、`total_pages`、`has_next`、`has_prev`。`backend/pagination.py` 統一 one-based bounds（預設 20、common max 100）；各 domain query 仍自行宣告 filter/sort，不能以任意 SQL 欄位或前端 post-filter 取代 backend predicate。

Normalized query 的順序固定為 current principal/authorization → public visibility 或 owner scope → search/filter → allowlisted deterministic ordering → exact count → SQL `LIMIT/OFFSET`。public search 的 `has_audio` 在 page 前以 `EXISTS` predicate 判定；book cards 只回 bounded projection，chapter counts、owners、categories、Author Profile 以 batch read 取得。`backend/db.py` 的 library/follow/history/bookmark/author indexes 是 additive、idempotent、SQLite-safe，沒有改寫 ownership、attribution、audio 或 analysis identifiers。

`/api/me/*` 與 explicit paged My Works response 設 `private, no-store`、`Vary: Cookie`；Service Worker 對 `/api/` 維持 bypass。省略 page 的既有 `/api/books?mine=1` 保留 legacy array compatibility boundary，新作者工作台只使用 explicit paged contract。`frontend/services/pagination.js` 負責 envelope normalization 與 semantic paginator；`platform.js`、`app.js` 由 domain state 管理 hash/query、mutation refetch、last-page recovery、route/query/principal generation fencing，避免 late response 或帳號切換污染畫面。

本 scope 不重寫 Admin/Review/Generation/Notification local pagination，不將 homepage curated sections、rankings、recommendations、chapter TOC、selectors/suggestions 強行改成 archive paginator；cursor、高 churn feed、Reader UX 與 production configuration 仍是獨立 future gates。

### 4.3.3 Public Audiobook Discovery

`GET /api/audiobooks` 與 `#/audiobooks` 提供專用公開有聲書 collection。`backend/services/book_service.py` 的 `public_audiobook_availability`／`list_audiobooks` 以同一個 backend-owned projection 判定：Book 必須已公開、章節必須已發布，且 active canonical generation 通過 ready/source/mode/character-registry 檢查並有安全可解析的 artifact，或 legacy `chapters.audio='ready'` 路徑仍有 book-boundary 內的實體檔案。只有至少一個可播放公開章節的作品進入 collection；partial 以 playable/public chapter X/N 呈現，第一個可播放章節依 canonical `seq` 選取。

Dedicated collection 支援 bounded `q`、dynamic `category_id`、language/type、`newest`／`updated`／`title` allowlisted sorting 與 Platform Pagination envelope。public DTO 只含 safe book metadata、Author Profile summary、availability/count 與 detail/listen routes，不含 owner/account security identity、request/operation/job/attempt、provider/model、failure detail、storage path 或 secret。`GET /api/audiobooks` 使用 `Cache-Control: no-store`；reader/audio route 在 click time 仍以 `can_view_chapter` 作最後授權邊界。

### 4.3.4 Public Reader / Audiobook UX

公開 reader 的 route/principal generation、audio listener、progress timer、Wake Lock 與 Media Session 都由可 dispose 的 local controller 管理；離開 route 或 principal 變更時會停止舊 audio、釋放能力並拒絕 stale response。auto-next 預設關閉，只有同一次明確播放鏈且下一章重新通過 public read contract 並具備 `audio='ready'` 時才銜接；瀏覽器拒絕 autoplay 不會被宣稱為播放成功。

reader progress 使用既有 `/api/me/progress` 保存 chapter、mode、文字 scroll 與 audio position；登入資料以 current Account 為界，訪客只寫入 book-scoped bounded localStorage。書籤沿用 `/api/me/bookmarks`，fullscreen 使用明確 user gesture；Wake Lock 與 Media Session 都是 feature-detected、失敗不阻斷播放的 enhancement。mobile 採明確 controls toggle 與 wrapping toolbar，不攔截一般 scroll。公開錯誤訊息不渲染 backend detail、provider、storage path 或 token。

公開作品詳情的閱讀入口以單一主要決策為中心：首次進入顯示「開始閱讀」，若 current Account 或同分頁訪客有有效進度則顯示「繼續閱讀」並保留「從第一章開始」；有聲作品提供同層的「聽書跟讀」替代入口。語言學習不再在詳情頁複製成第三個開始選項，而是在 Reader 內以學習工具切換。Reader 的純文字／聽書切換固定位於章節標題下方，返回作品目錄、章節上一頁／下一頁與音訊控制分層呈現，行動版導覽按鈕保有可單手操作的寬度。章節目錄仍採 progressive disclosure：長篇作品先呈現前 12 章，其他章節放在可鍵盤操作的原生 details 展開區；每一章仍保留 canonical reader link，空目錄則維持收藏／追蹤空狀態。作者 `/mine` 作品卡使用 workflow derived read model 顯示「下一步」；送審、管理章節、編輯與所有權轉移保持直接入口，低頻申請／統計／刪除收進明確的「更多操作」，行動版以 grid 重新排列，避免作品資訊被操作列壓窄。

既有 `/api/search?has_audio=1`、`audioReadyCount`、`audioRatio` 與 `rankings?kind=audio` 保持 compatibility semantics，沒有在本 change 靜默重定義；它們與 dedicated canonical projection 的差異記錄於 `openspec/changes/archive/2026-09-03-audiobook-discovery/CURRENT_AUDIOBOOK_DISCOVERY_MAP.md`。本流程只調整前端入口與 Reader layout，不增加 schema/index、generation orchestration、notification、announcement 或 tracking。

### 4.3.1 Identity/Profile 與作者導覽

目前已完成 Account + Public Profile + Author Profile 的最小落地：`users.id` 是 authentication、session、ownership、social、generation 與 audit 的 canonical Account key；`public_profiles` 是每個 Account 一對一的 account-facing 顯示設定；`author_profiles` 是一個 Account 對零至多個作者 persona 的公開 attribution，含 immutable opaque `public_id`、可變 unique `slug`、display name、bio、avatar、status 與 owner Account reference。`books.owner_id` 保持 ownership，nullable `books.author_profile_id` 只負責公開 attribution，兩者不可互換。Ownership Transfer 不會因 owner 變更而自動改寫 attribution。

`backend/services/profile.py` 集中名稱／bio plain-text 邊界、slug collision suffix、作者頁查詢、public author summary 與頭像驗證。公開 card/detail/read、home、search、ranking、recommendation、history、library 共用 author summary；可靠 profile 可導向 `/authors/{slug}`，legacy/unmapped attribution 僅保留文字、不產生假連結。公開序列化不帶 `ownerId`、email、security state 或 login username；留言使用不具登入識別性的「讀者」標籤，管理 payload 才可保留受保護的 owner 欄位。

帳號可從 `/api/auth/profile` 讀寫自己的 Public Profile 與所屬 Author Profiles；authenticated `#/settings` 是主要個人設定入口，將 Account、Public Profile、Author Profile、email/security 與 linked identity 分區，舊 profile modal 僅作相容 fallback。`/api/auth/me` 僅增加 caller 自己的 profile summary 與不含機密的 Google availability。email 會 trim/casefold 並在無既存衝突後維持 case-insensitive unique；profile mutation 有 basic rate limit。頭像只接受 JPEG/PNG/WebP，輸入上限 2 MB、decoded 尺寸 64..1024，使用 server-generated isolated JPEG path，原始檔名與使用者路徑不會被採用。

帳號停用會在每次 request 失效既有 session、禁止登入與 mutation，但不會自動下架既有公開作品；公開作者狀態投影為 suspended。刪除是 soft-delete/tombstone：撤銷 authentication、保留 ownership/FK/audit/chapter/analysis/audio/favorite/history/generation references，公開作品繼續可見，作者頁顯示 tombstone。`scripts/migrate_identity_profiles.py` 預設 dry-run，僅 `--apply` 進行 additive/idempotent backfill；衝突保留 report，無法可靠對應的書維持 nullable `author_profile_id` 與 legacy text，不猜測 first admin。完整 ownership transfer workflow 已由獨立 child 落地，並以 source revision、active-generation guard、atomic owner CAS 與 append-only history 保護。

Authentication/OAuth 目前已落地 Account + nullable password 的 Option A：新 password registration 需要 email verification；legacy Account 的 NULL email 仍可用 username/password 登入，但沒有 email recovery。`external_identities` 以 issuer/provider + subject 為 canonical key；Google Authorization Code + PKCE S256 由 backend-mediated OIDC flow 驗證 state、nonce、issuer、signature、audience、expiry 與 subject。same-email 不會 silent auto-link：單一既有 Account 回傳 `EXISTING_ACCOUNT_REQUIRES_LINK`，多重 legacy conflict 回傳 `EMAIL_IDENTITY_CONFLICT`；新的 verified Google identity 可建立 `password_hash=NULL` 的 OAuth-only Account，internal username 由 issuer/subject deterministic 產生，不採用 display name。

Email change 走 pending email → verification token → uniqueness recheck → atomic promote；password reset/verification token 只以 digest、purpose、expiry、used timestamp 保存。password policy 統一為 8~256，合法 PBKDF2 legacy hash 僅在成功登入時 rehash。password reset/change、disable/delete 與 primary email replacement bump `session_version`；OAuth link/unlink 只 rotate current session，unlink 受 last-credential guard 保護。MailAdapter 具繁中 subject/plain/HTML template、fake/local queryable provider、development console/file sink 與 Microsoft Graph OAuth2 client-credentials adapter；Graph 只以 safe delivery code 對外，缺少設定時 production fail-closed 為 `NOT_CONFIGURED`。verification/reset delivery failure 不會把 token 當作送達；email change 會復原舊 pending target。`AUTHENTICATION-OAUTH VERIFIED` 與 `PRODUCTION AUTH CONFIGURED` 是分開的 rollout 狀態。

### 4.4 V4 執行期契約摘要（真源：`backend/v4_contracts.py`）

**Domain 名稱（final tables）**：`chapter_analyses`（`analysis_type='speaker'`；新 writer artifact schemaVersion 4，v3 僅 compatibility reader）、`character_registry`／`character_aliases`／`character_resolution_events`（book-level identity view）、`audio_generations`（mode `single|multi`，emotion_policy `best_effort`）、`ai_providers`（type `openai|openai_compatible|deepseek`）、`tts_providers`（adapter_key `generic_http|openai_compatible|cosyvoice_http`）。AI 與 TTS provider 是獨立 domain，不共用 `providers` super-table。`chapters.seq` 是顯示順序，`chapter_key` 才是穩定 identity。作者「下一步」是 derived read model。

**Emotion registry**：`neutral, happy, sad, angry, afraid, surprised, calm, excited, tender`；canonical 每 segment `{label, intensity, source}`，`source ∈ {ai, fallback, manual}`；缺 emotion → `neutral` + source `fallback`。compatibility projection 才提供 legacy `value`。

**版本欄位**：`books.audio_settings_version`（任何改變輸出的設定都 increment）、`ai_providers.config_version`、`tts_providers.config_version`、`chapter_analyses.schema_version=4`（v3 僅由相容讀取層支援）。`chapter_analyses.progress_json` 是執行期進度 read model，與 canonical artifact 分離；`chapter_analyses.usage_metrics_json`、`generation_jobs.usage_metrics_json` 與 `analysis_partials.usage_metrics_json` 保存 provider-neutral、bounded 的累計 usage，不保存 prompt、小說全文、raw response 或 credential。

**分析進度**：分析 worker 依實際 chunk callback 持久化 `totalChunks`、`completedChunks`、`runningChunks`、`retryCount`、`currentStage`、`progressPercent` 與安全的 `lastError`；不使用時間估算。`currentStage` 可為 `queued`、`analyzing`、`normalizing`、`validating`、`saving`、`deriving_roster`、`ready`、`failed`。分析 API 與 owner workflow 只回傳這個安全 progress read model，provider raw error、credential 與完整回應留在 backend。

**Source-faithful rollout**：schemaVersion 4 的 source-faithful path 使用 provider-neutral structured annotation；`structured_capability.py` 以 bounded synthetic probe 建立 provider/model snapshot，結果分為 `supported`、`unsupported`、`invalid_schema`、`probe_timeout`、`provider_error`、`unknown`。正式 analyzer 只讀有效 snapshot，不在每章即時 probe；supported 使用 strict schema，其餘依 best-effort policy fallback 至 `json_object`/legacy。supported/explicit unsupported 可短期快取，transient probe 狀態不永久視為 unsupported；provider/model/config/schema/probe version 改變會使 snapshot stale。structured schema/mode/hash 與 segmentation/span identity 進入 partial cache identity。v4 可由 `source_faithful_migration.py` 逐章 dry-run、寫入 shadow artifact、驗證後 promote，或 rollback 至保留的 v3 artifact；shadow/failure 不切換 active pointer。歷史 benchmark（json_object 6000/4000/3000 與 strict schema 3000）僅作 acceptance evidence，不是 runtime threshold。

**AI request profile**：`ai_request_profile.py` 集中 provider/model 的 chat-completions transport 差異（completion token 欄位、temperature policy 與 structured-output probe eligibility）。capability probe、正式 analysis、JSON/reference repair 共用同一 versioned profile；profile version/hash 同時使 structured capability snapshot 與 partial cache identity 失效，避免 probe 與 production 使用不同參數。profile 只宣告 transport/probe eligibility，實際 strict 支援仍以 capability probe 為準。模型不支援的 request parameter 屬 deterministic、non-job-retryable failure。

Structured annotation coverage 使用版本化 `annotation-coverage-v1` policy：大型 annotation group 覆蓋率低於 0.8 直接產生 `annotation_coverage_low`，不進 targeted completion；達標但未完整時才允許一次 bounded completion。failure diagnostics 以 bounded structured JSON 保存，不截斷整段 JSON。

**一致性規則**：single generation 不得要求 `analysis_id`；multi 必須有 ready 且 text hash 相符的 analysis；`generation_key` 由所有實質影響輸出的 inputs 決定，包含 character registry revision（同 key 重用 ready/pending）；generation 完成前需重新確認 registry revision，stale generation 不得成為 active；`active_audio_generation_id` 只在輸出驗證通過後切換；章節文字變更 → 舊 analysis/generation 依 derivation 變 stale（不 delete rows）；default AI provider 為 0 或 1 個，0 個時 app 仍可運作、AI 功能明確回報 unavailable。

**關鍵 API**：
```
POST/GET /api/books/{bid}/chapters/{seq}/analysis      # POST 只 enqueue job；回傳 {analysisId, jobId, status}
POST /api/books/{bid}/analysis/batch
POST/GET /api/books/{bid}/chapters/{seq}/audio-generations
GET  /api/books/{bid}/chapters/{seq}/audio-generations/{generation_id}
POST /api/books/{bid}/audio-generations/batch
GET/POST/PUT/DELETE /api/admin/ai/providers[/{id}]      # admin-only；secret 永不回傳
POST /api/admin/ai/providers/{id}/test
GET/POST/PUT/DELETE /api/admin/tts/providers[/{id}]
POST /api/admin/tts/providers/{id}/test
POST /api/admin/tts/providers/{id}/capabilities/refresh
POST/GET /api/books/{bid}/requests                    # Author typed request + own history
GET /api/requests/{id}                                # requester-scoped detail/events
GET /api/review/requests                               # Reviewer/Admin paginated queue
POST /api/review/requests/{id}/start|approve|reject    # typed atomic transitions
POST /api/review/requests/{id}/generation              # approved audiobook follow-up
POST /api/admin/books/{bid}/emergency-hide             # Admin/Super Admin reasoned hide
```
- 生成 API 接受 product intent（mode/voiceId/emotionPolicy），不接受 provider-native payload。
- AI/TTS provider 管理員 API 全部 admin-only；state-changing 需 CSRF；secret 永不回傳前端。

### 4.4.1 Admin Console vNext（目前已實作範圍）

Admin 入口仍是 `#/admin`；Overview 由 `frontend/admin/admin-console.js` lazy-load `/api/admin/overview`，其餘 domain view 依 tab 請求 bounded API。`backend/services/admin_console.py` 從 `users`、`books`、`categories`、`banners`、`reports`、`audit_logs`、content requests 與 generation operation/job/attempt/provider/worker canonical truth 推導 safe read models；不回傳章節全文、attempt raw payload、OAuth subject、password hash、token 或 provider secret。

目前 Admin-local list contract 統一使用 `page`、bounded `page_size`、`total`、`total_pages`、`has_next`、whitelist sort 與 stable tie-breaker。Users、Books、Categories、Banners、Reports、Audit、Generation 與 AI/TTS service views 以 backend filter-before-page；Book row 分開呈現 owner Account、Author Profile attribution、publication、request 與 generation state。AI 與 TTS 維持分域，provider projection 另外呈現 capacity、active/available、queue、health/config validity；provider config mutation 以 config version conflict guard 保護。

Admin Overview 顯示帳號、公開作品、作者申請、content requests、AI/TTS queue、provider 與 worker/stale 摘要及 safe action alerts；舊 `/api/admin/dashboard` 保留相容。Reviewer UI 只呈現 shared Review/Generation tabs，但 backend capability/API deny 仍是 canonical boundary。正常 Admin UI 不提供 legacy restore/bypass control；emergency hide 仍要求既有 reason、CSRF、scope 與 audit。

R18 Audit Governance v1 已落地：`backend/services/audit.py` 是新 canonical audit event 的唯一 writer boundary，將 action 驗證、event class、policy version、bounded secret redaction 與 opaque deleted-account actor projection 集中處理；`audit_logs` 另有 SQLite BEFORE UPDATE/DELETE triggers，修正只能追加 `audit_event_corrected` lineage。`GET /api/admin/audit-logs` 與 detail 維持 bounded server-side filter/sort/pagination；只有 Super Admin 可執行有日期、列數、格式與 bytes 上限的 JSON/CSV export，export request/outcome 也會以 safe metadata 稽核。R18 v1 不啟用 retention expiry、archive mover、purge/delete UI 或 cron；domain event history、generation attempts、notification history 仍由各自 domain 擁有。這些是目前已實作的 enforcement，不代表 jurisdiction-specific retention、backup/release hardening 或 future purge policy 已完成。

### 4.5 遠端 TTS Provider 契約

管理員在後台設定：`Base URL`、`Voices path`、`Synthesis path`、`Auth scheme`（`none|bearer|x-api-key`）、`API key`（只存後台，Fernet 加密，不回傳前端）。`cosyvoice_http` adapter 依 `docs/tts-provider-api-v1.md` 呼叫六個 `/v1` endpoint；前端只讀 backend catalog/capability，永不直連 TTS service。

- `GET /voices`：legacy provider 相容端點；Provider API v1 使用 `/v1/voices`，平台快取 voice-level expressive capability。catalog refresh 不刪除已選 voice；消失 voice 進入 unavailable/stale，不自動換聲。
- `POST /synthesize`：legacy provider 相容格式；Provider API v1 使用 binary audio response，requested/applied expression、fallback/ignored、duration 與 request metadata 依 integration contract。
- **安全**：URL 必須 HTTPS（本機測試才可 HTTP）；生成前先確認 provider 在線；API key 不得進前端/Git/log；provider 需求不同欄位時在 `tts_provider.py`/`tts_adapter.py` 加 adapter，不要散落改前端生成流程。
- **`/capabilities`（選用）**：`GET /capabilities` 404 = 「不支援 capabilities」（`capabilities_status=unsupported`），**不代表 provider failure**；`refresh_capabilities` 在端點缺失時不覆寫 `/voices` 健康狀態。UI 呈現「連線狀態：可用 · 不支援 capabilities」。能力探測失敗（5xx/network）才視為 provider error。

## 5. 認證與安全模型

| 機制 | 位置 | 說明 |
|---|---|---|
| Session | `backend/auth.py` | HMAC 簽名 cookie `novel_session`（含 auth_time/session_version），金鑰 `data/.session_key` |
| Account lifecycle | `backend/security.py`、`backend/services/profile.py` | 每次 authenticated request 檢查 `account_status`；disabled session fail-closed，deleted account 以 tombstone 保留公開 attribution |
| CSRF | `security.py` | cookie `novel_csrf` + header `X-CSRF-Token`；state-changing 需帶 |
| 密碼 | `security.py` | PBKDF2-HMAC-SHA256；8~256 policy、legacy hash successful-login rehash |
| Email / recovery | `routers/auth.py`、`services/auth_tokens.py` | verification/pending email、generic recovery、digest-only one-time token |
| OAuth / identity | `routers/auth.py`、`services/oauth.py`、`db.py` | Google OIDC code+PKCE、fake provider、external identity link/unlink |
| Mail | `services/mail.py` | fake/local queryable adapter、繁中 auth templates、development console/file sink、Microsoft Graph readiness、production NOT_CONFIGURED |
| 角色 | reader/author/reviewer/admin/super_admin | `services/policy.py` explicit capability helpers；`security.py` current Account role/status |
| Dev bypass | `settings` + `auth.py` | 僅 `DEV_AUTH_BYPASS=true` 且非 production 且 DB 無使用者時啟用 |
| CORS | `main.py` | `PUBLIC_ORIGINS` 白名單，禁 `*` |
| Cookie | production | `Secure`、`HttpOnly`、`SameSite` |
| 遠端 TTS/AI 安全 | `services/tts_provider.py`、`services/ai_provider.py` | 僅 HTTPS、SSRF 防護（netsec）、機密加密（Fernet）、不洩 API key |
| Secret boundary | `public_provider()`、auth services | AI/TTS secret 與 auth token/credential 不進前端/log/audit；provider GET API 永不回傳機密明文（`hasSecret` 布林） |

## 6. 已知高耦合 / 易出 Bug 區域（重要）

1. **前端雙軌並存**：`app.js`（~2.9k 行，含書籍/章節/語者/播放器/後台）與 `platform.js`（公開平台）各自維護 state/toast/esc。兩者都委派 `NovelApi.request`，但 UI 邏輯重複，改一處常需同步另一處；`app.js` 會先正規化 hash query 再把公開 route 交給 `platform.js`，政策 canonical route 為 `#/policy/privacy`，並保留 `#/privacy` 別名。
2. **process-local 狀態**：`backend/state.py` 的鎖與 `routers/auth.py:99` 的登入限流是記憶體版，多副本/Restart 即失效。多副本前必須改 Redis sliding window。
3. **章節權限檢查（改動時勿破壞）**：`GET /books/{bid}/chapters/{seq}` 與 `GET /books/{bid}/audio/{seq}` 統一經 `book_service.can_view_chapter`（`book_service.py`）檢查整書可見性＋章節 `publish_status`。曾有 P0 缺口只查整書，後已補上。
4. **前端試聽一律走 NovelApi**：`previewVoice` 與發音 popup 皆經 `NovelApi.request("/api/preview-tts", ...)`，自動帶 CSRF header。勿改回裸 fetch（production 下 403）。
5. **生成任務**：必須走 DB 佇列（jobs.py），不可改回 FastAPI BackgroundTasks（會因重啟/多進程丟失任務）。
6. **rate limit**：preview-tts 每分鐘 3 次、單次 3000 字上限是單進程記憶體版。
7. **後台 tab 渲染 race**：`renderAdminPanel` 各 branch 是 async；除 `state.adminTab` 守衛外，AI/TTS provider renderer 使用 per-domain render token，防止較舊的 refresh 回應覆寫較新的 provider list；新增 async tab 時須保留 stale-response guard。
8. **`generation_key` 重用**：同輸入重生成會直接重用既有 ready generation（不重新合成）——這是平台預期行為（避免重複消費），不是 bug。
9. **後台大量資料**：生成任務／稽核紀錄／作者申請／使用者／書籍／分類／輪播／檢舉／generation operations 皆為 bounded server-side 分頁（`page`/`page_size`/`total`/`total_pages`）；任務清理僅限 terminal 狀態（success/failed/cancelled）且不刪音訊產物；稽核紀錄不提供刪除（保留追溯性）；使用者支援 username/email 搜尋與角色/狀態篩選，`last_login_at` 於成功登入時更新。
10. **書級名冊 projection**：`analysis_executor` 僅在 validated ready artifact 且 source hash 相符後，將 canonical `character_id`／`speaker_id` 投影至既有 name-keyed `books.voices`／`speaker_info`／`speaker_chapters`；`/voices/ai-match` 依賴此 compatibility projection。projection 失敗不重新呼叫 AI，可由 `retry_roster_projection` 從 ready artifact 重試。既有書可用 `python -m scripts.backfill_roster`（預設 dry-run；`--apply` 才寫入）從 ready 且 hash 相符的 artifact 重建名冊；v4 canonical identity 永不使用 substring merge，legacy backfill compatibility layer 才保留舊 heuristic。
10. **書級名冊與 identity projection**：`analysis_executor` 僅在 validated ready artifact 且 source hash 相符後，先寫入 book-level `character_registry`／segment resolution，再將 accepted `character_id`／`speaker_id` 投影至既有 name-keyed `books.voices`／`speaker_info`／`speaker_chapters`；`/voices/ai-match` 依賴此 compatibility projection。projection 失敗不重新呼叫 AI，可由 `retry_roster_projection` 從 ready artifact 重試。AI merge 需經 proposal、threshold、deterministic safety checks 與 revision check；merge/split 不改寫 immutable artifact。既有書可用 `python -m scripts.backfill_roster` 或 `python -m scripts.migrate_character_registry --bid BID`（預設 dry-run；`--apply` 才寫入）逐書處理；v4 canonical identity 永不使用 substring merge，legacy backfill compatibility layer 才保留舊 heuristic。player/TTS 仍走 compatibility projection，stable-ID-native TTS 未完成。
11. **章節 filter 語意**：「已分析」含分析完成的所有狀態（`needs_voice_configuration`／`ready_to_generate`／`audio_generating`／`audio_ready`／`audio_stale`），與「可播放」重疊——可播放章必定已分析，是設計意圖，改 filter 時勿退回「已分析不含可播放」。
12. **`#/detail/{id}` 只給 manager**：非 manager（讀者／訪客）一律導向公開作品頁 `#/book/{id}`（platform 視圖）；作者操作 API 有 owner+role 檢查，reader 呼叫 403。改動時勿讓 reader 繞過此導向看到作者章節管理 UI。

## 7. 部署與維運

### 7.1 開發環境

1. 安裝 `requirements.txt`；複製 `.env.example` 為 `.env`，`APP_ENV=development`。
2. 啟動 `python run.py`；`/api/health/live`（liveness）與 `/api/health/ready`（DB/worker/TTS，失敗 503）。
3. Dev DB 重設（destructive，只限 development）：刪 `data/app.db*` + `storage/books` + `storage/preview_cache` 後重啟 → 自動 fresh schema + seed 類別 + admin bootstrap。保留 `.env`、`data/.session_key`、`data/.ai_secret_key`、`data/.tts_secret_key`。

### 7.2 正式環境

1. `APP_ENV=production`；`ADMIN_USER` 設定首次管理員帳號（預設 `admin`）、`ADMIN_PASSWORD` 非空（空值拒啟動）；固定 `SESSION_KEY_FILE`；設定實際 `PUBLIC_ORIGINS`；HTTPS reverse proxy（`deploy/server/` Caddyfile 或 nginx → `127.0.0.1:8000`）。
2. Authentication/OAuth production enable 是獨立 gate：需 Google client/secret、exact redirect URI、production origin、正式 mail provider、sender domain、Microsoft Graph tenant/app permission/admin consent/mailbox/secret injection、proxy security 與 staging UAT；未配置 mail 不可被視為寄送成功。本 change 不含 production credentials 或 deploy。
3. Cookie Secure/HttpOnly/SameSite；FFmpeg 已安裝；正式 TTS 以遠端 provider 為主（無 GPU 主機不安裝 F5 模型/參考音檔）。
4. 若同一 DB 只允許一個 queue consumer，其他 web instance 設定 `WORKER_ENABLED=false`；確認 `/api/health/ready`
回報 worker identity/version，且 enabled worker 的 checks 全 true。
5. 用 `TTS_SECRET_KEY`/`AI_SECRET_KEY` 或受保護的 `data/.{tts,ai}_secret_key` 保存 provider credentials；不把 API key 放前端。
6. 無 GPU 正式環境用 `docker-compose.production.yml`（啟用 Redis、關閉 Edge/F5、data/storage/logs 掛主機）。
7. 備份 `data/app.db`（含 WAL/SHM）與 `storage/books`。

### 7.3 Split deployment（網站 + 獨立 TTS 服務）

網站與 TTS 引擎是獨立服務；網站只呼叫遠端 TTS API。TTS host（如 `<workspace-root>\TTS` F5/Kokoro）可經 Cloudflare Tunnel 暴露：

```
Novel platform :8000 → HTTPS + X-API-Key → Cloudflare Tunnel (tts-api.example.com) → TTS host :8100
```

- TTS 服務需提供穩定的 Provider API v1：`GET /v1/health`、`GET /v1/ready`、`GET /v1/version`、`GET /v1/voices`、`GET /v1/capabilities`、`POST /v1/synthesize`；除 health 外以 API key / Cloudflare Access / IP allowlist 保護，不要直接暴露 8100 port。
- 網站後台新增 provider：Base URL（tunnel 網址）、`cosyvoice_http`、X-API-Key、與 TTS 服務相同的 key。驗證順序：`/v1/health` → `/v1/version` → `/v1/voices` → `/v1/capabilities` → 後台測試連線 → 確認作者可見 voices → 先生成短章節再批次。
- 部署順序：先 TTS 服務，再網站 SERVER 包，最後管理員新增 provider 並測試。

### 7.4 備份／還原

- 工具：`scripts/backup.py`；`python -m scripts.backup backup [--output-dir DIR] [--keep N]` / `list` / `restore ARCHIVE [--dry-run]`。
- 用 SQLite online backup API 取一致快照；還原前自動建立緊急備份；restore 只允許 zip manifest 記錄的原始佈局（防路徑穿越）。
- 正式環境以 cron/Task Scheduler 排程 `backup`，`--keep` 控制保留份數；建議定期執行還原演練。

## 8. 已知技術限制（現況，非設計缺陷）

1. SQLite + 單機 Worker 適合 Beta；多副本前應遷移 PostgreSQL。
2. rate limit 為單進程記憶體版，多副本部署前應改 Redis sliding window。
3. PWA 快取只處理靜態資源，不快取需要權限的 API 與音訊。
4. sitemap 使用 SPA 公開路由；完整 SEO 需 history route 或 server-side prerender。
5. 前端 reader continuity 已由 `services/reader.js` 的 bounded helper 與 `platform.js` public controller 實作；`app.js` management player 仍維持獨立 state，兩者不共用 mutable playback controller。
