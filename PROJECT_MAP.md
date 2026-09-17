# 專案地圖 PROJECT_MAP

> 本文件是理解「小說朗讀 v4」功能位置的唯一入口地圖。想了解任何功能在哪裡、如何測試、如何部署，先看這裡。
> 更深入的資料流與模組邊界請看 `ARCHITECTURE.md`；開發規則請看 `AGENTS.md`。

## 1. 專案簡介

中文小說閱讀 + AI 有聲朗讀平台，產品名「語閱 StoryLingo」。

> **V4 RELEASE READY**；legacy generation caller = 0（作者生成流程全走 V4 canonical endpoints，無 legacy analyze/tts caller）。

- **核心功能**：上傳小說 → AI 分析章節（語者拆分、單字、雙語段落）→ 為每位語者生成 TTS 音訊 → 逐句同步播放。
- **公開平台**：首頁 / 搜尋 / 分類 / 排行 / 書籍詳情 / 純文字閱讀器 / 聽書跟讀 / 書架 / 公告 popup / 通知。
- **角色**：reader（讀者）、author（作者）、reviewer（Reviewer）、admin（管理員）、super_admin（Super Admin）。
- **部署模型**：無 GPU。TTS 靠遠端 provider（HTTP API），AI 分析用 OpenAI 相容 API（NVIDIA NIM 或 OpenAI）。

## 2. 技術棧

| 層 | 技術 |
|---|---|
| 後端 | Python 3.11 + FastAPI + Uvicorn（`run.py` 單入口） |
| 資料庫 | SQLite（`data/app.db`；`backend/db.py` 為真源） |
| 生成佇列 | 單一 canonical `generation_jobs` 表 + AI/TTS typed consumers、provider capacity、lease/fencing、attempt history（`backend/db.py`、`backend/jobs.py`） |
| AI 分析 | OpenAI 相容 API；NVIDIA NIM（deepseek-v4-flash / -pro）或 OpenAI（gpt-4o-mini） |
| TTS | 遠端 TTS provider（`tts_provider.py` + `tts_provider_v1.py` + `tts_adapter.py`）+ ffmpeg；provider-neutral API v1 |
| 前端 | 原生 JS SPA（無框架）：`app.js`（作品管理/播放器/後台）+ `platform.js`（公開平台） |
| 測試 | pytest（後端）、Vitest（前端單元）、Playwright（E2E，桌面+行動 Chromium） |
| 部署 | Docker（`docker-compose.production.yml`）、`deploy/server/`（Caddy/nginx 反向代理） |

## 3. 目錄地圖

```
<project-root>\
├─ run.py                      # 啟動入口（python run.py）
├─ requirements.txt / package.json
├─ .env.example                # 環境變數範本（複製為 .env）
├─ Dockerfile / docker-compose.production.yml
├─ playwright.config.cjs / vitest.config.js
├─ AGENTS.md / PROJECT_MAP.md / ARCHITECTURE.md   # 三份核心文件（唯一 current 文件）
├─ docs/LUNA_HANDOFF.md         # 使用者指定的 Luna 有界小單／驗收／停止條件；現況仍以架構與稽核為準
├─ backend\                    # FastAPI 應用
│  ├─ main.py                  # app 建立、lifespan、健康檢查、靜態檔
│  ├─ settings.py              # 環境變數 + 路徑設定
│  ├─ db.py                    # SQLite schema/migration、所有資料表（真源）
│  ├─ v4_contracts.py          # V4 執行期契約真源（domain 名稱、enum、API 契約）
│  ├─ jobs.py                  # 持久化 AI/TTS typed consumers、lease/heartbeat、retry/recovery worker
│  ├─ analyzer.py / tts.py / voices.py / splitter.py / replacements.py
│  ├─ storage.py               # storage/books/{bid} 檔案產物讀寫
│  ├─ auth.py / security.py    # session、CSRF、密碼、限流
│  ├─ state.py / exceptions.py
│  ├─ routers\                 # books.py voice.py auth.py platform.py admin.py announcements.py content_requests.py ownership_transfer.py character_resolution.py
│  ├─ services\                # policy.py audit.py notifications.py announcements.py content_requests.py book_service profile chapter_service admin_console tts_provider tts_adapter ai_provider analysis analysis_executor source_faithful structured_output source_faithful_migration character_resolution audio_generation generation_orchestration generation_pipeline emotion workflow distributed netsec category migration
│  ├─ pagination.py             # bounded page parser + canonical collection envelope
│  └─ tests\                   # pytest（conftest.py 用 tempdir 隔離 DB）
├─ frontend\
│  ├─ index.html / style.css
│  ├─ app.js                   # 書架/章節/語者/播放器/後台（作者生成流程走 V4 canonical endpoints）
│  ├─ platform.js              # 公開平台 SPA（home/search/category/rankings/book/read/shelf/notifications；Reader controller/auto-next/progress）
│  ├─ services\api.js          # NovelApi 統一 fetch/CSRF/錯誤映射（唯一 API 入口）
│  ├─ services\public-navigation.js # public shell active route、Mobile 主入口／單層工作區、Desktop disclosure、Reader shell 隔離與 Escape（不授權）
│  ├─ services\notifications.js # shared Account-scoped notification coordinator、badge/popover/center state、principal 與 route-intent fencing
│  ├─ services\bootstrap.js    # 初始 route visibility contract：BOOTSTRAPPING → ROUTE_RESOLVED → VIEW_VISIBLE
│  ├─ services\reader.js      # public Reader bounded mode/progress/settings/auto-next/capability helpers
│  ├─ services\toast.js / escape.js / workflow.js   # workflow.js 已載入，驅動作者 UI next-step
│  ├─ components\              # Native JS foundation: Button/IconButton/Field/Modal/ConfirmDialog/Status/PageHeader/Filter/Pagination/Toast/BookCard（window.NovelUI 暴露）
│  ├─ components\__tests__ / services\__tests__     # Vitest
│  ├─ e2e\                     # Playwright：initial-loading-fix/platform/reader-audiobook-ux/components/author-apply/author-create/author-generate-single/multi/provider-unavailable/publish-journey/roles-review-workflow/generation-orchestration/creation-mobile/admin-console-vnext/admin-ops/admin-ai-provider/admin-tts-provider/audit-governance/player-canonical-analysis
│  ├─ sw.js / seo.js / manifest.webmanifest / components-preview.html
├─ migrations\v3.py            # storage → SQLite 遷移 CLI
├─ scripts\backup.py           # 資料庫備份/還原
├─ scripts\migrate_identity_profiles.py # Identity/Profile dry-run 或明確 apply 遷移
├─ scripts\auth_inventory.py      # authentication read-only inventory（不修改 DB）
├─ scripts\uat_live_tts.mjs    # 真實本地 TTS Live UAT（Playwright；需 UAT_ADMIN_PASSWORD / UAT_TTS_API_KEY 環境變數）
├─ deploy\server\              # 正式 Server 部署包（Dockerfile/docker-compose/Caddyfile/README）
├─ data\ storage\ uploads\ logs\ backups\   # 執行期資料（勿進 git）
└─ .opencode\skills\           # opencode skills（含 node_modules，勿動）
```

## 4. 核心模組與職責

| 檔案 | 職責 | 備註 |
|---|---|---|
| `backend/db.py` | SQLite schema、可重跑 migration、所有表存取 | **書籍/章節/使用者等資料的真源** |
| `backend/pagination.py` | platform normalized collection 的 page/page_size bounds 與 canonical envelope | 不持有 domain query；各 service/DB path 自行負責 authorization、filter、sort、count、page |
| `backend/v4_contracts.py` | V4 執行期契約真源（domain 名稱、enum、API 契約） | 見 ARCHITECTURE §4.4 |
| `backend/main.py` | app 組裝、lifespan（logging/validate_env/init_db/orphan-analysis recovery/requeue_stale_jobs/recover_stuck_states/start_worker）、`/api/health`（live/ready）、SPA/API cache contract | release header 與 cache policy 不取代 route authorization |
| `backend/settings.py` | 讀 `.env`、解析路徑 | 測試會改 `settings.ROOT_DIR` 隔離 |
| `backend/jobs.py` | 持久化任務佇列 + worker identity/build isolation、單機 thread worker（MAX_ATTEMPTS=3） | `WORKER_ENABLED=false` 可 web-only；重啟後可復原 |
| `backend/services/generation_orchestration.py` | operation/job 建立、typed service、provider snapshot、dependency、retry/backoff 與安全 projection | 不持有 provider execution transaction |
| `backend/services/admin_console.py` | Admin bounded overview/list read models、filter-before-page、stable sort、grouped projection 與 safe provider/job/attempt/audit DTO | 不建立第二套業務真源；不回傳 secrets/full text/raw payload |
| `backend/services/audit.py` | R18 canonical audit writer、allowlisted action/event class、secret redaction、policy metadata、append-only correction lineage | 新 audit row 只經此 writer；不混入 domain event、generation attempt 或 notification truth；v1 不提供 purge/archive |
| `backend/services/notifications.py` | backend-owned event allowlist、Account recipient、dedupe、immutable snapshot、bounded private list/count/read 與 legacy adapter | 不提供 generic create、role inbox、broadcast 或 external delivery；source target 仍於 click time 授權 |
| `backend/services/announcements.py` | 公告 aggregate validation、UTC/audience filtering、version/archive、acknowledgement | plain text/CTA allowlist、bounded response、no secret |
| `backend/analyzer.py` | source-faithful schemaVersion 4 segmentation/structured annotation；v3 text-return path 僅供 compatibility | NVIDIA NIM 或 OpenAI |
| `backend/tts.py` | 依句界拆分 bounded TTS units、provider-aware concurrency、private partial reuse + ffmpeg 合併 | 正式以遠端 provider 為主；未知容量保守串行 |
| `backend/services/tts_provider.py` | 遠端 TTS provider（SSRF 防護、Fernet secret、legacy client 與 v1 routing） | API key 僅 backend |
| `backend/services/tts_provider_v1.py` | Provider API v1 binary client、expression/error/duration/request metadata | 不依賴 CosyVoice package/CUDA |
| `backend/services/tts_adapter.py` | V4 adapter_key + capabilities 快取/emotion 支援偵測 | 含 `cosyvoice_http`、voice-level capability |
| `backend/services/ai_provider.py` | V4 AI provider（Fernet 機密、單一 default、SSRF 驗證） | 後台「AI 服務」tab 管理 |
| `backend/services/ai_request_profile.py` | provider/model-aware chat-completions request profile | probe 與 analyzer 共用 token/temperature transport 規則 |
| `backend/services/analysis.py` | schemaVersion 4 ready persistence/驗證、v3 compatibility reader、stable character identity、emotion fallback、ready artifact 與 roster compatibility projection；legacy writer 不可降版 v4 | |
| `backend/services/analysis_version_policy.py` | analysis artifact namespace 的 v1/v2 legacy、v3 compatibility、v4 native registry 與 fail-closed/exact execution gates | 不管理 structured-output、usage-metrics 或 provider transport schema |
| `backend/services/profile.py` | Public Profile／Author Profile validation、sanitized serialization、作者頁查詢與隔離頭像儲存 | 不暴露 Account security identity |
| `backend/services/analysis_executor.py` | V4 analyzer job：綁定 AI provider、source hash pointer | |
| `backend/services/usage_metrics.py` | provider-neutral bounded token/request usage aggregate | 不保存 prompt、小說全文、raw response 或 credential |
| `backend/services/source_faithful.py` | schemaVersion 4 deterministic source spans、coverage validator、annotation assembly | canonical text 只由 source slice 建立 |
| `backend/services/structured_output.py` | provider-neutral structured annotation capability、schema、fallback、reference validation | provider-native mapping 不進 canonical artifact |
| `backend/services/source_faithful_migration.py` | v3/v4 逐章 dry-run、shadow artifact、promote/rollback helpers | 不 destructive rewrite active v3 artifact |
| `backend/services/character_resolution.py` | book-level character registry、alias/candidate、reconciliation、merge/split、voice conflict、逐書 migration | immutable ready artifact 之上的 identity view；append-only events |
| `backend/routers/character_resolution.py` | identity candidates、correction、voice conflict、history 與 migration API | reader 禁止；author 限可管理書；admin migration |
| `backend/services/audio_generation.py` | V4 generation domain：deterministic key、active pointer | |
| `backend/services/generation_pipeline.py` | V4 audio_single/multi/all jobs、輸出驗證後 atomic 啟動 | |
| `backend/tests/test_generation_orchestration.py` | claim/capacity/lease/fencing/retry/cancel/revision/dependency/migration/secret-boundary race tests | 使用 deterministic fake providers |
| `frontend/e2e/generation-orchestration.spec.js` | Reviewer/Admin operation surface、AI/TTS status separation、mobile overflow regression | mocked browser-first UAT |
| `backend/services/workflow.py` | V4 derived author workflow state | 前端 `services/workflow.js` 呈現 next-step |
| `backend/services/distributed.py` | Redis 分散式鎖；無 Redis 時 fallback process-local | |
| `backend/storage.py` | `storage/books/{bid}` 內 analyze json/audio/timing/cover | 生成產物，非真源 |
| `backend/auth.py` + `security.py` | HMAC session cookie、CSRF cookie、PBKDF2、登入限流、recent-auth/session revocation | |
| `backend/services/auth_inventory.py` | authentication read-only inventory、checksum 與 nullable password/legacy email conflict report | |
| `backend/services/auth_tokens.py` + `mail.py` | email verification、password recovery、MailAdapter/fake/local/console/file/Microsoft Graph boundary | raw token 僅進 mail boundary；DB 只存 digest；Graph production 仍需獨立 configuration gate |
| `backend/services/oauth.py` | Google OIDC Authorization Code + PKCE、JWKS validation、fake provider、safe redirect | provider subject 是 external identity key；不接 production credential |
| `backend/routers/books.py` | 書籍/章節 CRUD、V4 canonical analysis/audio-generations/單章與批次 cancel、語者、publish | 權限檢查核心 |
| `backend/routers/content_requests.py` | Author request、Reviewer/Admin queue/detail/decision/generation、Admin emergency hide | typed request API、CSRF/auth/IDOR/capability boundary |
| `backend/routers/ownership_transfer.py` | owner transfer request、target acceptance、Reviewer/Admin review、Super Admin emergency transfer | exact Account actor checks、CSRF、IDOR、recent-auth、active-generation guard由 service 保護 |
| `backend/routers/platform.py` | 公開 API：home/banners/genres/rankings/search/audiobooks/read/me/library/progress/history/comments；current Account private notifications list/count/read | audiobook response `no-store`；通知 response `private, no-store`；mutation 經 auth/current Account/CSRF |
| `backend/routers/admin.py` | 管理：overview、bounded users/books/categories/banners/reports/audit、審核、TTS/AI provider、job/generation operations、作者申請、檢舉 | current capability、CSRF、safe DTO；Reviewer Admin-only API deny |
| `backend/routers/announcements.py` | 公告 active delivery、authenticated acknowledgement、Admin CRUD/lifecycle | current Account audience、CSRF、private/no-store、safe DTO；不建立 notification |
| `backend/services/policy.py` | 固定五角色 explicit capability registry、current Account、scope/ownership checks | backend canonical security boundary，不使用 numeric hierarchy |
| `backend/services/content_requests.py` | request aggregate、append-only events、state machine、revision、publication/audiobook/emergency transitions | SQLite atomic transaction + expected-state guard |
| `backend/services/ownership_transfer.py` | ownership transfer aggregate、revision、expiry、CAS、immutable event、relationship-preserving owner transition | 只改 `books.owner_id`；反向移交用新 request，不提供 destructive undo |
| `scripts/bootstrap_super_admin.py` | exact existing Account 的非 public Super Admin bootstrap | idempotent、auditable、production fail-closed |

## 5. 快速定位表（功能 → 檔案:行號）

### 後端 API

| 功能 | 位置 |
|---|---|
| 註冊/登入/logout/csrf/me、email verification/recovery、password、Google OAuth/link/unlink | `backend/routers/auth.py` |
| 書籍 CRUD / 章節 / publish / 語者 / stats | `backend/routers/books.py` |
| V4 canonical：章節 analysis、audio-generations、batch、書籍音訊設定（audioMode/defaultVoiceId） | `backend/routers/books.py` |
| 公開首頁/banners/genres/rankings/search/read/comments | `backend/routers/platform.py` |
| 管理後台（overview、審核/作品/分類/banner/TTS/AI providers/jobs/generation/audit/作者申請/檢舉/使用者） | `backend/routers/admin.py`、`backend/services/admin_console.py` |
| R18 稽核瀏覽/詳情/受限匯出 | `backend/routers/admin.py`、`backend/services/admin_console.py`、`backend/services/audit.py`、`backend/tests/test_audit_governance.py` |
| 內容申請/審核/生成 follow-up/emergency hide | `backend/routers/content_requests.py`、`backend/services/content_requests.py` |
| Ownership Transfer request/accept/review/emergency | `backend/routers/ownership_transfer.py`、`backend/services/ownership_transfer.py` |
| 語者清單 / preview-tts | `backend/routers/voice.py:43` `:54` |
| 健康檢查 live/ready | `backend/main.py:82` `:87` `:92` |

### 前端

| 功能 | 位置 |
|---|---|
| 公開平台路由（home/search/category/rankings/audiobooks/book/read/shelf/notifications/author） | `frontend/platform.js`（platformRoute） |
| Public Experience UI shell / collection convergence | `frontend/index.html`、`frontend/platform.js`、`frontend/services/public-navigation.js`、`frontend/style.css`、`frontend/e2e/public-experience-convergence.spec.js`；public Editorial Ledger first wave與Book Detail single-h1 evidence見 `openspec/changes/platform-ux-modernization/` |
| 閱讀器（純文字＋學習模式＋音訊、progress、auto-next、bookmark、fullscreen） | `frontend/platform.js`（renderPublicReader/controller）、`frontend/services/reader.js` |
| 作品管理書架 / 我的創作（作者工作台） | `frontend/app.js` |
| 章節管理 / 語者聲線面板 | `frontend/app.js`（renderChapters / renderBookVoicesPanel） |
| 長文編輯、分頁內暫存、版本衝突與晚回應隔離 | `frontend/app.js`（chapterEditor）、`frontend/index.html`、`backend/routers/books.py`；`frontend/e2e/product-audit.spec.js`、`backend/tests/test_product_audit.py` |
| 播放器（逐句同步，使用 V4 canonical analysis） | `frontend/app.js`（openPlayer / renderTranscript / bindAudio / bindPlayer） |
| 後台（概覽/審核/作品/類別/輪播/公告/任務/TTS 服務/AI 服務/稽核/作者申請/檢舉/使用者） | `frontend/app.js`、`frontend/admin/admin-console.js`（tab 由 `data-atab` 切換） |
| 登入/註冊 | `frontend/app.js` |
| 初始載入與公告 popup coordinator | `frontend/services/bootstrap.js`、`frontend/services/announcements.js`；`frontend/index.html`、`frontend/app.js`、`frontend/platform.js` 協作揭示 view |
| Web release/update resilience | `backend/main.py`、`backend/settings.py`、`frontend/release.js`、`frontend/sw.js`、`frontend/seo.js`、`backend/tests/test_web_update_resilience.py`、`frontend/e2e/web-update-resilience.spec.js` |
| 統一 API 封裝 | `frontend/services/api.js`（NovelApi.request） |
| 帳號／作者 profile self-service、pending email、linked identity/password settings | `backend/routers/auth.py`、`frontend/app.js`（`#/settings` 個人設定頁；舊 modal 僅相容保留） |
| 公開作者頁與作者導覽 | `backend/routers/platform.py`、`backend/services/profile.py`、`frontend/platform.js` |
| Platform pagination（public search/category、My Works、private shelf/history/bookmarks、author works） | `backend/pagination.py`、`backend/db.py`、`backend/services/book_service.py`、`backend/services/profile.py`、`backend/routers/platform.py`、`backend/routers/books.py` |
| Shared pagination UI / route race fencing | `frontend/services/pagination.js`、`frontend/platform.js`、`frontend/app.js`、`frontend/e2e/platform-pagination.spec.js`、`frontend/services/__tests__/pagination.test.js` |
| Author 申請紀錄、Reviewer Queue/Detail、Super Admin protected UX | `frontend/app.js`、`frontend/index.html`、`frontend/e2e/roles-review-workflow.spec.js` |
| Admin Console bounded overview/books/governance/service/audit UX | `frontend/admin/admin-console.js`、`frontend/app.js`、`frontend/e2e/admin-console-vnext.spec.js`、`backend/tests/test_admin_console_vnext.py` |
| R18 稽核瀏覽、詳情與 Super Admin 匯出 | `frontend/app.js`、`frontend/e2e/audit-governance.spec.js`、`backend/tests/test_audit_governance.py` |
| Platform announcements popup/Admin UX | `frontend/services/announcements.js`、`frontend/admin/admin-console.js`、`frontend/style.css`、`frontend/e2e/platform-announcements.spec.js`、`backend/tests/test_platform_announcements.py` |
| Account notification bell/popover/center | `frontend/services/notifications.js`、`frontend/platform.js`、`frontend/app.js`、`frontend/index.html`、`frontend/style.css`、`frontend/e2e/notification-system.spec.js`、`backend/tests/test_notification_system.py`；coordinator 以 route snapshot + principal generation 防止 late read response 覆寫目前導覽 |
| Public Audiobook Discovery collection/detail integration | `backend/services/book_service.py`、`backend/routers/platform.py`、`frontend/services/audiobooks.js`、`frontend/platform.js`、`frontend/e2e/audiobook-discovery.spec.js`、`backend/tests/test_audiobook_discovery.py` |

## 6. 測試與驗證命令

```bash
# 後端
python -m pytest backend/tests -q        # 698 passed, 1 skipped, 1 warning（含 R18 audit governance、audiobook discovery、notification、announcements、roles/review、authentication/oauth、identity 與 v4 regression）
python -m compileall backend

# 前端單元（Vitest）
npm run test:unit                          # 94 passed

# 前端 E2E（Playwright，會自動起 webServer）
npm run test:e2e -- --workers=1            # 284 passed；deterministic single-worker gate；含 R18 audit、notification/announcements、roles/review 與 authentication/oauth UX

# 語法檢查
node --check frontend/app.js
node --check frontend/platform.js

# 真實本地 TTS Live UAT（需環境變數，不 commit credential）
$env:UAT_ADMIN_PASSWORD='...'; $env:UAT_TTS_API_KEY='...'; node scripts/uat_live_tts.mjs
```

測試設定：`playwright.config.cjs`（webServer 自動跑 `python run.py`）、`vitest.config.js`（jsdom + globals）。
後端 `conftest.py` 用 tempdir 覆寫 `settings.ROOT_DIR/DB_PATH/BOOKS_DIR`，每個 test 自動 `init_db()`。

## 7. 環境變數重點（`.env.example`）

| 變數 | 用途 |
|---|---|
| `AI_PROVIDER=nvidia\|openai`、`AI_API_KEY`、`AI_MODEL`、`AI_BASE_URL` | AI 分析提供者（legacy env；V4 以後台 AI provider 為主） |
| `APP_ENV=development\|production` | 決定安全門檻與 dev bypass |
| `DEV_AUTH_BYPASS=true\|false` | 僅非 production 且 DB 無使用者時生效 |
| `ADMIN_USER`、`ADMIN_PASSWORD` | 首次建立 admin 帳號與密碼；production 密碼必填，帳號預設 `admin`；ADMIN_PASSWORD-only path 保留但已 deprecated |
| `PUBLIC_ORIGINS` | CORS 白名單（勿用 `*`） |
| `REDIS_URL` | 可選；有設則用 Redis 分散式鎖 |
| `AUTH_MAIL_BACKEND`、`AUTH_MAIL_FROM`、`AUTH_MAIL_FROM_NAME`、`AUTH_MAIL_M365_*`、`AUTH_BASE_URL` | authentication mail boundary；development 可用 console/file/local，tests 用 fake，Microsoft Graph 需 tenant/app/mailbox/secret；production 未配置回 NOT_CONFIGURED |
| `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`、`GOOGLE_ISSUER`、`GOOGLE_REDIRECT_URI` | Google OIDC 設定；本 change 不包含 production credentials/configuration |
| `TTS_SECRET_KEY`、`TTS_PROVIDER_ID`、`TTS_EDGE_ENABLED` | 遠端 TTS provider 設定（legacy env；V4 以後台 TTS provider 為主） |

## 8. 開發維運操作

### Dev DB 重設（Destructive，只限 development）

> 確認 `APP_ENV != production` 且可接受資料遺失後才執行：

```powershell
Remove-Item -LiteralPath "data\app.db", "data\app.db-wal", "data\app.db-shm" -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "storage\books", "storage\preview_cache" -Recurse -Force -ErrorAction SilentlyContinue
# 重新啟動 python run.py → 自動 fresh V4 schema + seed 類別 + admin bootstrap
```

不得刪除：`data/.session_key`、`data/.ai_secret_key`、`data/.tts_secret_key`、`.env`。

### 資料庫備份／還原

```bash
python -m scripts.backup backup [--output-dir DIR] [--keep N]
python -m scripts.backup list [DIR]
python -m scripts.backup restore ARCHIVE [--dry-run]
```

- 使用 SQLite online backup API 取一致快照；還原前自動建立緊急備份；restore 只允許 zip manifest 記錄的原始佈局（防路徑穿越）。

## 9. 已知高風險/易改壞區域（改動前務必讀）

1. `frontend/app.js` 是 ~2.9k 行單體，雙前端（app/platform）各自有 state/toast/esc，改一處可能影響另一處。
2. `backend/state.py` 鎖、`routers/auth.py` 登入限流是 process-local，多副本時失效。
3. 章節權限：`GET /books/{bid}/chapters/{seq}` 與 `GET /books/{bid}/audio/{seq}` 已統一經 `book_service.can_view_chapter`（檢查整書可見性 + `publish_status`）。**改動章節讀取/音訊時務必保留此檢查**。
4. 前端試聽與所有 API 一律走 `NovelApi.request`（`frontend/services/api.js`），自動帶 CSRF header。**不要改回裸 fetch**（production 下會 403）。
5. `storage/books/{bid}` 是產物、`db.py` 是真源，別把兩邊當同一份資料處理。
6. 生成任務是 DB 佇列 + 單 worker，別改成 FastAPI BackgroundTasks。
7. 作者生成流程已遷移 V4 canonical：`app.js` 以 `book.workflow` 驅動 next-step（單語者：選聲線→生成；多語者：分析→聲線對映→生成），呼叫 `/analysis`、`/audio-generations`；**不得恢復 legacy `/analyze`、`/tts` endpoints 或 legacy pipeline jobs**。
8. 後台 tab 渲染是 async：`renderAdminPanel` 各 branch 在 fetch 後有 `if (state.adminTab !== tab) return;` 守衛，新增 tab 時須比照。
