# StoryLingo AI Workflow

## 用途與邊界

本文件是 StoryLingo 執行「已批准 OpenSpec change」的 reusable working model。它不是無限背景執行器，也不會授予 production、secret、deploy、destructive DB 或未批准 scope 的權限。Discovery pass、proposal pass 與 implementation pass 必須分開；本 vNext bootstrap 本身只完成 discovery artifacts。

## Intake contract

開始前，主 Agent 必須依序讀取 `AGENTS.md`、`PROJECT_MAP.md`、`ARCHITECTURE.md`，再讀目標 change 的 `proposal.md`、所有相關 `specs`、`design.md`、`tasks.md` 與 source/tests。接著檢查 `git status`、最近相關 log、完整相關 diff，建立 change scope、exclusions、dependency gates、test plan。

`TASK_LEDGER` 必須有下列欄位：TASK ID、Requirement IDs、Description、Status、Evidence、Tests、Review result。允許狀態只有 `TODO`、`IN_PROGRESS`、`IMPLEMENTED`、`VERIFIED`、`BLOCKED`；`VERIFIED` 必須有 evidence。

## Autonomous completion loop

### LOOP 1 — PLAN

將每個 approved requirement ID 對到 task、source boundary、acceptance/test 與 release gate。先處理 dependency；發現會改變 spec、migration、安全邊界或產品選擇的未知事項時，輸出 `BLOCKED_BY_USER_DECISION`，不要默猜。

### LOOP 2 — IMPLEMENT

只修改 approved change scope。保留 DB 真源、canonical V4 domain、job queue boundary、chapter visibility、CSRF、secret 與 path/SSRF invariant。不要為了方便把 frontend state 變成 workflow truth。

### LOOP 3 — TARGETED TEST

執行最接近改動的 test。失敗時先分類 root cause（code、fixture、環境、spec mismatch），在 scope 內修正後重跑；不要把第一次 fail 直接交回使用者。

### LOOP 4 — REQUIREMENT AUDIT

重新閱讀每個 requirement ID，逐條標 `PASS`、`FAIL`、`UNCLEAR` 或 `NOT IMPLEMENTED`。任何 `FAIL`／`NOT IMPLEMENTED` 都使 change 保持未完成；`UNCLEAR` 需補證據或升級決策。

### LOOP 5 — ADVERSARIAL REVIEW

由不同 review role 問：「為什麼這還不能算完成？」檢查漏 requirement、API mismatch、權限繞過、stale UI、錯誤路徑、reload/persistence、legacy compatibility、migration、secret、dirty contamination。發現合理缺陷就回到 IMPLEMENT。

### LOOP 6 — REGRESSION

依 risk level 執行 targeted、backend、frontend、Playwright、compile、syntax 與 diff checks。跨層行為必須有 integration regression，不得只用 helper/unit test 代替。

### LOOP 7 — BROWSER-FIRST

只要 change 影響使用者可操作流程，就從 UI 驗證真正的 desktop/mobile flow、reload、loading、empty、error、permission boundary 與 direct URL。API tests 不能取代 browser evidence。

### LOOP 8 — HYGIENE

檢查 unstaged/staged diff、unrelated changes、TODO/FIXME、placeholder、debug log、temporary files、secrets、migration files 與 generated artifacts。不得用 `git add .`；不得碰使用者既有 dirty。

### LOOP 9 — COMPLETION AUDIT

只有所有 approved requirements 有實作結果、acceptance/tests PASS、適用 browser flow PASS、error／persistence／permission／backward compatibility 已驗證、文件與 runtime 一致、traceability 完整、independent review 無 blocker，才可標 `VERIFIED`／完成。仍有已知 minor issue 時只能標 `READY WITH KNOWN NON-BLOCKERS` 並列明；blocker 不得降級。

## Roles and delegation

主 Agent 是 Integrator／Orchestrator，負責 scope、決策、合併證據與最後 audit。可有限委派下列角色：

- Product/UX：需求完整性、user flow、mobile interaction、missing states；預設 read-only。
- Architecture：DB/API/data ownership、state transition、queue、migration、compatibility；預設 read-only。
- Security/Auth：OAuth、CSRF、RBAC、privileged roles、recovery、audit；預設 read-only。
- Implementation：只修改被分配的 approved tasks。
- QA/Adversarial：不假設實作正確，專找漏做與 regression。
- Release Auditor：只讀檢查 traceability、tests、diff、scope、placeholder、TODO、skip、secret、migration。

本環境目前可辨識有限的 agent delegation／Codex task tooling；repository rule 要求 delegated agent 使用與主 Agent 相同模型，而且產出必須由主 Agent 驗證。若當次執行沒有可用的相同模型 delegation，主 Agent 必須依上述順序模擬角色，不得因此省略 review phase。不要把 user-owned 新 task 當作隱性 subtask，也不要宣稱可無限背景執行。

## Skill capability assessment

本輪盤點到的 project-local OpenSpec skills 是 `openspec-explore`、`openspec-propose`、`openspec-apply-change`、`openspec-update-change`、`openspec-sync-specs`、`openspec-archive-change`；另有 `.opencode/skills` 的 imported skills，但沒有既有 StoryLingo-specific implementation/review skill。Codex 會依可發現的 `SKILL.md` skill catalog 載入 repo／global skills；system skill `skill-creator` 可驗證新 skill。本輪已新增並通過 quick validation 的 `.agents/skills/storylingo-change-autopilot/SKILL.md`，把本文件的 loop 封裝成一致入口。它仍是每次執行時的流程指引，不會創造 infinite background execution、持久 agent 或額外權限。

## Stop policy

第一次 test fail、漏 test、小 regression、spec/code 不一致或需要重跑 validation 都不是停止理由。只有互斥產品選擇、必要 OAuth credentials/secrets、不可逆 production/destructive operation、必須改變明確禁止 invariant、未授權付費外部服務、repo 無法推導的必要資料、互相矛盾的 approved specs，才可停並輸出：`BLOCKED_BY_USER_DECISION`、blocker、可選方案、推薦、後果。

## Discovery-to-implementation handoff

Discovery 交付狀態只能是 `DISCOVERY READY FOR REVIEW` 或 `DISCOVERY BLOCKED`。Implementation 只有在 child proposal/spec/tasks 被批准、decision gate 已關閉、ledger 已建立後才能開始。對 StoryLingo vNext，第一個推薦的窄 scope child 是 `initial-loading-fix`；它不能與 identity/admin refactor 合併。
