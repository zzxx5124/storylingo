"use strict";

const $ = (s) => document.querySelector(s);
const StoryLingoBootstrap = window.StoryLingoBootstrap;
const CAT_LABEL = { zh: "純中文", vocab: "中英單字", bilingual: "雙語", en: "純英文", other: "其它" };
function languageTypeOptions() {
  return Object.entries(CAT_LABEL).map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
}
function bookHasPrologue(book) {
  // 舊書沒有設定時維持原本 seq=0 顯示為序章的行為。
  return book?.hasPrologue !== false;
}
function chapterDisplayNumber(book, seq) {
  const index = Number(seq);
  if (!Number.isFinite(index)) return "";
  return bookHasPrologue(book) && index === 0 ? "序" : String(index + (bookHasPrologue(book) ? 0 : 1));
}
function chapterDisplayLabel(book, seq) {
  const number = chapterDisplayNumber(book, seq);
  return number === "序" ? "序章" : `第 ${number} 章`;
}
// 依「主分類 + 分類標籤」判斷實際分析/生成風格（與後端 settings.effective_category 完全對齊）
function effCategory(b) {
  const cats = [];
  for (const c of [b.category, ...(b.categories || [])]) {
    if (c in CAT_LABEL && !cats.includes(c)) cats.push(c);
  }
  // 標籤同時含原有 4 類（「放入全部分類」）時標籤無區別作用 → 退回主分類
  if (["zh", "vocab", "bilingual", "en"].every((value) => cats.includes(value))) {
    return (b.category in CAT_LABEL) ? b.category : "vocab";
  }
  for (const c of ["en", "bilingual", "vocab", "zh"]) if (cats.includes(c)) return c;
  return (b.category in CAT_LABEL) ? b.category : "vocab";
}
function needsEnglishVoice(b) {
  return ["en", "vocab", "bilingual"].includes(effCategory(b));
}
const state = {
  books: [],
  book: null,
  voices: [],
  view: "bookshelf",
  filter: "all",
  categoryFilter: "all",
  authed: false,
  me: null,
  authMethods: { google: { available: false } },
  adminTab: "review",
  adminPage: { review: 1, ownership: 1, books: 1, categories: 1, banners: 1, announcements: 1, jobs: 1, generation: 1, audit: 1, authors: 1, reports: 1, users: 1 },
  adminFilter: { reviewType: "", reviewStatus: "", bookQ: "", bookPublication: "", bookRequestStatus: "", bookGenerationStatus: "", categoryQ: "", categoryEnabled: "", announcementStatus: "", announcementAudience: "", reportStatus: "open", reportTargetType: "", reportQ: "", jobsStatus: "", jobsType: "", generationService: "", generationStatus: "", auditAction: "", auditActor: "", auditCategory: "", auditTargetType: "", auditDateFrom: "", auditDateTo: "", authorStatus: "pending", userQ: "", userRole: "", userStatus: "", userAuthorStatus: "" },
  reviewDetailId: null,
  expandedSeq: null,
  analysisCache: {},
  voicePrefs: {},
  segments: [],
  cumStart: [],
  segmentDurations: [],
  totalDur: 0,
  currentSeq: -1,
  currentSeg: -1,
  textOnly: false,
  autoNext: true,
  pollTimer: null,
  autoGen: false,
  _pollSnap: "",
  pendingSeek: null,
  _lastSaveTs: 0,
  playbackRate: 1,
  batchActive: false,
  batchCancelled: false,
  batchJobId: null,
  batchCancelInFlight: false,
  analysisInFlight: new Set(),
  analysisCancelInFlight: new Set(),
  newBookSubmitting: false,
  wakeOn: true,
  chapterAudio: new Audio(),
  previewAudio: new Audio(),
  previewBlobUrl: null,
  previewInFlight: false,
  previewButton: null,
  mineRequestGeneration: 0,
  minePrincipalKey: "",
  ownershipBookId: null,
  ownershipEmergency: false,
  ownershipSubmitting: false,
};

function updateMinePrincipal(me) {
  const key = me
    ? `${me.id}:${me.role}:${me.accountStatus || me.account_status || "active"}:${me.sessionVersion || me.session_version || ""}`
    : "guest";
  if (key !== state.minePrincipalKey) {
    state.mineRequestGeneration += 1;
    state.minePrincipalKey = key;
  }
}

/* L1-6：試聽 blob 統一管理，避免舊 URL 洩漏 */
function setPreviewBlob(blob) {
  if (state.previewBlobUrl) URL.revokeObjectURL(state.previewBlobUrl);
  state.previewBlobUrl = URL.createObjectURL(blob);
  state.previewAudio.src = state.previewBlobUrl;
}

const SPEAKER_COLORS = ["#58a6ff", "#3fb950", "#e3b341", "#f778ba", "#bc8cff", "#79c0ff", "#ffd479", "#39d5c9", "#ff9f7a", "#8b5cf6"];
function colorOf(name) {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return SPEAKER_COLORS[h % SPEAKER_COLORS.length];
}
function genderColor(g) {
  return g === "男" ? "#58a6ff" : g === "女" ? "#f778ba" : "#8b98a9";
}
function isGuest() { return !state.authed; }

/* ---------------- 聆聽進度記憶（localStorage） ---------------- */
const PROG_KEY = "novel_listen_progress"; // { [bookId]: {seq, time, updatedAt} }
const LAST_KEY = "novel_last_book";       // 最近收聽的書 id

function loadListenMap() {
  try { return JSON.parse(localStorage.getItem(PROG_KEY) || "{}") || {}; }
  catch (e) { return {}; }
}
function getProgress(bookId) {
  const p = loadListenMap()[bookId];
  return p ? p : null;
}
function setProgress(bookId, seq, time) {
  const m = loadListenMap();
  m[bookId] = { seq: seq, time: time >= 0 ? time : 0, updatedAt: Date.now() };
  try { localStorage.setItem(PROG_KEY, JSON.stringify(m)); } catch (e) {}
  try { localStorage.setItem(LAST_KEY, bookId); } catch (e) {}
}
function clearProgress(bookId) {
  const m = loadListenMap();
  delete m[bookId];
  try { localStorage.setItem(PROG_KEY, JSON.stringify(m)); } catch (e) {}
}
/* ---------------- helpers ---------------- */
async function api(path, opts = {}) {
  // 統一委派給 NovelApi（FE-005）：CSRF、credentials、JSON、錯誤映射皆集中處理
  return NovelApi.request(path, opts);
}
function esc(s) {
  return window.NovelEscape.esc(s);
}
function fmtDate(s) { return new Date(s).toLocaleDateString("zh-Hant", { month: "numeric", day: "numeric" }); }
function fmtChars(n) { return n > 999 ? (n / 1000).toFixed(1) + "k" : String(n); }
function fmtTime(s) {
  if (!isFinite(s) || s < 0) s = 0;
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

function showView(name) {
  state.view = name;
  const all = ["platform", "bookshelf", "chapters", "player", "mine", "requests", "admin", "settings"];
  all.forEach((v) => {
    const el = $("#view-" + v);
    if (el) el.hidden = v !== name;
  });
  StoryLingoBootstrap.routeResolved(name);
  if (name !== "platform") StoryLingoBootstrap.viewVisible(name);
  if (["platform", "bookshelf", "mine", "requests", "admin"].includes(name)) stopPoll();
  if (name === "admin") renderAdmin();
  if (name === "mine") renderMine();
  if (name === "requests") renderRequests();
  if (name === "settings") loadSettingsPage();
}

const STATUS_LABEL = { draft: "草稿", submitted: "待審核", approved: "已上架", rejected: "已退件", removed: "已下架" };
const STATUS_CLASS = { draft: "draft", submitted: "submitted", approved: "approved", rejected: "rejected", removed: "removed" };
const RESET_LINK_ERROR_MESSAGE = "此重設連結已失效或已被使用。請重新申請密碼重設連結。";

/* hash 路由：公開平台與既有朗讀管理流程並存。 */
let _handledAuthResult = "";
let _resetPasswordToken = "";

async function handleAuthResult() {
  const rawHash = location.hash || "";
  const hashQuestion = rawHash.indexOf("?");
  const hashRoute = hashQuestion >= 0 ? rawHash.slice(0, hashQuestion) : rawHash;
  const hashParams = hashQuestion >= 0 ? new URLSearchParams(rawHash.slice(hashQuestion + 1)) : null;
  const searchParams = new URLSearchParams(location.search || "");
  const result = hashParams?.get("auth") || searchParams.get("auth");
  if (!result) return;
  const key = location.href + ":" + result;
  if (_handledAuthResult === key) return;
  _handledAuthResult = key;
  if (result === "success" || result === "verified") {
    if (result === "success") {
      try {
        const me = await api("/api/auth/me");
        state.authed = !!me?.authed;
        state.me = me?.user || null;
        state.authMethods = me?.authMethods || { google: { available: false } };
        updateMinePrincipal(state.me);
        updateAuthUI();
        document.dispatchEvent(new CustomEvent("storylingo:auth-changed"));
      } catch (_) {}
      toast("登入成功", "ok");
    } else {
      toast("電子郵件已驗證，現在可以登入", "ok");
    }
  } else if (result === "link_pending") {
    const confirmed = window.confirm("確認將這個 Google 身份連結到目前帳號嗎？");
    try {
      if (confirmed) {
        await api("/api/auth/oauth/google/link/confirm", { method: "POST" });
        toast("Google 身份已連結", "ok");
      } else {
        await api("/api/auth/oauth/google/link/cancel", { method: "POST" });
      }
      if (confirmed) await openProfileSettings();
    } catch (error) {
      toast("Google 連結未完成：" + error.message, "err");
    }
  } else if (result.startsWith("error_")) {
    const code = result.slice(6);
    const message = code.includes("existing_account_requires_link")
      ? "這個信箱已有帳號，請先用密碼登入，再從個人設定明確連結 Google。"
      : code.includes("email_identity_conflict")
        ? "這個信箱對應多個既有帳號，請聯絡支援處理。"
        : code.includes("reset_token_invalid")
          ? RESET_LINK_ERROR_MESSAGE
          : "驗證流程未完成，請稍後再試。";
    toast(message, "err");
  }
  // 移除一次性結果參數，避免 reload 重複執行；不改變目前 route。
  history.replaceState(null, "", hashRoute || "#/home");
}

async function route() {
  void handleAuthResult();
  const rawRoute = (location.hash || "#/home").replace(/^#\/?/, "");
  const routeQueryIndex = rawRoute.indexOf("?");
  const h = (routeQueryIndex >= 0 ? rawRoute.slice(0, routeQueryIndex) : rawRoute);
  if (h === "reset-password") {
    const params = routeQueryIndex >= 0 ? new URLSearchParams(rawRoute.slice(routeQueryIndex + 1)) : new URLSearchParams();
    _resetPasswordToken = params.get("token") || "";
    if (_resetPasswordToken) {
      openAppModal("#login-modal", { focus: "#reset-password-new" });
      showResetPasswordPanel();
    } else {
      toast("重設連結無效或已過期", "err");
    }
    return;
  }
  resetAuthModal();
  const parts = h.split("/");
  const name = parts[0] || "bookshelf";
  if (["home", "search", "category", "rankings", "audiobooks", "book", "read", "shelf", "notifications", "author", "policy", "privacy"].includes(name)) {
    closeNavigationModals();
    showView("platform");
    return;
  } else if (name === "mine") {
    closeNavigationModals();
    if (!state.authed || !["author", "admin", "super_admin"].includes(state.me && state.me.role)) {
      toast("請先登入作者帳號", "err");
      openLogin(true);
      location.hash = "#/home";
      return;
    }
    state.book = null;
    state.chapterAudio.pause();
    showView("mine");
  } else if (name === "requests") {
    closeNavigationModals();
    if (!state.authed) {
      toast("請先登入", "err");
      openLogin(true);
      location.hash = "#/home";
      return;
    }
    state.book = null;
    state.chapterAudio.pause();
    showView("requests");
  } else if (name === "settings") {
    closeNavigationModals();
    if (!state.authed) {
      toast("請先登入才能開啟個人設定", "err");
      openLogin(true);
      return;
    }
    state.book = null;
    state.chapterAudio.pause();
    showView("settings");
  } else if (name === "admin") {
    closeNavigationModals();
    if (!state.authed || !(state.me && ["reviewer", "admin", "super_admin"].includes(state.me.role))) {
      toast("需要管理員權限", "err");
      openLogin(true);
      if (!state.authed) return;
      location.hash = "#/bookshelf";
      return;
    }
    const adminParams = routeQueryIndex >= 0 ? new URLSearchParams(rawRoute.slice(routeQueryIndex + 1)) : new URLSearchParams();
    const requestedAdminTab = adminParams.get("tab");
    const adminTabs = ["overview", "review", "ownership", "books", "categories", "banners", "announcements", "jobs", "generation", "tts", "ai", "audit", "authors", "reports", "users"];
    if (adminTabs.includes(requestedAdminTab)) state.adminTab = requestedAdminTab;
    restoreAdminUrlState(adminParams);
    const requestedPage = Number(adminParams.get("page"));
    if (Number.isFinite(requestedPage) && requestedPage >= 1 && requestedPage <= 100000) state.adminPage[state.adminTab] = Math.floor(requestedPage);
    if (state.me?.role === "reviewer" && !["review", "ownership", "generation"].includes(state.adminTab)) state.adminTab = "review";
    state.book = null;
    state.chapterAudio.pause();
    showView("admin");
  } else if (name === "detail" && parts[1]) {
    await openBook(parts[1]);
  } else {
    if (state.view === "player") state.chapterAudio.pause();
    state.book = null;
    showView("bookshelf");
    loadBooks();
  }
}
function renderBreadcrumb() {
  const el = $("#topbar-breadcrumb");
  const b = state.book;
  if (!b) { el.innerHTML = `<span class="cur">小說朗讀</span>`; return; }
  let html = `<button class="crumb" data-crumb="home">書架</button><span class="sep">/</span>`;
  if (state.view === "chapters") {
    html += `<span class="cur">${esc(b.title)}</span>`;
  } else if (state.view === "player") {
    html += `<button class="crumb" data-crumb="chapters">${esc(b.title)}</button><span class="sep">/</span><span class="cur">${esc(state.currentChapterTitle || "")}</span>`;
  } else {
    html += `<span class="cur">${esc(b.title)}</span>`;
  }
  el.innerHTML = html;
  el.querySelectorAll("[data-crumb]").forEach((btn) => btn.addEventListener("click", () => {
    if (btn.dataset.crumb === "home") goHome(); else goChapters();
  }));
}

const _TOAST_TYPE = { ok: "success", err: "error", info: "info" };
function toast(msg, type = "info") {
  return window.NovelToast.toast(msg, _TOAST_TYPE[type] || "info");
}

/* ---------------- 書架 ---------------- */
async function loadBooks() {
  state.books = await api("/api/books");
  renderBookshelf();
  renderContinueBanner();
}

function continueBook(book) {
  const prog = getProgress(book.id);
  const withAudio = (book.chapters || []).filter((c) => c.audio === "ready");
  if (!withAudio.length) { toast("本書尚無可播放的音訊", "err"); return; }
  let target = withAudio.find((c) => c.seq === (prog && prog.seq));
  if (!target) target = withAudio[0];
  const seek = (prog && target.seq === prog.seq && isFinite(prog.time)) ? prog.time : 0;
  state.pendingSeek = seek;
  state.book = book;
  openPlayer(target.seq, true);
}

function renderContinueBanner() {
  const el = $("#continue-banner");
  if (!el) return;
  let lastId = null;
  try { lastId = localStorage.getItem(LAST_KEY); } catch (e) {}
  const prog = lastId ? getProgress(lastId) : null;
  const book = lastId ? state.books.find((b) => b.id === lastId) : null;
  const ch = book && prog ? book.chapters.find((c) => c.seq === prog.seq && c.audio === "ready") : null;
  if (!book || !ch) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  el.innerHTML = `
    <span class="cb-title">繼續收聽</span>
    <span class="cb-name">${esc(book.title)}</span>
    <span class="cb-sub">上次聽到 ${chapterDisplayLabel(book, ch.seq)} · ${esc(ch.title)}</span>
    <span class="spacer"></span>
    <button class="btn btn-accent" data-continue="${book.id}">繼續收聽</button>`;
  el.querySelector("[data-continue]").addEventListener("click", () => {
    const b = state.books.find((x) => x.id === book.id);
    if (b) continueBook(b);
  });
}

function renderBookshelf() {
  const list = $("#book-list");
  const books = state.categoryFilter === "all"
    ? state.books
    : state.books.filter((b) => (b.categories || []).includes(state.categoryFilter));
  $("#shelf-count").textContent = books.length ? `${books.length} 本書` : "";
  if (!books.length) {
    list.innerHTML = `<div class="ch-hintline" style="text-align:center;padding:30px 0">${
      state.authed
        ? "上傳一本小說，開始你的聽讀英語之旅。"
        : "訪客模式：僅能瀏覽已上架的小說。請登入（或註冊作者）以進行上傳。"
    }</div>`;
    return;
  }
  list.innerHTML = books.map((b) => {
    const total = (b.chapters || []).length;
    const ready = b.chapters.filter((c) => c.audio === "ready").length;
    const analyzed = b.chapters.filter((c) => c.status === "analyzed").length;
    const pct = total ? Math.round((ready / total) * 100) : 0;
    // 標籤一律顯示「實際分析風格」（= 主分類），與分析/生成結果一致，避免誤導
    const catBadges = `<span class="badge badge-cat-mini" data-cat="${effCategory(b)}">${CAT_LABEL[effCategory(b)]}</span>`;
    // 聆聽進度（瀏覽器記憶）
    const prog = getProgress(b.id);
    const progCh = prog ? b.chapters.find((c) => c.seq === prog.seq && c.audio === "ready") : null;
    const resumeBtn = progCh
      ? `<button class="btn" data-resume="${b.id}" title="繼續上次聽到「${esc(progCh.title)}」">繼續收聽</button>`
      : "";
    const progLabel = progCh
      ? ` · <span class="resume-label">上次聽到 ${chapterDisplayLabel(b, progCh.seq)} ${esc(progCh.title)}</span>`
      : "";
    const actions = `${resumeBtn}<button class="btn btn-accent" data-open="${b.id}">開啟</button>
       ${canEditBook(b) ? `<button class="btn btn-ghost btn-danger" data-del="${b.id}">刪除</button>` : ""}`
      ;
    return `
    <div class="book-card" data-id="${b.id}">
      <div class="book-cover${b.coverImage ? " has-img" : ""}">${b.coverImage ? `<img src="${esc(b.coverImage)}" alt="" onerror="this.onerror=null;this.parentElement.classList.remove('has-img');" loading="lazy">` : ""}<span class="book-cover-fallback">${esc(b.title.slice(0, 1))}</span></div>
      <div class="book-info">
        <div class="book-title">${esc(b.title)} <span class="cat-badge-wrap">${catBadges}</span></div>
        <div class="book-sub">${total} 章 · 已分析 ${analyzed} · 可播放 ${ready}${progLabel} · ${fmtDate(b.created)}</div>
        <div class="book-progress"><div class="progress"><div class="bar bar-green" style="width:${pct}%"></div></div></div>
      </div>
      <div class="book-actions">${actions}</div>
    </div>`;
  }).join("");

  list.querySelectorAll("[data-open]").forEach((btn) =>
    btn.addEventListener("click", (e) => { e.stopPropagation(); openBook(btn.dataset.open); }));
  list.querySelectorAll("[data-resume]").forEach((btn) =>
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const book = state.books.find((x) => x.id === btn.dataset.resume);
      if (book) continueBook(book);
    }));
  list.querySelectorAll(".book-card").forEach((card) =>
    card.addEventListener("click", (e) => { if (!e.target.closest("[data-resume],[data-del],[data-open]")) openBook(card.dataset.id); }));
  list.querySelectorAll("[data-del]").forEach((btn) =>
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("確定刪除這本小說（含已生成音訊）？")) return;
      await api("/api/books/" + btn.dataset.del, { method: "DELETE" });
      clearProgress(btn.dataset.del);
      toast("已刪除", "ok");
      loadBooks();
    }));

  if (state.authed) {
    list.querySelectorAll(".book-cover").forEach((cover) => {
      cover.addEventListener("click", (e) => {
        e.stopPropagation();
        const bid = cover.closest("[data-id]").dataset.id;
        const book = state.books.find((item) => item.id === bid);
        if (!canEditBook(book)) return;
        const input = document.createElement("input");
        input.type = "file";
        input.accept = "image/jpeg,image/png,image/webp";
        input.addEventListener("change", async () => {
          if (!input.files[0]) return;
          try {
            const fd = new FormData();
            fd.append("file", input.files[0], input.files[0].name);
            const res = await api(`/api/books/${bid}/cover`, { method: "POST", body: fd });
            const book = state.books.find((b) => b.id === bid);
            if (book) book.coverImage = res.coverImage;
            renderBookshelf();
            toast("封面已更新", "ok");
          } catch (er) { toast(`封面更新失敗：${er.message}`, "err"); }
        });
        input.click();
      });
    });
  }
}

function bindShelfFilters() {
  document.querySelectorAll("#shelf-filters .chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      state.categoryFilter = chip.dataset.shelf;
      document.querySelectorAll("#shelf-filters .chip").forEach((c) => c.classList.toggle("active", c === chip));
      renderBookshelf();
      renderContinueBanner();
    }));
}

/* ---------------- 我的創作 ---------------- */
async function openOwnershipTransfer(bookId, emergency = false) {
  const modal = $("#ownership-transfer-modal");
  const target = $("#ot-target");
  if (!modal || !target) return;
  state.ownershipBookId = bookId;
  state.ownershipEmergency = emergency;
  const title = $("#ot-title");
  const hint = $("#ot-hint");
  const submit = $("#ot-submit");
  if (title) title.textContent = emergency ? "緊急轉移作品所有權" : "轉移作品所有權";
  if (hint) hint.textContent = emergency
    ? "Super Admin 專用。請確認目標帳號與緊急原因；此操作會留下不可刪除的治理紀錄。"
    : "目前所有者可邀請一個明確的現有帳號。目標接受後仍需 Reviewer／Admin 審核。邀請 7 天後過期。";
  target.innerHTML = '<option value="">載入可選帳號…</option>';
  target.disabled = true;
  if (submit) submit.disabled = true;
  openAppModal("#ownership-transfer-modal", { focus: "#ot-target" });
  try {
    const data = await api("/api/ownership-transfers/targets");
    const items = data.items || [];
    target.innerHTML = items.length
      ? `<option value="">選擇目標帳號</option>${items.map((item) => `<option value="${esc(item.id)}">${esc(item.username)} · ${esc(item.role)}</option>`).join("")}`
      : '<option value="">目前沒有可選的 active 帳號</option>';
    target.disabled = !items.length;
    if (submit) submit.disabled = !items.length;
  } catch (error) {
    target.innerHTML = '<option value="">帳號載入失敗</option>';
    if (hint) hint.textContent = `無法載入目標帳號：${error.message}`;
  }
}

async function submitOwnershipTransfer() {
  if (state.ownershipSubmitting) return;
  const target = $("#ot-target");
  const reason = $("#ot-reason");
  const submit = $("#ot-submit");
  const targetId = Number(target?.value || 0);
  if (!targetId) { toast("請選擇明確的目標帳號", "err"); target?.focus(); return; }
  if (state.ownershipEmergency && !(reason?.value || "").trim()) { toast("緊急轉移需要明確原因", "err"); reason?.focus(); return; }
  if (state.ownershipEmergency && !confirm("這是 Super Admin 緊急轉移，不是一般所有權移交。確定繼續？")) return;
  state.ownershipSubmitting = true;
  if (submit) submit.disabled = true;
  const body = { targetAccountId: targetId, reason: (reason?.value || "").trim() };
  const path = state.ownershipEmergency
    ? `/api/admin/books/${encodeURIComponent(state.ownershipBookId)}/ownership-transfer/emergency`
    : `/api/books/${encodeURIComponent(state.ownershipBookId)}/ownership-transfers`;
  try {
    await api(path, { method: "POST", body });
    closeAppModal("#ownership-transfer-modal");
    toast(state.ownershipEmergency ? "緊急所有權轉移已完成並寫入治理紀錄" : "所有權轉移邀請已送出", "ok");
    if (state.view === "mine") renderMine();
    if (state.view === "requests") renderRequests();
    if (state.view === "admin") renderAdminPanel();
  } catch (error) {
    toast(`所有權轉移失敗：${error.message}`, "err");
  } finally {
    state.ownershipSubmitting = false;
    if (submit) submit.disabled = false;
  }
}

async function renderMine() {
  const list = $("#mine-list");
  const routeParams = new URLSearchParams((location.hash || "").split("?")[1] || "");
  const page = Math.max(1, Number(routeParams.get("page") || 1) || 1);
  const principalKey = state.me
    ? `${state.me.id}:${state.me.role}:${state.me.accountStatus || state.me.account_status || "active"}:${state.me.sessionVersion || state.me.session_version || ""}`
    : "guest";
  updateMinePrincipal(state.me);
  const requestGeneration = ++state.mineRequestGeneration;
  let mine = [];
  let mineMeta = null;
  try {
    const result = await api(`/api/books?mine=1&page=${page}&page_size=20`);
    mineMeta = window.StoryLingoPagination.normalize(result);
    mine = mineMeta.items;
    if (requestGeneration !== state.mineRequestGeneration || principalKey !== state.minePrincipalKey) return;
  } catch (e) {
    if (requestGeneration !== state.mineRequestGeneration || principalKey !== state.minePrincipalKey) return;
    toast(`載入失敗：${e.message}`, "err");
    list.innerHTML = `<div class="ch-hintline" style="text-align:center;padding:30px 0">載入作品失敗，請稍後再試。</div>`;
    return;
  }
  // Book publication state and request state are separate truths.  Overlay
  // the bounded request read model so an active request cannot leave a
  // second Submit action visible while the Book itself remains non-public.
  let requestItems = [];
  try { requestItems = (await api("/api/requests?page=1&page_size=100")).items || []; } catch (e) {}
  if (requestGeneration !== state.mineRequestGeneration || principalKey !== state.minePrincipalKey) return;
  const requestFor = (bookId, type, activeOnly = false) => requestItems.find((item) =>
    (String(item.bookId) === String(bookId) || String(item.bookBid) === String(bookId)) && item.requestType === type &&
    (!activeOnly || ["SUBMITTED", "IN_REVIEW"].includes(item.status)));
  if (mineMeta.total_pages > 0 && page > mineMeta.total_pages) {
    location.hash = `#/mine?page=${mineMeta.total_pages}`;
    return;
  }
  $("#mine-count").textContent = mineMeta.total ? `${mineMeta.total} 本` : "";
  if (!mine.length) {
    list.innerHTML = `<div class="ch-hintline" style="text-align:center;padding:30px 0"><p style="margin:0 0 12px">還沒有作品。建立你的第一本作品，開始創作旅程。</p><button class="btn btn-accent" id="btn-new-book-empty" type="button">＋ 建立新作品</button></div>`;
    const emptyBtn = $("#btn-new-book-empty");
    if (emptyBtn) emptyBtn.addEventListener("click", openNewBookModal);
    return;
  }
  list.innerHTML = mine.map((b) => {
    const publishRequest = requestFor(b.id, "publish");
    const activePublishRequest = requestFor(b.id, "publish", true);
    const activeUnpublishRequest = requestFor(b.id, "unpublish", true);
    const activeAudiobookRequest = requestFor(b.id, "audiobook", true);
    const total = b.chapterCount ?? (b.chapters || []).length;
    const ready = b.audioReadyCount ?? (b.chapters || []).filter((c) => c.audio === "ready").length;
    const analyzed = b.analyzedChapterCount ?? (b.chapters || []).filter((c) => c.status === "analyzed").length;
    const pct = total ? Math.round((ready / total) * 100) : 0;
    const displayStatus = activePublishRequest ? activePublishRequest.status : b.status;
    const displayStatusLabel = REQUEST_STATUS_LABEL[displayStatus] || STATUS_LABEL[displayStatus] || displayStatus;
    const chip = `<span class="chip status-chip ${STATUS_CLASS[displayStatus] || String(displayStatus).toLowerCase()}">${displayStatusLabel}</span>`;
    const rejectionReason = publishRequest?.status === "REJECTED" ? publishRequest.decisionReason : b.rejectReason;
    const rejection = rejectionReason ? `<div class="book-rejection"><strong>退件原因</strong><span>${esc(rejectionReason)}</span></div>` : "";
    const submitBtn = (b.status === "draft" || b.status === "rejected") && !activePublishRequest
      ? `<button class="btn btn-accent" data-submit="${b.id}">送審</button>` : "";
    const audiobookBtn = b.status === "approved" && !activeAudiobookRequest
      ? `<button class="btn" data-request-type="audiobook" data-request-bid="${b.id}">申請有聲書</button>` : "";
    const unpublishBtn = b.status === "approved" && !activeUnpublishRequest
      ? `<button class="btn btn-ghost" data-request-type="unpublish" data-request-bid="${b.id}">申請下架</button>` : "";
    const pending = [activePublishRequest, activeUnpublishRequest, activeAudiobookRequest].filter(Boolean)
      .map((item) => `${REQUEST_TYPE_LABEL[item.requestType] || item.requestType}：${REQUEST_STATUS_LABEL[item.status] || item.status}`).join(" · ");
    const editBtn = `<button class="btn" data-medit="${b.id}">編輯</button>`;
    const statsBtn = `<button class="btn" data-mstats="${b.id}">統計</button>`;
    const delBtn = `<button class="btn btn-ghost btn-danger" data-mdel="${b.id}">刪除</button>`;
    const manage = `<a class="btn" href="#/detail/${encodeURIComponent(b.id)}" data-mopen="${esc(b.id)}">管理章節</a>`;
    const transferBtn = state.me?.id && String(b.ownerId) === String(state.me.id)
      ? `<button class="btn btn-ghost" data-transfer-book="${esc(b.id)}">轉移所有權</button>` : "";
    const nextStep = b.workflow && window.NovelWorkflow?.bookNextStep
      ? window.NovelWorkflow.bookNextStep(b.workflow)
      : (b.status === "draft" ? (total ? "完成內容後送審" : "先新增第一章") : "");
    const nextStepMarkup = nextStep ? `<div class="mine-next-step"><span>下一步</span><strong>${esc(nextStep.replace(/^下一步：/, ""))}</strong></div>` : "";
    // Keep the next step and the frequent management actions visible.
    // Lower-frequency requests and destructive actions stay behind one explicit menu.
    const moreActions = [audiobookBtn, unpublishBtn, statsBtn, delBtn].filter(Boolean).join("");
    const moreMenu = moreActions
      ? `<details class="mine-more-actions"><summary class="btn btn-ghost">更多操作</summary><div class="mine-more-menu">${moreActions}</div></details>`
      : "";
    const cover = b.coverImage || b.cover;
    return `
    <div class="book-card mine-card" data-id="${b.id}">
      <div class="book-cover${cover ? " has-img" : ""}">${cover ? `<img src="${esc(cover)}" alt="" onerror="this.onerror=null;this.parentElement.classList.remove('has-img');" loading="lazy">` : ""}<span class="book-cover-fallback">${esc((b.title || "?").slice(0, 1))}</span></div>
      <div class="book-info">
        <div class="book-title">${esc(b.title)} ${chip}</div>
        ${rejection}
        <div class="book-sub">${total} 章 · 已分析 ${analyzed} · 可播放 ${ready} · ${fmtDate(b.created)}${rejectionReason ? ` · 退件原因：${esc(rejectionReason)}` : ""}</div>
        ${pending ? `<div class="arev-sub">進行中的申請：${esc(pending)}</div>` : ""}
        ${nextStepMarkup}
        <div class="book-progress"><div class="progress"><div class="bar bar-green" style="width:${pct}%"></div></div></div>
      </div>
      <div class="book-actions">${submitBtn} ${manage} ${editBtn} ${transferBtn} ${moreMenu}</div>
    </div>`;
  }).join("") + (mineMeta ? window.StoryLingoPagination.render(mineMeta, {
    label: "我的創作分頁",
    hrefForPage: (value) => `#/mine?page=${value}`,
  }) : "");

  list.querySelectorAll("[data-submit]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      if (btn.disabled || !confirm("確定送審？送審後將由 Reviewer 審核。")) return;
      btn.disabled = true;
      try {
        await api(`/api/books/${btn.dataset.submit}/requests`, { method: "POST", body: { requestType: "publish" } });
        toast("已送審", "ok");
        renderMine();
      } catch (e) { btn.disabled = false; toast(`送審失敗：${e.message}`, "err"); }
    }));
  list.querySelectorAll("[data-request-type]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      if (btn.disabled) return;
      const label = btn.dataset.requestType === "audiobook" ? "申請有聲書" : "申請下架";
      if (!confirm(`確定${label}？`)) return;
      btn.disabled = true;
      try {
        await api(`/api/books/${btn.dataset.requestBid}/requests`, { method: "POST", body: { requestType: btn.dataset.requestType } });
        toast("申請已送出，等待 Reviewer 審核", "ok");
        renderMine();
      } catch (e) { btn.disabled = false; toast(`申請失敗：${e.message}`, "err"); }
    }));
  list.querySelectorAll("[data-medit]").forEach((btn) =>
    btn.addEventListener("click", () => openBookEdit(mine.find((b) => String(b.id) === btn.dataset.medit))));
  list.querySelectorAll("[data-mstats]").forEach((btn) =>
    btn.addEventListener("click", () => openBookStats(mine.find((b) => String(b.id) === btn.dataset.mstats))));
  list.querySelectorAll("[data-mdel]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      if (!confirm("確定刪除這本小說（含已生成音訊）？")) return;
      try {
        await api("/api/books/" + btn.dataset.mdel, { method: "DELETE" });
        clearProgress(btn.dataset.mdel);
        toast("已刪除", "ok");
        renderMine();
      } catch (e) { toast(`刪除失敗：${e.message}`, "err"); }
    }));
  list.querySelectorAll("[data-transfer-book]").forEach((btn) => {
    btn.addEventListener("click", () => openOwnershipTransfer(btn.dataset.transferBook));
  });
  // 「我的創作」的封面也沿用作品書架的上傳入口；若未綁定此事件，
  // 點擊圖片不會開啟檔案選擇器，使用者會看起來像是按鈕沒有反應。
  list.querySelectorAll(".mine-card .book-cover").forEach((cover) => {
    cover.addEventListener("click", (e) => {
      e.stopPropagation();
      const bid = cover.closest("[data-id]").dataset.id;
      const book = mine.find((item) => String(item.id) === bid);
      if (!canEditBook(book)) return;
      const input = document.createElement("input");
      input.type = "file";
      input.accept = "image/jpeg,image/png,image/webp";
      input.addEventListener("change", async () => {
        if (!input.files[0]) return;
        try {
          const fd = new FormData();
          fd.append("file", input.files[0], input.files[0].name);
          const res = await api(`/api/books/${bid}/cover`, { method: "POST", body: fd });
          const current = mine.find((item) => String(item.id) === bid);
          if (current) current.coverImage = res.coverImage;
          renderMine();
          toast("封面已更新", "ok");
        } catch (er) { toast(`封面更新失敗：${er.message}`, "err"); }
      });
      input.click();
    });
  });
}

async function renderRequests() {
  const list = $("#request-list");
  if (!list) return;
  list.innerHTML = `<div class="ch-hintline" style="text-align:center;padding:30px">載入申請紀錄中…</div>`;
  let data = { items: [] };
  let transferData = { items: [] };
  try { data = await api("/api/requests?page=1&page_size=100"); } catch (_) {}
  try { transferData = await api("/api/ownership-transfers?page=1&page_size=20"); } catch (_) {}
  const items = data.items || [];
  const transfers = transferData.items || [];
  if (!items.length && !transfers.length) {
    list.innerHTML = `<div class="ch-hintline" style="text-align:center;padding:30px">目前沒有內容申請。</div>`;
    return;
  }
  const labels = { SUBMITTED: "待審核", IN_REVIEW: "審核中", APPROVED: "已核准", REJECTED: "已退回", CANCELLED: "已取消", INVALIDATED: "已失效" };
  const types = { publish: "發布", unpublish: "下架", audiobook: "有聲書" };
  const contentHtml = items.length ? `<h2 class="request-section-title">內容申請</h2>${items.map((item) => `<div class="arev-row request-card">
    <div class="arev-info"><strong>${esc(item.bookTitle || item.bookBid || "作品")} · ${types[item.requestType] || item.requestType}</strong>
      <span class="chip status-chip ${item.status.toLowerCase()}">${labels[item.status] || item.status}</span>
      <span class="arev-sub">申請 #${item.id} · ${esc(item.submittedAt || "")} · Reviewer：${esc(item.reviewer || "尚未認領")}</span>
      ${item.decisionReason ? `<div class="arev-reason">原因：${esc(item.decisionReason)}</div>` : ""}</div>
    <div class="arev-actions"><a class="btn" href="#/requests" data-request-detail="${item.id}">查看紀錄</a>
      ${item.status === "SUBMITTED" ? `<button class="btn btn-ghost" data-request-cancel="${item.id}">取消申請</button>` : ""}</div>
  </div>`).join("")}` : "";
  const transferLabels = { REQUESTED: "等待目標回應", TARGET_ACCEPTED: "等待審核", IN_REVIEW: "審核中", REJECTED: "已拒絕", CANCELLED: "已取消", EXPIRED: "已過期", INVALIDATED: "已失效", COMPLETED: "已完成" };
  const transferHtml = transfers.length ? `<h2 class="request-section-title">所有權轉移</h2>${transfers.map((item) => {
    const mine = String(item.requesterAccountId) === String(state.me?.id);
    const targetMe = String(item.targetAccountId) === String(state.me?.id);
    const targetActions = targetMe && item.status === "REQUESTED"
      ? `<button class="btn btn-accent" data-transfer-accept="${item.id}">接受</button><button class="btn btn-ghost" data-transfer-reject="${item.id}">拒絕</button>` : "";
    const ownerCancel = mine && item.status === "REQUESTED" ? `<button class="btn btn-ghost" data-transfer-cancel="${item.id}">撤回</button>` : "";
    return `<div class="arev-row request-card ownership-transfer-card">
      <div class="arev-info"><strong>${esc(item.bookTitle || item.bookBid || "作品")} · 所有權轉移</strong>
        <span class="chip status-chip ${String(item.status).toLowerCase()}">${transferLabels[item.status] || item.status}</span>
        <span class="arev-sub">#${item.id} · 目前所有者：${esc(item.currentOwner || "—")} · 目標：${esc(item.target || "—")}</span>
        <span class="arev-sub">${item.status === "REQUESTED" ? `目標回應期限：${esc(item.expiresAt || "—")}` : `更新：${esc(item.updatedAt || "—")}`}</span>
        ${item.reason ? `<div class="arev-reason">原因：${esc(item.reason)}</div>` : ""}</div>
      <div class="arev-actions"><button class="btn" data-transfer-detail="${item.id}">查看紀錄</button>${targetActions}${ownerCancel}</div>
    </div>`;
  }).join("")}` : "";
  list.innerHTML = contentHtml + transferHtml;
  list.querySelectorAll("[data-request-cancel]").forEach((btn) => btn.addEventListener("click", async () => {
    btn.disabled = true;
    try { await api(`/api/requests/${btn.dataset.requestCancel}/cancel`, { method: "POST" }); toast("申請已取消", "ok"); renderRequests(); }
    catch (e) { btn.disabled = false; toast(`取消失敗：${e.message}`, "err"); }
  }));
  list.querySelectorAll("[data-request-detail]").forEach((link) => link.addEventListener("click", async (event) => {
    event.preventDefault();
    try {
      const detail = await api(`/api/requests/${link.dataset.requestDetail}`);
      const events = (detail.events || []).map((item) => `<li><strong>${esc(item.event_type)}</strong> · ${esc(item.created_at)}${item.reason ? ` · ${esc(item.reason)}` : ""}</li>`).join("");
      list.innerHTML = `<div class="review-detail"><button class="btn btn-ghost" id="request-list-back">← 返回申請紀錄</button><h2>${esc(detail.bookTitle || detail.bookBid || "內容申請")}</h2><p class="arev-sub">#${detail.id} · ${REQUEST_TYPE_LABEL[detail.requestType] || detail.requestType} · ${REQUEST_STATUS_LABEL[detail.status] || detail.status}</p><p class="arev-sub">送出：${esc(detail.submittedAt || "")} · Reviewer：${esc(detail.reviewer || "尚未認領")}</p>${detail.decisionReason ? `<div class="arev-reason">決策原因：${esc(detail.decisionReason)}</div>` : ""}<h3>事件紀錄</h3><ol class="review-events">${events}</ol></div>`;
      list.querySelector("#request-list-back")?.addEventListener("click", renderRequests);
    } catch (e) { toast(`載入詳細失敗：${e.message}`, "err"); }
  }));
  list.querySelectorAll("[data-transfer-accept]").forEach((button) => button.addEventListener("click", async () => {
    button.disabled = true;
    try { await api(`/api/ownership-transfers/${button.dataset.transferAccept}/accept`, { method: "POST" }); toast("已接受所有權轉移邀請，等待審核", "ok"); renderRequests(); }
    catch (e) { button.disabled = false; toast(`接受失敗：${e.message}`, "err"); }
  }));
  list.querySelectorAll("[data-transfer-reject]").forEach((button) => button.addEventListener("click", async () => {
    const reason = prompt("如有需要，請填寫拒絕原因：") || "";
    button.disabled = true;
    try { await api(`/api/ownership-transfers/${button.dataset.transferReject}/reject`, { method: "POST", body: { reason } }); toast("已拒絕轉移邀請", "ok"); renderRequests(); }
    catch (e) { button.disabled = false; toast(`拒絕失敗：${e.message}`, "err"); }
  }));
  list.querySelectorAll("[data-transfer-cancel]").forEach((button) => button.addEventListener("click", async () => {
    if (!confirm("確定撤回這筆所有權轉移邀請？")) return;
    button.disabled = true;
    try { await api(`/api/ownership-transfers/${button.dataset.transferCancel}/cancel`, { method: "POST" }); toast("轉移邀請已撤回", "ok"); renderRequests(); }
    catch (e) { button.disabled = false; toast(`撤回失敗：${e.message}`, "err"); }
  }));
  list.querySelectorAll("[data-transfer-detail]").forEach((button) => button.addEventListener("click", async () => {
    try {
      const detail = await api(`/api/ownership-transfers/${button.dataset.transferDetail}`);
      const events = (detail.events || []).map((item) => `<li><strong>${esc(item.event_type)}</strong> · ${esc(item.created_at)}${item.actor_username ? ` · ${esc(item.actor_username)}` : ""}${item.reason ? ` · ${esc(item.reason)}` : ""}</li>`).join("");
      list.innerHTML = `<div class="review-detail"><button class="btn btn-ghost" id="transfer-list-back">← 返回申請紀錄</button><h2>${esc(detail.bookTitle || detail.bookBid || "所有權轉移")}</h2><p class="arev-sub">#${detail.id} · ${transferLabels[detail.status] || detail.status} · 目前所有者：${esc(detail.currentOwner || "—")} · 目標：${esc(detail.target || "—")}</p><p class="arev-sub">邀請期限：${esc(detail.expiresAt || "—")} · 完成：${esc(detail.completedAt || "—")}</p>${detail.reason ? `<div class="arev-reason">原因：${esc(detail.reason)}</div>` : ""}<h3>轉移歷程</h3><ol class="review-events">${events || "<li>尚無事件。</li>"}</ol></div>`;
      list.querySelector("#transfer-list-back")?.addEventListener("click", renderRequests);
    } catch (e) { toast(`載入轉移紀錄失敗：${e.message}`, "err"); }
  }));
}

/* ---------- 建立新作品（手動建立，不需上傳檔） ---------- */

function openNewBookModal() {
  openAppModal("#newbook-modal", { focus: "#nb-title" });
  loadAuthorProfileChoices();
}

async function submitNewBook() {
  if (state.newBookSubmitting) return;
  const title = $("#nb-title").value.trim();
  if (!title) { toast("請輸入書名", "err"); $("#nb-title").focus(); return; }
  state.newBookSubmitting = true;
  const submitButton = $("#nb-submit");
  const originalLabel = submitButton?.textContent || "建立作品";
  if (submitButton) {
    submitButton.disabled = true;
    submitButton.textContent = "建立中…";
  }
  const payload = {
    title,
    synopsis: $("#nb-synopsis").value.trim(),
    category: $("#nb-category").value,
    serial: $("#nb-serial").value,
    categoryId: $("#nb-genre").value ? Number($("#nb-genre").value) : null,
    hasPrologue: document.querySelector('input[name="nb-prologue"]:checked')?.value !== "false",
    authorProfileId: $("#nb-author-profile")?.value ? Number($("#nb-author-profile").value) : null,
  };
  try {
    const book = await api("/api/books/manual", { method: "POST", body: payload });
    closeAppModal("#newbook-modal");
    $("#nb-title").value = ""; $("#nb-synopsis").value = "";
    toast("作品已建立，來寫第一章吧", "ok");
    state.pendingFirstChapter = true;
    location.hash = `#/detail/${book.id}`;
  } catch (e) { toast(`建立失敗：${e.message}`, "err"); }
  finally {
    state.newBookSubmitting = false;
    if (submitButton) {
      submitButton.disabled = false;
      submitButton.textContent = originalLabel;
    }
  }
}

function bindNewBook() {
  $("#btn-new-book")?.addEventListener("click", openNewBookModal);
  $("#nb-cancel")?.addEventListener("click", () => closeAppModal("#newbook-modal"));
  $("#nb-submit")?.addEventListener("click", submitNewBook);
}

/* ---------- 申請成為創作者 ---------- */

function openProfileSettings() {
  if (!state.authed) return openLogin(true);
  location.hash = "#/settings";
}

// Kept as a compatibility fallback for integrations that still open the old
// modal directly; the dedicated settings route is now the primary surface.
async function loadProfileSettingsModal() {
  if (!state.authed) return openLogin(true);
  openAppModal("#profile-modal", { focus: "#profile-display-name" });
  const list = $("#profile-author-list");
  if (list) list.innerHTML = '<p class="modal-subtitle">載入 profile…</p>';
  try {
    const data = await api("/api/auth/profile");
    state.authMethods = data?.authMethods || state.authMethods;
    const publicProfile = data.publicProfile || {};
    $("#profile-display-name").value = publicProfile.displayName || state.me?.username || "";
    $("#profile-bio").value = publicProfile.bio || "";
    $("#profile-email").value = data.account?.email || "";
    const emailStatus = $("#profile-email-status");
    if (emailStatus) {
      emailStatus.textContent = data.account?.pendingEmail
        ? "新信箱驗證中：" + data.account.pendingEmail
        : data.account?.emailVerified ? "信箱已驗證" : "尚未設定已驗證信箱";
    }
    const identities = $("#profile-identities");
    if (identities) {
      const linked = data.account?.externalIdentities || [];
      identities.innerHTML = linked.length
        ? linked.map((identity) => '<div class="profile-identity"><span>' + esc(identity.provider) +
          (identity.email ? " · " + esc(identity.email) : "") +
          '</span><button class="btn btn-ghost" type="button" data-unlink-google="' +
          esc(identity.id) + '">解除連結</button></div>').join("")
        : '<p class="modal-subtitle">尚未連結外部登入方式。</p>';
      identities.querySelectorAll("[data-unlink-google]").forEach((button) => {
        button.addEventListener("click", () => unlinkGoogle(button.dataset.unlinkGoogle));
      });
      if (!linked.some((identity) => identity.provider === "google")) {
        if (data.authMethods?.google?.available) {
          identities.insertAdjacentHTML("beforeend", '<button id="profile-google-link" class="btn btn-ghost" type="button">連結 Google</button>');
          identities.querySelector("#profile-google-link")?.addEventListener("click", beginGoogleLink);
        } else {
          identities.insertAdjacentHTML("beforeend", '<p class="modal-subtitle">Google 登入目前尚未開放，請使用帳號密碼。</p>');
        }
      }
    }
    const currentPassword = $("#profile-current-password");
    const passwordSave = $("#profile-password-save");
    if (currentPassword) currentPassword.hidden = !data.account?.hasPasswordCredential;
    if (passwordSave) passwordSave.textContent = data.account?.hasPasswordCredential ? "變更密碼" : "設定密碼";
    const authors = data.authorProfiles || [];
    if (list) {
      const entries = authors.map((author) => '<div class="profile-author-entry" data-profile-id="' + esc(author.profileId || "") + '"><label>作者名稱<input data-author-name maxlength="80" value="' + esc(author.displayName || "") + '"></label><label>作者網址<input data-author-slug maxlength="100" value="' + esc(author.slug || "") + '"></label><label>作者簡介<textarea data-author-bio maxlength="1000">' + esc(author.bio || "") + '</textarea></label><input data-author-avatar type="file" accept="image/jpeg,image/png,image/webp"><button class="btn btn-ghost" type="button" data-author-save>儲存作者 profile</button></div>').join("");
      const add = ["author", "admin"].includes(data.account?.role)
        ? '<div class="profile-author-entry profile-author-new"><p class="field-label">新增作者 profile</p><input data-new-author-name maxlength="80" placeholder="作者名稱"><input data-new-author-slug maxlength="100" placeholder="作者網址（選填）"><textarea data-new-author-bio maxlength="1000" placeholder="作者簡介（選填）"></textarea><button class="btn btn-ghost" type="button" data-author-create>新增作者 profile</button></div>' : '';
      list.innerHTML = (authors.length ? '<div class="profile-author-settings"><p class="field-label">作者 profile</p>' + entries + add + '</div>' : add || '<p class="modal-subtitle">目前還沒有作者 profile；作者申請核准後會建立。</p>');
      list.querySelectorAll("[data-author-save]").forEach((button) => {
        button.addEventListener("click", () => saveAuthorProfile(button));
      });
      list.querySelector("[data-author-create]")?.addEventListener("click", () => saveNewAuthorProfile(list.querySelector("[data-author-create]")));
    }
  } catch (error) {
    if (list) list.innerHTML = '<p class="detail-empty">載入失敗：' + esc(error.message) + '</p>';
  }
}

function profileEditorCurrent(editor = profileEditor) {
  return !!editor && profileEditor === editor && state.view === "settings" &&
    location.hash.split("?")[0] === "#/settings" && state.me?.id === editor.accountId;
}

function profileEditorValues() {
  return {
    displayName: $("#settings-display-name")?.value.trim() || "",
    bio: $("#settings-bio")?.value || "",
    email: $("#settings-email")?.value.trim() || "",
  };
}

function profileEditorDirty(editor = profileEditor) {
  if (!editor?.loaded) return false;
  const values = profileEditorValues();
  return JSON.stringify(values) !== JSON.stringify(editor.baseline) || !!$("#settings-avatar-file")?.files?.length;
}

function readProfileEditorDraft(editor) {
  try {
    const raw = sessionStorage.getItem(editor.draftKey);
    if (!raw) return null;
    const draft = JSON.parse(raw);
    return draft && draft.values ? draft : null;
  } catch (_) { return null; }
}

function saveProfileEditorDraft(editor) {
  if (!editor || !profileEditorDirty(editor)) return;
  try {
    sessionStorage.setItem(editor.draftKey, JSON.stringify({
      values: profileEditorValues(),
      revision: editor.revision || null,
      savedAt: Date.now(),
    }));
  } catch (_) {}
}

function clearProfileEditorDraft(editor, snapshot = null) {
  if (!editor) return;
  try {
    if (!snapshot || sessionStorage.getItem(editor.draftKey) === snapshot) sessionStorage.removeItem(editor.draftKey);
  } catch (_) {}
}

function updateProfileEditorUi(editor = profileEditor) {
  if (!editor || !profileEditorCurrent(editor)) return;
  const fields = ["#settings-display-name", "#settings-bio", "#settings-email", "#settings-avatar-file"];
  fields.forEach((selector) => { const el = $(selector); if (el) el.disabled = editor.busy || !editor.loaded; });
  const save = $("#settings-save");
  if (save) save.disabled = editor.busy || !editor.loaded;
  const discard = $("#settings-discard");
  if (discard) discard.disabled = editor.busy || !profileEditorDirty(editor);
  const retry = $("#settings-retry");
  if (retry) retry.hidden = !editor.loadError && !editor.conflict;
}

async function loadSettingsPage() {
  if (!state.authed) return;
  const page = $("#settings-page");
  const status = $("#settings-status");
  const authorList = $("#settings-author-list");
  if (!page) return;
  const editor = {
    accountId: state.me?.id,
    route: location.hash,
    loaded: false,
    busy: false,
    loadError: false,
    conflict: false,
    revision: null,
    baseline: { displayName: "", bio: "", email: "" },
    draftKey: `storylingo.profile-settings-draft:${state.me?.id ?? "guest"}`,
  };
  profileEditor = editor;
  page.setAttribute("aria-busy", "true");
  if (status) { status.hidden = false; status.className = "settings-status"; status.textContent = "載入個人設定…"; }
  updateProfileEditorUi(editor);
  try {
    const data = await api("/api/auth/profile");
    if (!profileEditorCurrent(editor)) return;
    state.authMethods = data?.authMethods || state.authMethods;
    const account = data.account || {};
    const publicProfile = data.publicProfile || {};
    const set = (id, value) => { const el = $(id); if (el) el.value = value ?? ""; };
    editor.revision = data.profileRevision || publicProfile.updatedAt || null;
    editor.baseline = {
      displayName: publicProfile.displayName || state.me?.username || "",
      bio: publicProfile.bio || "",
      email: account.email || "",
    };
    set("#settings-display-name", editor.baseline.displayName);
    set("#settings-bio", editor.baseline.bio);
    set("#settings-email", editor.baseline.email);
    let draft = readProfileEditorDraft(editor);
    if (draft) {
      set("#settings-display-name", draft.values.displayName);
      set("#settings-bio", draft.values.bio);
      set("#settings-email", draft.values.email);
      if (draft.revision) editor.revision = draft.revision;
    }
    set("#settings-username", account.username || state.me?.username || "");
    set("#settings-role", account.role || state.me?.role || "reader");
    set("#settings-account-status", account.status || "active");
    const emailStatus = $("#settings-email-status");
    if (emailStatus) emailStatus.textContent = account.pendingEmail
      ? "新信箱驗證中，請完成寄到新信箱的驗證。"
      : account.emailVerified ? "信箱已驗證。" : "尚未設定已驗證信箱。";

    const identities = $("#settings-identities");
    if (identities) {
      const linked = account.externalIdentities || [];
      identities.innerHTML = linked.length
        ? linked.map((identity) => `<div class="settings-identity"><span>${esc(identity.provider)}${identity.email ? ` · ${esc(identity.email)}` : ""}</span><button class="btn btn-ghost" type="button" data-unlink-google="${esc(identity.id)}">解除連結</button></div>`).join("")
        : '<p class="settings-muted">尚未連結外部登入方式。</p>';
      identities.querySelectorAll("[data-unlink-google]").forEach((button) => button.addEventListener("click", () => unlinkGoogle(button.dataset.unlinkGoogle)));
      if (!linked.some((identity) => identity.provider === "google")) {
        if (data.authMethods?.google?.available) {
          identities.insertAdjacentHTML("beforeend", '<button id="settings-google-link" class="btn btn-ghost" type="button">連結 Google</button>');
          identities.querySelector("#settings-google-link")?.addEventListener("click", beginGoogleLink);
        } else {
          identities.insertAdjacentHTML("beforeend", '<p class="settings-muted">Google 登入目前尚未開放，請使用帳號密碼。</p>');
        }
      }
    }
    const currentPassword = $("#settings-current-password");
    const passwordSave = $("#settings-password-save");
    if (currentPassword) currentPassword.hidden = !account.hasPasswordCredential;
    if (passwordSave) passwordSave.textContent = account.hasPasswordCredential ? "變更密碼" : "設定密碼";

    if (authorList) {
      const authors = data.authorProfiles || [];
      const entries = authors.map((author) => `<div class="settings-author-entry profile-author-entry" data-profile-id="${esc(author.profileId || "")}"><label for="settings-author-name-${esc(author.profileId)}">作者名稱<input id="settings-author-name-${esc(author.profileId)}" data-author-name maxlength="80" value="${esc(author.displayName || "")}"></label><label for="settings-author-slug-${esc(author.profileId)}">作者網址<input id="settings-author-slug-${esc(author.profileId)}" data-author-slug maxlength="100" value="${esc(author.slug || "")}"></label><label for="settings-author-bio-${esc(author.profileId)}">作者簡介<textarea id="settings-author-bio-${esc(author.profileId)}" data-author-bio maxlength="1000">${esc(author.bio || "")}</textarea></label><label>作者頭像<input data-author-avatar type="file" accept="image/jpeg,image/png,image/webp"></label><button class="btn btn-ghost" type="button" data-author-save>儲存作者 profile</button></div>`).join("");
      const canCreate = ["author", "admin", "super_admin"].includes(account.role);
      const add = canCreate ? '<div class="profile-author-entry profile-author-new"><p class="field-label">新增作者 profile</p><label>作者名稱<input data-new-author-name maxlength="80" placeholder="作者名稱"></label><label>作者網址<input data-new-author-slug maxlength="100" placeholder="作者網址（選填）"></label><label>作者簡介<textarea data-new-author-bio maxlength="1000" placeholder="作者簡介（選填）"></textarea></label><button class="btn btn-ghost" type="button" data-author-create>新增作者 profile</button></div>' : "";
      authorList.innerHTML = entries || add ? `<div class="settings-author-settings"><p class="field-label">作者 profile</p>${entries}${add}</div>` : '<p class="settings-muted">目前還沒有作者 profile；作者申請核准後會建立。</p>';
      authorList.querySelectorAll("[data-author-save]").forEach((button) => button.addEventListener("click", () => saveAuthorProfile(button)));
      authorList.querySelector("[data-author-create]")?.addEventListener("click", () => saveNewAuthorProfile(authorList.querySelector("[data-author-create]")));
    }
    editor.loaded = true;
    editor.loadError = false;
    editor.conflict = false;
    if (status) {
      status.hidden = false;
      status.className = "settings-status settings-status-ok";
      status.textContent = draft ? "已找回未儲存修改，請確認後儲存。" : "設定已載入。";
    }
    updateProfileEditorUi(editor);
  } catch (error) {
    if (!profileEditorCurrent(editor)) return;
    editor.loadError = true;
    if (status) { status.hidden = false; status.className = "settings-status settings-status-error"; status.textContent = `個人設定暫時無法載入：${error.message}。`; }
    updateProfileEditorUi(editor);
  } finally {
    if (profileEditorCurrent(editor)) page.setAttribute("aria-busy", "false");
  }
}

async function saveNewAuthorProfile(button) {
  const entry = button.closest(".profile-author-new");
  button.disabled = true;
  try {
    await api("/api/auth/profile/authors", { method: "POST", body: {
      displayName: entry.querySelector("[data-new-author-name]")?.value || "",
      slug: entry.querySelector("[data-new-author-slug]")?.value || "",
      bio: entry.querySelector("[data-new-author-bio]")?.value || "",
    }});
    toast("作者 profile 已建立", "ok");
    await openProfileSettings();
    await loadAuthorProfileChoices();
  } catch (error) {
    toast("建立作者 profile 失敗：" + error.message, "err");
  } finally { button.disabled = false; }
}

async function saveAuthorProfile(button) {
  const entry = button.closest("[data-profile-id]");
  const id = entry?.dataset.profileId;
  if (!id) return;
  button.disabled = true;
  try {
    await api("/api/auth/profile/authors/" + encodeURIComponent(id), { method: "PATCH", body: {
      displayName: entry.querySelector("[data-author-name]")?.value || "",
      slug: entry.querySelector("[data-author-slug]")?.value || "",
      bio: entry.querySelector("[data-author-bio]")?.value || "",
    }});
    const file = entry.querySelector("[data-author-avatar]")?.files?.[0];
    if (file) {
      const form = new FormData();
      form.append("file", file, file.name);
      await api("/api/auth/profile/authors/" + encodeURIComponent(id) + "/avatar", { method: "POST", body: form });
    }
    toast("作者 profile 已儲存", "ok");
    await openProfileSettings();
  } catch (error) {
    toast("作者 profile 儲存失敗：" + error.message, "err");
  } finally {
    button.disabled = false;
  }
}

async function saveProfileSettings() {
  const pageMode = state.view === "settings";
  if (pageMode) {
    const editor = profileEditor;
    if (!editor || !profileEditorCurrent(editor) || !editor.loaded || editor.busy) return;
    const draftSnapshot = (() => { try { return sessionStorage.getItem(editor.draftKey); } catch (_) { return null; } })();
    const values = profileEditorValues();
    const body = { ...values };
    if (editor.revision) body.expectedProfileRevision = editor.revision;
    editor.busy = true;
    editor.conflict = false;
    const status = $("#settings-status");
    if (status) { status.className = "settings-status"; status.textContent = "正在儲存個人設定，請稍候…"; }
    updateProfileEditorUi(editor);
    try {
      const profile = await api("/api/auth/profile", { method: "PATCH", body });
      if (!profileEditorCurrent(editor)) return;
      const file = $("#settings-avatar-file")?.files?.[0];
      if (file) {
        const form = new FormData();
        form.append("file", file, file.name);
        await api("/api/auth/profile/avatar", { method: "POST", body: form });
        if (!profileEditorCurrent(editor)) return;
        $("#settings-avatar-file").value = "";
      }
      clearProfileEditorDraft(editor, draftSnapshot);
      await loadSettingsPage();
      toast(profile?.account?.pendingEmail ? "設定已儲存，請到新信箱完成驗證" : "個人設定已儲存", "ok");
    } catch (error) {
      if (!profileEditorCurrent(editor)) return;
      editor.conflict = error.status === 409;
      if (status) {
        status.className = "settings-status settings-status-error";
        status.textContent = editor.conflict
          ? "設定已在其他分頁更新；你的修改仍保留，請重新載入最新設定後再決定。"
          : `儲存失敗：${error.message}。你的修改仍保留在此頁。`;
      }
      toast("個人設定未儲存，請查看頁面內的說明", "err");
    } finally {
      if (profileEditorCurrent(editor)) { editor.busy = false; updateProfileEditorUi(editor); }
    }
    return;
  }
  const field = (name) => $(pageMode ? "#settings-" + name : "#profile-" + name);
  const button = $(pageMode ? "#settings-save" : "#profile-save");
  if (button) button.disabled = true;
  try {
    const profile = await api("/api/auth/profile", { method: "PATCH", body: {
      displayName: field("display-name").value.trim(),
      bio: field("bio").value,
      email: field("email").value.trim(),
    }});
    const file = field("avatar-file")?.files?.[0];
    if (file) {
      const form = new FormData();
      form.append("file", file, file.name);
      await api("/api/auth/profile/avatar", { method: "POST", body: form });
      field("avatar-file").value = "";
    }
    if (pageMode) await loadSettingsPage(); else closeAppModal("#profile-modal");
    await loadAuthorProfileChoices();
    toast(profile?.account?.pendingEmail ? "設定已儲存，請到新信箱完成驗證" : "個人設定已儲存", "ok");
  } catch (error) {
    toast("儲存失敗：" + error.message, "err");
  } finally {
    if (button) button.disabled = false;
  }
}

async function beginGoogleLink() {
  if (!state.authMethods?.google?.available) {
    toast("Google 登入目前尚未開放，請使用帳號密碼。", "info");
    return;
  }
  try {
    const result = await api("/api/auth/oauth/google/link/start", {
      method: "POST", body: { returnPath: location.pathname + "#/home" },
    });
    if (!result?.url) throw new Error("OAuth provider 未回傳導向網址");
    window.location.assign(result.url);
  } catch (error) {
    toast("無法開始 Google 連結：" + error.message, "err");
  }
}

async function unlinkGoogle(identityId) {
  if (!window.confirm("確定要解除這個 Google 登入方式嗎？")) return;
  try {
    await api("/api/auth/oauth/google/" + encodeURIComponent(identityId), { method: "DELETE" });
    toast("Google 身份已解除連結", "ok");
    await openProfileSettings();
  } catch (error) {
    toast("解除連結失敗：" + error.message, "err");
  }
}

async function saveProfilePassword() {
  const pageMode = state.view === "settings";
  const button = $(pageMode ? "#settings-password-save" : "#profile-password-save");
  const current = $(pageMode ? "#settings-current-password" : "#profile-current-password");
  const next = $(pageMode ? "#settings-new-password" : "#profile-new-password");
  if (button) button.disabled = true;
  try {
    await api("/api/auth/password", { method: "POST", body: {
      currentPassword: current?.value || "",
      newPassword: next?.value || "",
    }});
    if (current) current.value = "";
    if (next) next.value = "";
    toast("密碼已更新，其他登入 session 已失效", "ok");
    await openProfileSettings();
  } catch (error) {
    toast("密碼更新失敗：" + error.message, "err");
  } finally {
    if (button) button.disabled = false;
  }
}

function bindProfileSettings() {
  $("#btn-profile")?.addEventListener("click", openProfileSettings);
  $("#profile-cancel")?.addEventListener("click", () => closeAppModal("#profile-modal"));
  $("#profile-save")?.addEventListener("click", saveProfileSettings);
  $("#profile-password-save")?.addEventListener("click", saveProfilePassword);
  $("#settings-save")?.addEventListener("click", saveProfileSettings);
  $("#settings-password-save")?.addEventListener("click", saveProfilePassword);
  $("#settings-discard")?.addEventListener("click", () => {
    const editor = profileEditor;
    if (!editor || editor.busy || !profileEditorDirty(editor)) return;
    if (!confirm("放棄目前未儲存的個人設定修改？")) return;
    clearProfileEditorDraft(editor);
    void loadSettingsPage();
  });
  $("#settings-retry")?.addEventListener("click", () => {
    const editor = profileEditor;
    if (!editor || editor.busy) return;
    if (profileEditorDirty(editor) && !confirm("重新載入會捨棄目前的修改，確定繼續？")) return;
    clearProfileEditorDraft(editor);
    void loadSettingsPage();
  });
  ["#settings-display-name", "#settings-bio", "#settings-email", "#settings-avatar-file"].forEach((selector) => {
    const el = $(selector);
    if (!el) return;
    ["input", "change"].forEach((eventName) => el.addEventListener(eventName, () => {
      const editor = profileEditor;
      if (!editor || !profileEditorCurrent(editor) || !editor.loaded || editor.busy) return;
      if (profileEditorDirty(editor)) saveProfileEditorDraft(editor); else clearProfileEditorDraft(editor);
      const status = $("#settings-status");
      if (status && !editor.conflict) {
        status.className = "settings-status";
        status.textContent = profileEditorDirty(editor) ? "有未儲存修改；離開後可在本分頁找回。" : "目前沒有未儲存修改。";
      }
      updateProfileEditorUi(editor);
    }));
  });
  window.addEventListener("beforeunload", (event) => {
    if (profileEditorDirty() || profileEditor?.busy) { event.preventDefault(); event.returnValue = ""; }
  });
}

function openApplyAuthorModal() {
  openAppModal("#apply-author-modal", { focus: "#apply-pen-name" });
  renderApplyAuthorStatus();
}

function applyStatusUi(status, reason) {
  const form = $("#apply-form");
  const box = $("#apply-status");
  if (!status) {
    if (form) form.hidden = false;
    if (box) box.hidden = true;
    return;
  }
  if (status === "pending") {
    if (form) form.hidden = true;
    if (box) {
      box.hidden = false;
      box.innerHTML = `<strong>申請狀態：審核中</strong><p class="modal-subtitle">站方審核通過後，你就可以開始創作。</p>`;
    }
  } else if (status === "approved") {
    if (form) form.hidden = true;
    if (box) {
      box.hidden = false;
      box.innerHTML = `<strong>申請狀態：已通過</strong><p class="modal-subtitle">你現在是創作者了，到<a href="#/mine">我的作品</a>開始建立作品。</p>`;
    }
  } else if (status === "rejected") {
    if (form) form.hidden = false;
    if (box) {
      box.hidden = false;
      box.innerHTML = `<strong>申請狀態：已拒絕</strong>${reason ? `<p class="modal-subtitle">原因：${esc(reason)}</p>` : ""}<p class="modal-subtitle">修正後可以重新送出申請。</p>`;
    }
  }
}

async function renderApplyAuthorStatus() {
  try {
    const res = await api("/api/authors/application");
    const app = res && res.application;
    applyStatusUi(app ? app.status : null, app ? app.reject_reason : "");
  } catch (error) {
    const box = $("#apply-status");
    if (box) { box.hidden = false; box.innerHTML = `<strong>申請狀態暫時無法載入</strong><p class="modal-subtitle">${esc(error.message || "請稍後再試")}</p>`; }
    const form = $("#apply-form");
    if (form) form.hidden = true;
  }
}

async function submitApplyAuthor() {
  if (submitApplyAuthor.inFlight) return;
  if (!$("#apply-rights").checked) { toast("請確認內容為原創或已取得授權", "err"); return; }
  submitApplyAuthor.inFlight = true;
  const submitButton = $("#apply-submit");
  if (submitButton) { submitButton.disabled = true; submitButton.textContent = "送出中…"; }
  const payload = {
    penName: $("#apply-pen-name").value.trim(),
    bio: $("#apply-bio").value.trim(),
    rightsConfirmed: true,
  };
  try {
    const res = await api("/api/authors/apply", { method: "POST", body: payload });
    $("#apply-pen-name").value = ""; $("#apply-bio").value = "";
    toast(res.status === "pending" ? "申請已送出，請等待審核" : `申請狀態：${res.status}`, "ok");
    applyStatusUi(res.status, "");
  } catch (e) { toast(`申請失敗：${e.message}`, "err"); }
  finally {
    submitApplyAuthor.inFlight = false;
    if (submitButton) { submitButton.disabled = false; submitButton.textContent = "送出申請"; }
  }
}

function bindApplyAuthor() {
  $("#nav-apply-author")?.addEventListener("click", openApplyAuthorModal);
  $("#apply-cancel")?.addEventListener("click", () => closeAppModal("#apply-author-modal"));
  $("#apply-submit")?.addEventListener("click", submitApplyAuthor);
}

let bookEditTarget = null;
let bookEditor = null;
let profileEditor = null;

const BOOK_EDITOR_DRAFT_PREFIX = "storylingo.book-metadata-draft";

function bookEditorDraftKey(editor) {
  return `${BOOK_EDITOR_DRAFT_PREFIX}:${editor.accountId ?? "guest"}:${editor.bookId}`;
}

function bookEditorValues() {
  return {
    title: $("#be-title").value.trim(),
    tags: $("#be-tags").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    synopsis: $("#be-synopsis").value.trim(),
    category: $("#be-language-type").value || "vocab",
    categoryId: $("#be-category").value ? Number($("#be-category").value) : null,
    serial: $("#be-serial").value,
    hasPrologue: document.querySelector('input[name="be-prologue"]:checked')?.value !== "false",
  };
}

function bookEditorValuesFromBook(book) {
  return {
    title: book?.title || "",
    tags: Array.isArray(book?.tags) ? book.tags : (book?.tags || "").split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    synopsis: book?.synopsis || "",
    category: book?.category || "vocab",
    categoryId: book?.categoryId || null,
    serial: book?.serial === "完結" ? "完結" : "連載",
    hasPrologue: bookHasPrologue(book),
  };
}

function sameBookEditorValues(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function bookEditorDirty(editor = bookEditor) {
  return !!editor?.loaded && !sameBookEditorValues(bookEditorValues(), editor.baseline);
}

function readBookEditorDraft(editor) {
  try {
    const raw = sessionStorage.getItem(bookEditorDraftKey(editor));
    if (!raw) return null;
    const draft = JSON.parse(raw);
    return draft && draft.values ? draft : null;
  } catch (_) { return null; }
}

function saveBookEditorDraft(editor) {
  if (!editor || !bookEditorDirty(editor)) return;
  try {
    sessionStorage.setItem(bookEditorDraftKey(editor), JSON.stringify({
      values: bookEditorValues(),
      expectedUpdatedAt: editor.expectedUpdatedAt || null,
      expectedMetadataHash: editor.expectedMetadataHash || null,
      savedAt: Date.now(),
    }));
  } catch (_) {}
}

function clearBookEditorDraft(editor, snapshot = null) {
  if (!editor) return;
  try {
    const key = bookEditorDraftKey(editor);
    if (!snapshot || sessionStorage.getItem(key) === snapshot) sessionStorage.removeItem(key);
  } catch (_) {}
}

function applyBookEditorValues(values) {
  $("#be-title").value = values.title || "";
  $("#be-tags").value = Array.isArray(values.tags) ? values.tags.join(", ") : (values.tags || "");
  $("#be-synopsis").value = values.synopsis || "";
  $("#be-language-type").value = values.category || "vocab";
  $("#be-category").value = values.categoryId || "";
  $("#be-serial").value = values.serial === "完結" ? "完結" : "連載";
  const prologueChoice = document.querySelector(`input[name="be-prologue"][value="${values.hasPrologue === false ? "false" : "true"}"]`);
  if (prologueChoice) prologueChoice.checked = true;
}

function updateBookEditorUi(editor = bookEditor) {
  if (!editor || !bookEditorCurrent(editor)) return;
  const form = ["#be-title", "#be-tags", "#be-synopsis", "#be-language-type", "#be-category", "#be-serial",
    'input[name="be-prologue"]'];
  form.forEach((selector) => document.querySelectorAll(selector).forEach((el) => { el.disabled = editor.busy || !editor.loaded || (el.id === "be-language-type" && editor.languageTypeLocked); }));
  const submit = $("#be-submit");
  const cancel = $("#be-cancel");
  if (submit) submit.disabled = editor.busy || !editor.loaded;
  if (cancel) cancel.disabled = editor.busy;
  const retry = $("#book-editor-retry");
  if (retry) retry.hidden = !editor.loadError && !editor.conflict;
}

function bookEditorCurrent(editor) {
  return !!editor && bookEditor === editor && !$("#book-edit-modal")?.hidden &&
    location.hash === editor.route && state.me?.id === editor.accountId;
}

function renderBookEditorBook(editor, book, { restoreDraft = true } = {}) {
  editor.book = book;
  editor.baseline = bookEditorValuesFromBook(book);
  editor.expectedUpdatedAt = book.updated || book.updatedAt || null;
  editor.expectedMetadataHash = book.metadataHash || null;
  editor.languageTypeLocked = book.languageTypeEditable === false;
  $("#book-edit-title").textContent = `編輯：${book.title || "作品"}`;
  $("#be-language-type").innerHTML = languageTypeOptions();
  applyBookEditorValues(editor.baseline);
  const languageTypeHint = $("#be-language-type-hint");
  languageTypeHint.hidden = !editor.languageTypeLocked;
  languageTypeHint.textContent = book.languageTypeLockReason || "作品已有分析資料，無法直接修改語言型別。";
  const draft = restoreDraft ? readBookEditorDraft(editor) : null;
  if (draft) {
    applyBookEditorValues(draft.values);
    if (draft.expectedUpdatedAt) editor.expectedUpdatedAt = draft.expectedUpdatedAt;
    if (draft.expectedMetadataHash) editor.expectedMetadataHash = draft.expectedMetadataHash;
    editor.restoredDraft = true;
  } else {
    editor.restoredDraft = false;
  }
  editor.loaded = true;
  editor.loadError = false;
  editor.conflict = false;
  const status = $("#book-editor-status");
  if (status) status.textContent = draft
    ? "已找回未儲存修改，請確認內容後儲存。"
    : "資料已載入；修改會暫存在此分頁，直到你成功儲存或取消。";
  updateBookEditorUi(editor);
}

async function loadBookEditor(editor, { restoreDraft = true } = {}) {
  editor.loaded = false;
  editor.loadError = false;
  editor.conflict = false;
  updateBookEditorUi(editor);
  try {
    const book = await api(`/api/books/${encodeURIComponent(editor.bookId)}`);
    if (!bookEditorCurrent(editor)) return;
    renderBookEditorBook(editor, book, { restoreDraft });
  } catch (error) {
    if (!bookEditorCurrent(editor)) return;
    editor.loadError = true;
    const status = $("#book-editor-status");
    if (status) status.textContent = `作品資料載入失敗：${error.message}。目前沒有送出任何修改。`;
    updateBookEditorUi(editor);
  }
}

/* ---------- L1-5 modal 無障礙 ---------- */
/* 與 components/Modal.js 同級：role/aria-modal/ESC/Tab trap/focus 還原。
 * #login-modal 為下拉式（非 overlay），不鎖捲動。 */
function openAppModal(sel, { focus = null } = {}) {
  const el = $(sel);
  if (!el) return;
  if (el.dataset.appModalKey) {
    el.hidden = false;
    return;
  }
  const titleEl = el.querySelector(".modal-title, .login-title");
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-modal", "true");
  if (titleEl && titleEl.textContent.trim() && !el.getAttribute("aria-label")) {
    el.setAttribute("aria-label", titleEl.textContent.trim());
  }
  el._appModalPrev = document.activeElement;
  el.hidden = false;
  const onKey = (e) => {
    if (e.key === "Escape") closeAppModal(sel);
    else if (e.key === "Tab") trapAppModal(el, e);
  };
  document.addEventListener("keydown", onKey);
  el.dataset.appModalKey = "1";
  el._appModalCleanup = () => {
    document.removeEventListener("keydown", onKey);
    delete el.dataset.appModalKey;
    const prev = el._appModalPrev;
    if (prev && document.contains(prev)) prev.focus();
  };
  setTimeout(() => {
    const target = focus ? el.querySelector(focus) : el.querySelector("input, button, [href], select, textarea");
    (target || el).focus();
  }, 10);
  if (el.classList.contains("modal-overlay")) document.body.classList.add("modal-open");
}

function closeAppModal(sel, { navigation = false, saved = false } = {}) {
  const el = $(sel);
  if (!el || el.hidden) return;
  if (sel === "#chapter-modal" && chapterEditor) {
    if (!saved && !navigation && chapterEditor.busy) return;
    if (!saved && !navigation && chapterEditorDirty() && !confirm("離開編輯器？未儲存文字會暫存在此分頁，再次開啟可繼續編輯。")) return;
    chapterEditor = null;
    delete el.dataset.unsaved;
  }
  if (sel === "#book-edit-modal" && bookEditor) {
    if (!saved && !navigation && bookEditor.busy) return;
    if (!saved && !navigation && bookEditorDirty() && !confirm("離開作品編輯？未儲存修改會暫存在此分頁，再次開啟可繼續編輯。")) return;
    if (!saved && (navigation || bookEditorDirty())) saveBookEditorDraft(bookEditor);
    bookEditor = null;
    bookEditTarget = null;
  }
  el.hidden = true;
  document.body.classList.remove("modal-open");
  if (el._appModalCleanup) {
    el._appModalCleanup();
    el._appModalCleanup = null;
  }
}

function closeNavigationModals() {
  ["#login-modal", "#register-modal", "#chapter-modal", "#bookstats-modal", "#book-edit-modal", "#newbook-modal", "#profile-modal", "#apply-author-modal", "#ownership-transfer-modal"].forEach((sel) => closeAppModal(sel, { navigation: true }));
}

function trapAppModal(container, event) {
  const focusables = Array.from(container.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])"))
    .filter((node) => !node.disabled && node.getClientRects().length);
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (event.shiftKey && document.activeElement === first) { last.focus(); event.preventDefault(); }
  else if (!event.shiftKey && document.activeElement === last) { first.focus(); event.preventDefault(); }
}

/* FE-017 作者統計：開啟作品數據 */
function openBookStats(b) {
  if (!b) return;
  const modal = $("#bookstats-modal");
  $("#bookstats-title").textContent = `統計：${b.title}`;
  $("#bookstats-body").innerHTML = `<div class="detail-empty">載入中…</div>`;
  openAppModal("#bookstats-modal");
  api(`/api/books/${b.id}/stats`).then((s) => {
    const trendHtml = (s.trend || []).slice(-14).map((t) => `
      <span class="trend-col" title="${esc(t.date)} 閱讀 ${t.reads}">
        <i style="height:${Math.max(6, Math.round((t.reads / (s.reads || 1)) * 100))}%"></i>
      </span>`).join("");
    $("#bookstats-body").innerHTML = `
      <div class="stats-grid">
        <div class="stat-cell"><b>${s.reads}</b><span>閱讀次數</span></div>
        <div class="stat-cell"><b>${s.completions}</b><span>完讀人數</span></div>
        <div class="stat-cell"><b>${Math.round(s.completionRate * 100)}%</b><span>完成率</span></div>
        <div class="stat-cell"><b>${s.uniqueReaders}</b><span>讀者人數</span></div>
        <div class="stat-cell"><b>${s.follows}</b><span>追蹤</span></div>
        <div class="stat-cell"><b>${s.favorites}</b><span>收藏</span></div>
        <div class="stat-cell"><b>${s.audioReady}/${s.chapters}</b><span>音訊就緒</span></div>
      </div>
      <p class="modal-subtitle">近 14 日閱讀趨勢</p>
      <div class="trend-chart">${trendHtml || `<span class="detail-empty">尚無數據</span>`}</div>`;
  }).catch((e) => {
    $("#bookstats-body").innerHTML = `<div class="detail-empty">載入失敗：${esc(e.message)}</div>`;
  });
}

function bindBookStatsModal() {
  const modal = $("#bookstats-modal");
  $("#bookstats-close").addEventListener("click", () => { closeAppModal("#bookstats-modal"); });
  modal.addEventListener("click", (e) => { if (e.target === modal) closeAppModal("#bookstats-modal"); });
}

function openBookEdit(b) {
  if (!b) return;
  const editor = { bookId: b.id, accountId: state.me?.id, route: location.hash, book: b,
    loaded: false, busy: false, loadError: false, conflict: false, languageTypeLocked: false,
    expectedUpdatedAt: b.updated || b.updatedAt || null, baseline: bookEditorValuesFromBook(b) };
  editor.expectedMetadataHash = b.metadataHash || null;
  bookEditor = editor;
  bookEditTarget = b;
  renderBookEditorBook(editor, b);
  editor.loaded = false;
  $("#book-editor-status").textContent = "正在載入最新作品資料…";
  openAppModal("#book-edit-modal", { focus: "#be-title" });
  updateBookEditorUi(editor);
  void loadBookEditor(editor);
}

async function saveBookEdit() {
  const editor = bookEditor;
  if (!editor || !bookEditorCurrent(editor) || !editor.loaded || editor.busy) return;
  const values = bookEditorValues();
  const body = { ...values };
  if (!editor.expectedUpdatedAt) delete body.expectedUpdatedAt;
  else body.expectedUpdatedAt = editor.expectedUpdatedAt;
  if (!editor.expectedMetadataHash) delete body.expectedMetadataHash;
  else body.expectedMetadataHash = editor.expectedMetadataHash;
  if (editor.languageTypeLocked) delete body.category;
  const draftSnapshot = (() => { try { return sessionStorage.getItem(bookEditorDraftKey(editor)); } catch (_) { return null; } })();
  editor.busy = true;
  updateBookEditorUi(editor);
  $("#book-editor-status").textContent = "正在儲存作品資料，請稍候…";
  try {
    const res = await api("/api/books/" + editor.bookId, {
      method: "PUT",
      body,
    });
    clearBookEditorDraft(editor, draftSnapshot);
    if (!bookEditorCurrent(editor)) return;
    editor.book = res || editor.book;
    closeAppModal("#book-edit-modal", { saved: true });
    toast("已儲存", "ok");
    if (state.view === "mine") renderMine();
    else { await loadBooks(); }
    if (state.book && String(state.book.id) === String(res.id)) state.book = res;
  } catch (e) {
    if (!bookEditorCurrent(editor)) return;
    editor.conflict = e.status === 409;
    $("#book-editor-status").textContent = editor.conflict
      ? "作品資料已在其他分頁更新。你的修改仍保留，請重新載入最新資料後再決定。"
      : `儲存失敗：${e.message}。你的修改仍保留在此視窗中。`;
    toast("作品未儲存，請查看編輯器內的說明", "err");
  } finally {
    if (bookEditorCurrent(editor)) { editor.busy = false; updateBookEditorUi(editor); }
  }
}
function bindBookEdit() {
  $("#be-cancel").addEventListener("click", () => {
    if (bookEditor?.busy) return;
    if (bookEditorDirty() && !confirm("取消作品編輯？未儲存修改會被捨棄。")) return;
    clearBookEditorDraft(bookEditor);
    closeAppModal("#book-edit-modal", { saved: true });
  });
  $("#book-editor-retry").addEventListener("click", () => {
    if (!bookEditor || bookEditor.busy) return;
    if ((bookEditorDirty() || bookEditor.conflict) && !confirm("重新載入會捨棄目前視窗中的修改，確定繼續？")) return;
    clearBookEditorDraft(bookEditor);
    void loadBookEditor(bookEditor, { restoreDraft: false });
  });
  $("#be-submit").addEventListener("click", saveBookEdit);
  ["#be-title", "#be-tags", "#be-synopsis", "#be-language-type", "#be-category", "#be-serial",
    'input[name="be-prologue"]'].forEach((selector) => document.querySelectorAll(selector).forEach((el) => el.addEventListener("input", () => {
      if (!bookEditor || !bookEditor.loaded || bookEditor.busy) return;
      if (bookEditorDirty()) saveBookEditorDraft(bookEditor); else clearBookEditorDraft(bookEditor);
      if (!bookEditor.conflict) $("#book-editor-status").textContent = bookEditorDirty()
        ? "有未儲存修改；離開時會保留草稿。"
        : "目前沒有未儲存修改。";
    })));
  $("#book-edit-modal").addEventListener("click", (e) => { if (e.target === $("#book-edit-modal")) closeAppModal("#book-edit-modal"); });
  $("#book-edit-modal").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); $("#be-submit").click(); }
  });
  window.addEventListener("beforeunload", (e) => {
    if (bookEditorDirty() || bookEditor?.busy) { e.preventDefault(); e.returnValue = ""; }
  });
}

/* ---------------- 後台 ---------------- */
const ADMIN_URL_FILTERS = {
  review: [["reviewType", "review_type"], ["reviewStatus", "review_status"]],
  ownership: [["ownershipStatus", "status"]],
  books: [["bookPublication", "publication"], ["bookRequestStatus", "request_status"], ["bookGenerationStatus", "generation_status"]],
  categories: [["categoryEnabled", "enabled"]],
  reports: [["reportStatus", "report_status"], ["reportTargetType", "target_type"]],
  jobs: [["jobsStatus", "status"], ["jobsType", "job_type"]],
  generation: [["generationService", "service_type"], ["generationStatus", "status"]],
  audit: [["auditAction", "action"], ["auditCategory", "category"], ["auditTargetType", "target_type"], ["auditDateFrom", "date_from"], ["auditDateTo", "date_to"]],
  authors: [["authorStatus", "status"]],
  users: [["userRole", "role"], ["userStatus", "status"], ["userAuthorStatus", "author_status"]],
};

function syncAdminUrlState() {
  if (!location.hash.startsWith("#/admin")) return;
  const raw = location.hash.slice(2);
  const qIndex = raw.indexOf("?");
  const params = new URLSearchParams(qIndex >= 0 ? raw.slice(qIndex + 1) : "");
  params.set("tab", state.adminTab);
  const page = Number(state.adminPage[state.adminTab] || 1);
  if (page > 1) params.set("page", String(page)); else params.delete("page");
  for (const [stateKey, queryKey] of ADMIN_URL_FILTERS[state.adminTab] || []) {
    const value = state.adminFilter[stateKey];
    if (value) params.set(queryKey, String(value)); else params.delete(queryKey);
  }
  history.replaceState(null, "", `#/admin?${params.toString()}`);
}

function restoreAdminUrlState(params) {
  for (const [stateKey, queryKey] of ADMIN_URL_FILTERS[state.adminTab] || []) {
    if (params.has(queryKey)) state.adminFilter[stateKey] = params.get(queryKey) || "";
  }
}

function syncAdminTabsForRole() {
  const reviewerOnly = state.me?.role === "reviewer";
  const sharedTabs = new Set(["review", "ownership", "generation"]);
  document.querySelectorAll(".admin-tabs [data-atab]").forEach((tab) => {
    const hidden = reviewerOnly && !sharedTabs.has(tab.dataset.atab);
    tab.hidden = hidden;
    tab.setAttribute("aria-hidden", hidden ? "true" : "false");
  });
}

function setAdminTab(tab) {
  if (state.me?.role === "reviewer" && !["review", "ownership", "generation"].includes(tab)) tab = "review";
  state.adminTab = tab;
  // 讓仍在等待的 dashboard continuation 失效，避免晚到回應重建使用者正在操作的 tab。
  _adminRenderGeneration += 1;
  syncAdminTabsForRole();
  document.querySelectorAll(".admin-tabs .chip").forEach((c) => c.classList.toggle("active", c.dataset.atab === tab));
  // Persist only non-sensitive view state.  Search terms remain in memory and
  // are never copied into a bookmarkable URL.
  syncAdminUrlState();
  renderAdminPanel();
}

async function renderAdmin() {
  const renderGeneration = ++_adminRenderGeneration;
  const isCurrentRender = () => renderGeneration === _adminRenderGeneration && state.view === "admin";
  syncAdminTabsForRole();
  if (state.me?.role === "reviewer") {
    if (!["review", "ownership", "generation"].includes(state.adminTab)) state.adminTab = "review";
    $("#admin-stats").innerHTML = "";
    return renderAdminPanel();
  }
  try {
    const d = await api("/api/admin/dashboard");
    if (!isCurrentRender()) return;
    $("#admin-stats").innerHTML = [
      ["書籍總數", d.books.total],
      ["待審核", (d.books.by_status || {}).submitted || 0],
      ["使用者", d.users.total],
      ["作者", (d.users.by_role || {}).author || 0],
      ["已生成音訊章節", d.chapters.audioReady],
    ].map(([k, v]) => `<div class="stat-card"><div class="stat-num">${v ?? 0}</div><div class="stat-label">${k}</div></div>`).join("");
  } catch (e) {
    if (!isCurrentRender()) return;
    $("#admin-stats").innerHTML = `<div class="ch-hintline">載入統計失敗：${esc(e.message)}</div>`;
  }
  if (!isCurrentRender()) return;
  renderAdminPanel();
}

// 共用分頁條（server-side pagination 的 UI 控制項）
function paginationBar(p) {
  const { page, total, total_pages } = p || {};
  const cur = page || 1;
  const pages = total_pages || 1;
  return `<div class="arev-pager">
    <button class="btn btn-ghost" data-page="1" ${cur <= 1 ? "disabled" : ""} title="第一頁">«</button>
    <button class="btn btn-ghost" data-page="${cur - 1}" ${cur <= 1 ? "disabled" : ""} title="上一頁">‹ 上一頁</button>
    <span class="pager-info">第 ${cur} / ${pages} 頁 · 共 ${total ?? 0} 筆</span>
    <button class="btn btn-ghost" data-page="${cur + 1}" ${cur >= pages ? "disabled" : ""} title="下一頁">下一頁 ›</button>
    <button class="btn btn-ghost" data-page="${pages}" ${cur >= pages ? "disabled" : ""} title="最後一頁">»</button>
  </div>`;
}
let _aiEditId = null;
let _adminRenderGeneration = 0;
let _aiRenderToken = 0;
let _ttsRenderToken = 0;
function bindPager(panel, key) {
  panel.querySelectorAll("[data-page]").forEach((b) =>
    b.addEventListener("click", () => {
      const p = Number(b.dataset.page);
      if (!Number.isFinite(p) || p < 1) return;
      state.adminPage = { ...state.adminPage, [key]: p };
      syncAdminUrlState();
      renderAdminPanel();
    }));
}

const REQUEST_STATUS_LABEL = { SUBMITTED: "待審核", IN_REVIEW: "審核中", APPROVED: "已核准", REJECTED: "已退回", CANCELLED: "已取消", INVALIDATED: "已失效" };
const REQUEST_TYPE_LABEL = { publish: "發布", unpublish: "下架", audiobook: "有聲書" };

async function renderReviewerDetail(panel, requestId, tab = "review") {
  let item;
  try { item = await api(`/api/review/requests/${requestId}`); }
  catch (e) {
    if (state.adminTab !== tab) return;
    panel.innerHTML = `<div class="ch-hintline">申請載入失敗：${esc(e.message)} <button class="btn" id="review-back">返回隊列</button></div>`;
    panel.querySelector("#review-back")?.addEventListener("click", () => renderReviewerQueue(panel, tab));
    return;
  }
  if (state.adminTab !== tab) return;
  const book = item.book || {};
  const chapters = (book.chapters || []).map((chapter) => `<details class="review-chapter"><summary>第 ${chapter.seq} 章 · ${esc(chapter.title || "未命名")}</summary><pre>${esc(chapter.text || "")}</pre></details>`).join("");
  const canDecide = item.status === "IN_REVIEW" && (!item.reviewerAccountId || String(item.reviewerAccountId) === String(state.me?.id));
  panel.innerHTML = `<div class="review-detail">
    <div class="review-detail-head"><button class="btn btn-ghost" id="review-back">← 返回隊列</button><h2>${esc(item.bookTitle || "申請詳細")}</h2></div>
    <p class="arev-sub">申請 #${item.id} · ${REQUEST_TYPE_LABEL[item.requestType] || item.requestType} · ${REQUEST_STATUS_LABEL[item.status] || item.status}</p>
    <p class="arev-sub">作者：${esc(item.requester || "")} · 送出：${esc(item.submittedAt || "")} · Reviewer：${esc(item.reviewer || "尚未認領")}</p>
    <p class="arev-sub">提交 revision：<code>${esc(item.submittedRevision || "")}</code> · 目前 revision：<code>${esc(book.currentRevision || "")}</code></p>
    ${item.decisionReason ? `<div class="arev-reason">決策原因：${esc(item.decisionReason)}</div>` : ""}
     ${item.requestType === "audiobook" ? `<div class="ch-provider-banner">授權狀態：${item.generationAuthorized ? "AUTHORIZED_FOR_GENERATION" : "尚未授權"}${item.operationJobId ? ` · 操作 #${item.operationJobId}` : ""}${item.operationId ? ` <button class="btn btn-ghost" id="review-generation-open">查看生成運維</button>` : ""}</div>` : ""}
    <div class="review-book-content"><h3>作品內容（唯讀）</h3>${chapters || "<p>尚無章節。</p>"}</div>
    <div class="review-detail-actions">
      ${item.status === "SUBMITTED" ? `<button class="btn btn-accent" id="review-start">開始審核</button>` : ""}
      ${canDecide ? `<button class="btn btn-accent" id="review-approve">核准</button><textarea id="review-reason" maxlength="500" placeholder="退回原因（退回時必填）"></textarea><button class="btn btn-danger" id="review-reject">退回</button>` : ""}
      ${item.status === "APPROVED" && item.requestType === "audiobook" && !item.operationJobId ? `<button class="btn btn-accent" id="review-generation">啟動授權生成</button>` : ""}
    </div>
    <h3>事件紀錄</h3><ol class="review-events">${(item.events || []).map((event) => `<li><strong>${esc(event.event_type)}</strong> · ${esc(event.created_at)}${event.reason ? ` · ${esc(event.reason)}` : ""}</li>`).join("")}</ol>
  </div>`;
  panel.querySelector("#review-back")?.addEventListener("click", () => renderReviewerQueue(panel, tab));
  const action = async (path, options = {}, success = "操作完成") => { try { await api(path, { method: "POST", ...options }); toast(success, "ok"); renderReviewerDetail(panel, requestId, tab); } catch (e) { toast(`操作失敗：${e.message}`, "err"); } };
  panel.querySelector("#review-start")?.addEventListener("click", () => action(`/api/review/requests/${requestId}/start`, {}, "已開始審核"));
  panel.querySelector("#review-approve")?.addEventListener("click", () => action(`/api/review/requests/${requestId}/approve`, {}, "申請已核准"));
  panel.querySelector("#review-reject")?.addEventListener("click", () => action(`/api/review/requests/${requestId}/reject`, { body: { reason: panel.querySelector("#review-reason")?.value || "" } }, "申請已退回"));
   panel.querySelector("#review-generation")?.addEventListener("click", () => action(`/api/review/requests/${requestId}/generation`, {}, "已建立生成操作"));
  panel.querySelector("#review-generation-open")?.addEventListener("click", () => { state.adminTab = "generation"; document.querySelectorAll(".admin-tabs .chip").forEach((c) => c.classList.toggle("active", c.dataset.atab === "generation")); renderAdminPanel(); });
}

async function renderReviewerQueue(panel, tab = "review") {
  const f = state.adminFilter;
  const page = state.adminPage.review || 1;
  const qs = new URLSearchParams({ page, page_size: 20 });
  if (f.reviewType) qs.set("request_type", f.reviewType);
  if (f.reviewStatus) qs.set("status", f.reviewStatus);
  panel.innerHTML = `<div class="ch-hintline" style="text-align:center;padding:30px">載入審核隊列中…</div>`;
  let data;
  try { data = await api(`/api/review/requests?${qs}`); }
  catch (e) {
    if (state.adminTab !== tab) return;
    panel.innerHTML = `<div class="ch-hintline">審核隊列載入失敗：${esc(e.message)} <button class="btn" id="review-retry">重試</button></div>`;
    panel.querySelector("#review-retry")?.addEventListener("click", () => renderReviewerQueue(panel, tab));
    return;
  }
  if (state.adminTab !== tab) return;
  const rows = (data.items || []).map((item) => `<div class="arev-row" data-review-id="${item.id}"><div class="arev-info"><strong>${esc(item.bookTitle || item.bookBid || "作品")}</strong><span class="chip status-chip ${String(item.status).toLowerCase()}">${REQUEST_STATUS_LABEL[item.status] || item.status}</span><span class="arev-sub">#${item.id} · ${REQUEST_TYPE_LABEL[item.requestType] || item.requestType} · 作者：${esc(item.requester || "")} · ${esc(item.submittedAt || "")} · Reviewer：${esc(item.reviewer || "尚未認領")}</span></div><div class="arev-actions"><button class="btn" data-review-open="${item.id}">查看詳細</button></div></div>`).join("");
  const totalPages = data.totalPages || 1;
  panel.innerHTML = `<div class="arev-row cat-add review-filters"><div class="arev-info"><select id="review-type"><option value="">全部類型</option><option value="publish" ${f.reviewType === "publish" ? "selected" : ""}>發布</option><option value="unpublish" ${f.reviewType === "unpublish" ? "selected" : ""}>下架</option><option value="audiobook" ${f.reviewType === "audiobook" ? "selected" : ""}>有聲書</option></select><select id="review-status"><option value="">進行中</option>${Object.entries(REQUEST_STATUS_LABEL).map(([value, label]) => `<option value="${value}" ${f.reviewStatus === value ? "selected" : ""}>${label}</option>`).join("")}</select><button class="btn" id="review-filter">套用篩選</button></div></div>${rows || `<div class="ch-hintline" style="text-align:center;padding:30px">目前沒有符合條件的申請。</div>`}<div class="arev-pager"><button class="btn btn-ghost" data-review-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>‹ 上一頁</button><span class="pager-info">第 ${page} / ${totalPages} 頁 · 共 ${data.total || 0} 筆</span><button class="btn btn-ghost" data-review-page="${page + 1}" ${page >= totalPages ? "disabled" : ""}>下一頁 ›</button></div>`;
  panel.querySelector("#review-filter")?.addEventListener("click", () => { state.adminFilter = { ...state.adminFilter, reviewType: panel.querySelector("#review-type").value, reviewStatus: panel.querySelector("#review-status").value }; state.adminPage = { ...state.adminPage, review: 1 }; syncAdminUrlState(); renderReviewerQueue(panel, tab); });
  panel.querySelectorAll("[data-review-page]").forEach((button) => button.addEventListener("click", () => { const next = Number(button.dataset.reviewPage); if (next < 1 || next > totalPages) return; state.adminPage = { ...state.adminPage, review: next }; syncAdminUrlState(); renderReviewerQueue(panel, tab); }));
  panel.querySelectorAll("[data-review-open]").forEach((button) => button.addEventListener("click", () => renderReviewerDetail(panel, button.dataset.reviewOpen, tab)));
}

async function renderGenerationOperations(panel, tab = "generation") {
  const reviewer = state.me?.role === "reviewer";
  const f = state.adminFilter;
  const page = state.adminPage.generation || 1;
  let summary = null;
  if (!reviewer) { try { summary = await api("/api/admin/generation/summary"); } catch (_) { summary = null; } }
  const qs = new URLSearchParams({ page, page_size: 20 });
  if (f.generationService) qs.set("service_type", f.generationService);
  if (f.generationStatus) qs.set("status", f.generationStatus);
  const endpoint = reviewer ? "/api/review/generation/operations" : "/api/admin/generation/operations";
  panel.innerHTML = `<div class="ch-hintline" style="text-align:center;padding:30px">載入生成運維狀態中…</div>`;
  let data;
  try { data = await api(`${endpoint}?${qs}`); }
  catch (e) { panel.innerHTML = `<div class="ch-hintline">生成運維載入失敗：${esc(e.message)} <button class="btn" id="generation-retry-load">重試</button></div>`; panel.querySelector("#generation-retry-load")?.addEventListener("click", () => renderGenerationOperations(panel, tab)); return; }
  if (state.adminTab !== tab) return;
  const rows = (data.items || []).map((op) => {
    const jobs = op.jobs || [];
    const job = op.job || jobs[jobs.length - 1] || {};
    const jobStatus = job.status || op.status;
    const canRetry = !reviewer || ["failed", "stale"].includes(jobStatus);
    const progress = job.progress ?? op.progress;
    return `<div class="arev-row generation-op-row"><div class="arev-info"><strong>操作 #${op.id}</strong><span class="status-chip ${esc(op.status)}">${esc(op.status)}</span><span class="arev-sub">${esc(op.operationType || "generation")} · ${esc(op.serviceType || job.serviceType || "—")} · ${esc(job.providerLabel || "平台路由")} · ${progress == null ? "進度待定" : `進度 ${esc(progress)}%`} · attempts ${esc(job.attempts ?? op.attemptCount ?? 0)}</span><span class="arev-sub">建立：${esc(op.createdAt || "")} ${job.error ? `· ${esc(job.failureCategory || "failed")}：${esc(job.error)}` : ""}</span></div><div class="arev-actions"><button class="btn" data-generation-open="${op.id}">查看詳細</button>${canRetry && ["failed", "stale"].includes(jobStatus) && job.id ? `<button class="btn btn-accent" data-generation-retry="${job.id}">重試</button>` : ""}${["queued", "running"].includes(jobStatus) && job.id ? `<button class="btn btn-ghost" data-generation-cancel="${job.id}">取消</button>` : ""}</div></div>`;
  }).join("");
  const totalPages = data.total_pages || 1;
  const summaryHtml = summary ? `<div class="admin-grid generation-summary">${["AI", "TTS"].map((service) => { const counts = (summary.counts || []).filter((row) => row.service_type === service); const queued = counts.filter((row) => ["pending", "retrying"].includes(row.status)).reduce((n, row) => n + Number(row.n || 0), 0); const running = counts.filter((row) => row.status === "running").reduce((n, row) => n + Number(row.n || 0), 0); return `<div class="stat-card"><div class="stat-num">${queued}</div><div class="stat-label">${service} 排隊 · 執行中 ${running}</div>`; }).join("")}<div class="stat-card"><div class="stat-num">${(summary.workers || []).length}</div><div class="stat-label">活躍 workers · providers ${(summary.providers || []).length}</div></div></div>` : "";
  panel.innerHTML = `${summaryHtml}<div class="arev-row cat-add review-filters"><div class="arev-info"><select id="generation-service-filter"><option value="">AI + TTS</option><option value="AI" ${f.generationService === "AI" ? "selected" : ""}>AI</option><option value="TTS" ${f.generationService === "TTS" ? "selected" : ""}>TTS</option></select><select id="generation-status-filter"><option value="">全部狀態</option>${[["queued", "排隊"], ["running", "執行中"], ["retrying", "等待重試"], ["waiting_dependency", "等待依賴"], ["stale", "來源已變更"], ["failed", "失敗"], ["cancelled", "已取消"], ["ready", "完成"]].map(([value, label]) => `<option value="${value}" ${f.generationStatus === value ? "selected" : ""}>${label}</option>`).join("")}</select><span class="arev-sub">操作狀態由 job/attempt 真實資料推導；Reviewer 看不到 provider secrets。</span></div></div>${rows || `<div class="ch-hintline" style="text-align:center;padding:30px">目前沒有生成操作。</div>`}<div class="arev-pager"><button class="btn btn-ghost" data-generation-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>‹ 上一頁</button><span class="pager-info">第 ${page} / ${totalPages} 頁 · 共 ${data.total || 0} 筆</span><button class="btn btn-ghost" data-generation-page="${page + 1}" ${page >= totalPages ? "disabled" : ""}>下一頁 ›</button></div>`;
  const applyGenerationFilter = () => { state.adminFilter = { ...state.adminFilter, generationService: panel.querySelector("#generation-service-filter").value, generationStatus: panel.querySelector("#generation-status-filter").value }; state.adminPage = { ...state.adminPage, generation: 1 }; syncAdminUrlState(); renderGenerationOperations(panel, tab); };
  panel.querySelector("#generation-service-filter")?.addEventListener("change", applyGenerationFilter);
  panel.querySelector("#generation-status-filter")?.addEventListener("change", applyGenerationFilter);
  panel.querySelectorAll("[data-generation-page]").forEach((button) => button.addEventListener("click", () => { const next = Number(button.dataset.generationPage); if (next >= 1 && next <= totalPages) { state.adminPage = { ...state.adminPage, generation: next }; syncAdminUrlState(); renderGenerationOperations(panel, tab); } }));
  panel.querySelectorAll("[data-generation-open]").forEach((button) => button.addEventListener("click", async () => {
    try { const detail = await api(`${reviewer ? "/api/review" : "/api/admin"}/generation/operations/${button.dataset.generationOpen}`); panel.innerHTML = `<div class="review-detail"><button class="btn btn-ghost" id="generation-back">← 返回生成運維</button><h2>生成操作 #${detail.id}</h2><p class="arev-sub">狀態：${esc(detail.status)} · 類型：${esc(detail.operationType)} · 授權：${esc(detail.authorizationState || "—")}</p><p class="arev-sub">Book #${esc(detail.bookId || "—")} · Request #${esc(detail.requestId || "—")} · source revision：<code>${esc(detail.sourceRevision || "")}</code></p><div class="generation-attempts">${(detail.jobs || []).map((j) => `<div class="arev-row"><div class="arev-info"><strong>#${j.id} · ${esc(j.serviceType || "")} · ${esc(j.providerLabel || "平台路由")}</strong><span class="status-chip ${esc(j.status)}">${esc(j.status)}</span><span class="arev-sub">進度 ${j.progress ?? 0}% · attempts ${j.attempts ?? 0} · ${esc(j.failureCategory || "")}</span>${j.error ? `<span class="arev-sub">錯誤：${esc(j.error)}</span>` : ""}</div></div>${(j.attemptsHistory || []).map((a) => `<div class="arev-sub">Attempt ${a.attemptNumber ?? a.attempt_number ?? "—"} · ${esc(a.outcome || "")} · ${esc(a.failureCategory || a.failure_category || "")}</div>`).join("")}`).join("") || "<p>尚無 job。</p>"}</div></div>`; panel.querySelector("#generation-back")?.addEventListener("click", () => renderGenerationOperations(panel, tab)); } catch (e) { toast(`載入生成操作失敗：${esc(e.message)}`, "err"); }
  }));
  const mutate = async (button, action) => { button.disabled = true; try { await api(`${reviewer ? "/api/review" : "/api/admin"}/generation/jobs/${button.dataset.generationCancel || button.dataset.generationRetry}/${action}`, { method: "POST" }); toast(action === "retry" ? "已排入重試" : "已要求取消", "ok"); renderGenerationOperations(panel, tab); } catch (e) { button.disabled = false; toast(`操作失敗：${e.message}`, "err"); } };
  panel.querySelectorAll("[data-generation-cancel]").forEach((button) => button.addEventListener("click", () => mutate(button, "cancel")));
  panel.querySelectorAll("[data-generation-retry]").forEach((button) => button.addEventListener("click", () => mutate(button, "retry")));
}

async function renderOwnershipTransferQueue(panel, tab = "ownership") {
  const page = state.adminPage.ownership || 1;
  const status = state.adminFilter.ownershipStatus || "";
  const qs = new URLSearchParams({ page, page_size: 20 });
  if (status) qs.set("status", status);
  panel.innerHTML = '<div class="ch-hintline" style="text-align:center;padding:30px">載入所有權轉移隊列中…</div>';
  let data;
  try { data = await api(`/api/review/ownership-transfers?${qs}`); }
  catch (error) {
    panel.innerHTML = `<div class="admin-status-panel admin-status-error" role="alert"><strong>所有權轉移載入失敗</strong><span>${esc(error.message)}</span><button class="btn" id="ownership-retry">重試</button></div>`;
    panel.querySelector("#ownership-retry")?.addEventListener("click", () => renderOwnershipTransferQueue(panel, tab));
    return;
  }
  if (state.adminTab !== tab) return;
  const labels = { REQUESTED: "等待目標", TARGET_ACCEPTED: "等待審核", IN_REVIEW: "審核中", REJECTED: "已拒絕", CANCELLED: "已取消", EXPIRED: "已過期", INVALIDATED: "已失效", COMPLETED: "已完成" };
  const rows = (data.items || []).map((item) => `<div class="arev-row ownership-admin-row">
    <div class="arev-info"><strong>${esc(item.bookTitle || item.bookBid || "作品")}</strong><span class="chip status-chip ${String(item.status).toLowerCase()}">${labels[item.status] || item.status}</span><span class="arev-sub">#${item.id} · ${esc(item.currentOwner || "—")} → ${esc(item.target || "—")} · 申請：${esc(item.requester || "—")}</span><span class="arev-sub">${esc(item.createdAt || "—")} · Reviewer：${esc(item.reviewer || "尚未認領")}</span></div>
    <div class="arev-actions"><button class="btn" data-ownership-open="${item.id}">查看詳細</button>${state.me?.role === "super_admin" && item.bookBid ? `<button class="btn btn-ghost btn-danger" data-ownership-emergency="${esc(item.bookBid)}">緊急轉移</button>` : ""}</div>
  </div>`).join("");
  panel.innerHTML = `<div class="arev-row cat-add review-filters"><div class="arev-info"><select id="ownership-status-filter"><option value="">進行中</option><option value="TARGET_ACCEPTED" ${status === "TARGET_ACCEPTED" ? "selected" : ""}>等待審核</option><option value="IN_REVIEW" ${status === "IN_REVIEW" ? "selected" : ""}>審核中</option></select><span class="arev-sub">所有權完成只更新 Book owner；公開署名與讀者關係保持不變。</span></div><div class="arev-actions"><button class="btn" id="ownership-filter">套用篩選</button></div></div>${rows || '<div class="admin-status-panel admin-status-empty"><strong>目前沒有待處理的所有權轉移</strong><span>目標接受後會出現在這裡。</span></div>'}${paginationBar(data)}`;
  panel.querySelector("#ownership-filter")?.addEventListener("click", () => { state.adminFilter = { ...state.adminFilter, ownershipStatus: panel.querySelector("#ownership-status-filter").value }; state.adminPage = { ...state.adminPage, ownership: 1 }; syncAdminUrlState(); renderOwnershipTransferQueue(panel, tab); });
  bindPager(panel, "ownership");
  panel.querySelectorAll("[data-ownership-open]").forEach((button) => button.addEventListener("click", () => renderOwnershipTransferDetail(panel, button.dataset.ownershipOpen, tab)));
  panel.querySelectorAll("[data-ownership-emergency]").forEach((button) => button.addEventListener("click", () => openOwnershipTransfer(button.dataset.ownershipEmergency, true)));
}

async function renderOwnershipTransferDetail(panel, requestId, tab = "ownership") {
  let item;
  try { item = await api(`/api/review/ownership-transfers/${requestId}`); }
  catch (error) { panel.innerHTML = `<div class="ch-hintline">轉移載入失敗：${esc(error.message)} <button class="btn" id="ownership-back">返回隊列</button></div>`; panel.querySelector("#ownership-back")?.addEventListener("click", () => renderOwnershipTransferQueue(panel, tab)); return; }
  if (state.adminTab !== tab) return;
  const labels = { REQUESTED: "等待目標", TARGET_ACCEPTED: "等待審核", IN_REVIEW: "審核中", REJECTED: "已拒絕", CANCELLED: "已取消", EXPIRED: "已過期", INVALIDATED: "已失效", COMPLETED: "已完成" };
  const canDecide = item.status === "IN_REVIEW" && (!item.reviewerAccountId || String(item.reviewerAccountId) === String(state.me?.id));
  const events = (item.events || []).map((event) => `<li><strong>${esc(event.event_type)}</strong> · ${esc(event.created_at)}${event.actor_username ? ` · ${esc(event.actor_username)}` : ""}${event.reason ? ` · ${esc(event.reason)}` : ""}</li>`).join("");
  panel.innerHTML = `<div class="review-detail ownership-transfer-detail"><button class="btn btn-ghost" id="ownership-back">← 返回所有權轉移</button><h2>${esc(item.bookTitle || item.bookBid || "所有權轉移")}</h2><p class="arev-sub">#${item.id} · ${labels[item.status] || item.status} · ${esc(item.currentOwner || "—")} → ${esc(item.target || "—")}</p><p class="arev-sub">作品狀態：${esc(item.bookStatus || "—")} · source revision：<code>${esc(item.sourceRevision || "")}</code></p>${item.reason ? `<div class="arev-reason">原因：${esc(item.reason)}</div>` : ""}<div class="review-detail-actions">${item.status === "TARGET_ACCEPTED" ? '<button class="btn btn-accent" id="ownership-start">開始審核</button>' : ""}${canDecide ? '<button class="btn btn-accent" id="ownership-approve">核准並完成轉移</button><textarea id="ownership-reason" maxlength="500" placeholder="退回原因（退回時必填）"></textarea><button class="btn btn-danger" id="ownership-reject">退回</button>' : ""}</div><h3>不可變更的轉移歷程</h3><ol class="review-events">${events || "<li>尚無事件。</li>"}</ol></div>`;
  panel.querySelector("#ownership-back")?.addEventListener("click", () => renderOwnershipTransferQueue(panel, tab));
  const action = async (path, options, success) => { try { await api(path, { method: "POST", ...(options || {}) }); toast(success, "ok"); renderOwnershipTransferDetail(panel, requestId, tab); } catch (error) { toast(`操作失敗：${error.message}`, "err"); } };
  panel.querySelector("#ownership-start")?.addEventListener("click", () => action(`/api/review/ownership-transfers/${requestId}/start`, {}, "已開始審核"));
  panel.querySelector("#ownership-approve")?.addEventListener("click", () => action(`/api/review/ownership-transfers/${requestId}/approve`, {}, "所有權已轉移"));
  panel.querySelector("#ownership-reject")?.addEventListener("click", () => action(`/api/review/ownership-transfers/${requestId}/reject`, { body: { reason: panel.querySelector("#ownership-reason")?.value || "" } }, "轉移已退回"));
}

async function renderAdminPanel() {
  const panel = $("#admin-panel");
  const tab = state.adminTab;
  syncAdminUrlState();
  if (state.adminTab === "overview" && window.StoryLingoAdminConsole) {
    return window.StoryLingoAdminConsole.renderOverview(panel, tab);
  } else if (state.adminTab === "review") {
    return renderReviewerQueue(panel, tab);
  } else if (state.adminTab === "ownership") {
    return renderOwnershipTransferQueue(panel, tab);
  } else if (state.adminTab === "generation") {
    return renderGenerationOperations(panel, tab);
  } else if (state.adminTab === "announcements" && window.StoryLingoAdminConsole) {
    return window.StoryLingoAdminConsole.renderAnnouncements(panel, tab);
  } else if (state.adminTab === "books") {
    const f = state.adminFilter;
    const page = state.adminPage.books || 1;
    const qs = new URLSearchParams({ page, page_size: 20, sort: "updated_at", order: "desc" });
    if (f.bookQ) qs.set("q", f.bookQ);
    if (f.bookPublication) qs.set("publication", f.bookPublication);
    if (f.bookRequestStatus) qs.set("request_status", f.bookRequestStatus);
    if (f.bookGenerationStatus) qs.set("generation_status", f.bookGenerationStatus);
    let data = { items: [], total: 0, page: 1, total_pages: 0 };
    try { data = (await api("/api/admin/books?" + qs)) || data; }
    catch (e) {
      panel.innerHTML = `<div class="admin-status-panel admin-status-error" role="alert"><strong>作品清單載入失敗</strong><span>${esc(e.message)}</span><button class="btn" id="admin-books-retry">重試</button></div>`;
      panel.querySelector("#admin-books-retry")?.addEventListener("click", () => renderAdminPanel());
      return;
    }
    if (state.adminTab !== tab) return;
    const rows = (data.items || []).map((book) => {
      const requests = book.requestState || {};
      const activeRequest = ["SUBMITTED", "IN_REVIEW"].some((value) => Object.values(requests).includes(value));
      const publication = book.publicationState === "published" ? "已公開" : esc(book.publicationState || book.status || "未公開");
      return `<div class="arev-row admin-book-row" data-admin-book-row="${esc(book.id)}">
        <div class="arev-info">
          <strong><a href="#/detail/${encodeURIComponent(book.id)}">${esc(book.title || book.id)}</a></strong>
          <span class="chip">${publication}</span><span class="chip">生成：${esc(book.generationState || "none")}</span>
          <span class="arev-sub">Owner：${esc(book.owner || "—")}（Account #${esc(book.ownerId || "—")}） · Attribution：${esc(book.authorProfile?.displayName || "未設定")} · ${esc(book.categoryName || book.category || "未分類")} · ${esc(book.language || "—")}</span>
          <span class="arev-sub">章節 ${esc(book.chapterCount ?? 0)} · 可播放音訊 ${esc(book.audioReadyCount ?? 0)} · Publish ${esc(requests.publish || "—")} · Unpublish ${esc(requests.unpublish || "—")} · Audiobook ${esc(requests.audiobook || "—")}</span>
          <span class="arev-sub">更新：${esc(book.updatedAt || book.createdAt || "—")}</span>
        </div>
        <div class="arev-actions">${activeRequest ? `<button class="btn" data-book-review="${esc(book.id)}">前往審核</button>` : ""}${book.publicationState === "published" ? `<button class="btn btn-ghost btn-danger" data-book-emergency-hide="${esc(book.id)}">緊急隱藏</button>` : ""}</div>
      </div>`;
    }).join("");
    panel.innerHTML = `<div class="arev-row cat-add review-filters"><div class="arev-info"><input id="book-q" placeholder="搜尋標題、摘要、Owner 或作者署名" value="${esc(f.bookQ)}"><select id="book-publication-filter"><option value="">全部公開狀態</option><option value="published" ${f.bookPublication === "published" ? "selected" : ""}>已公開</option><option value="unpublished" ${f.bookPublication === "unpublished" ? "selected" : ""}>未公開</option></select><select id="book-request-filter"><option value="">全部申請狀態</option>${Object.entries(REQUEST_STATUS_LABEL).map(([value, label]) => `<option value="${value}" ${f.bookRequestStatus === value ? "selected" : ""}>${label}</option>`).join("")}</select><select id="book-generation-filter"><option value="">全部生成狀態</option><option value="queued" ${f.bookGenerationStatus === "queued" ? "selected" : ""}>排隊</option><option value="running" ${f.bookGenerationStatus === "running" ? "selected" : ""}>執行中</option><option value="failed" ${f.bookGenerationStatus === "failed" ? "selected" : ""}>失敗</option><option value="ready" ${f.bookGenerationStatus === "ready" ? "selected" : ""}>完成</option></select></div><div class="arev-actions"><button class="btn" id="book-filter">套用篩選</button><button class="btn btn-ghost" id="book-reset">重設</button></div></div>${rows || `<div class="admin-status-panel admin-status-empty"><strong>目前沒有符合條件的作品</strong><span>可調整搜尋或篩選條件後重試。</span></div>`}${paginationBar(data)}`;
    const apply = () => {
      state.adminFilter = { ...state.adminFilter, bookQ: panel.querySelector("#book-q").value.trim(), bookPublication: panel.querySelector("#book-publication-filter").value, bookRequestStatus: panel.querySelector("#book-request-filter").value, bookGenerationStatus: panel.querySelector("#book-generation-filter").value };
      state.adminPage = { ...state.adminPage, books: 1 };
      renderAdminPanel();
    };
    panel.querySelector("#book-filter")?.addEventListener("click", apply);
    panel.querySelector("#book-reset")?.addEventListener("click", () => { state.adminFilter = { ...state.adminFilter, bookQ: "", bookPublication: "", bookRequestStatus: "", bookGenerationStatus: "" }; state.adminPage = { ...state.adminPage, books: 1 }; renderAdminPanel(); });
    panel.querySelectorAll("[data-book-review]").forEach((button) => button.addEventListener("click", () => setAdminTab("review")));
    panel.querySelectorAll("[data-book-emergency-hide]").forEach((button) => button.addEventListener("click", async () => {
      if (!confirm("這是緊急隱藏，不是一般下架。確定要繼續？")) return;
      const reason = prompt("請輸入法律、濫用、安全或緊急治理原因：");
      if (!reason || !reason.trim()) return toast("緊急隱藏需要明確原因", "err");
      button.disabled = true;
      try { await api(`/api/admin/books/${encodeURIComponent(button.dataset.bookEmergencyHide)}/remove`, { method: "POST", body: { reason: reason.trim() } }); toast("作品已緊急隱藏並寫入稽核", "ok"); renderAdminPanel(); }
      catch (e) { button.disabled = false; toast(`緊急隱藏失敗：${e.message}`, "err"); }
    }));
    bindPager(panel, "books");
  } else if (state.adminTab === "categories") {
    const f = state.adminFilter;
    const page = state.adminPage.categories || 1;
    let categoryData = { items: [], categories: [], total: 0, page: 1, total_pages: 0 };
    const categoryParams = new URLSearchParams({ page, page_size: 20, sort: "sort", order: "asc" });
    if (f.categoryQ) categoryParams.set("q", f.categoryQ);
    if (f.categoryEnabled) categoryParams.set("enabled", f.categoryEnabled);
    // Preserve the old exact endpoint for the default first page so existing
    // bookmarks/fixtures continue to work; filtered and later pages use the
    // bounded pagination contract explicitly.
    const categoryPath = page === 1 && !f.categoryQ && !f.categoryEnabled
      ? "/api/admin/categories"
      : `/api/admin/categories?${categoryParams}`;
    try { categoryData = (await api(categoryPath)) || categoryData; } catch (e) {
      panel.innerHTML = `<div class="admin-status-panel admin-status-error" role="alert"><strong>分類載入失敗</strong><span>${esc(e.message)}</span><button class="btn" id="admin-categories-retry">重試</button></div>`;
      panel.querySelector("#admin-categories-retry")?.addEventListener("click", () => renderAdminPanel());
      return;
    }
    const cats = categoryData.items || categoryData.categories || [];
    let books = [];
    // Keep the legacy endpoint shape for existing Admin bookmarks/fixtures;
    // the backend default is still bounded and this bulk picker never loads
    // the full Book detail payload.
    try { const r = await api("/api/admin/books"); books = r.items || (Array.isArray(r) ? r : []); } catch (e) {}
    if (state.adminTab !== tab) return;
    const bookRows = books.map((b) => `
      <div class="arev-row" data-id="${esc(b.id)}">
        <label class="admin-book-select"><input type="checkbox" data-admin-book-select value="${esc(b.id)}" aria-label="選擇 ${esc(b.title)}"></label>
        <div class="arev-info"><strong>${esc(b.title)}</strong><span class="arev-sub">${b.chapterCount ?? 0} 章 · 所有者：${esc(b.owner || b.ownerId || "?")}</span></div>
      </div>`).join("");
    const rows = cats.map((c) => `
      <div class="arev-row">
        <div class="arev-info"><strong>${esc(c.name)}</strong><span class="chip">${c.enabled === false ? "已停用" : "啟用中"}</span><span class="arev-sub">書籍數：${c.bookCount ?? "?"} · 排序：${esc(c.sort ?? 0)}</span></div>
        <div class="arev-actions">
          <button class="btn" data-cat-edit="${c.id}">編輯</button>
          <button class="btn" data-cat-toggle="${c.id}" data-enabled="${c.enabled === false ? 1 : 0}">${c.enabled === false ? "啟用" : "停用"}</button>
          <button class="btn btn-ghost btn-danger" data-cat-del="${c.id}">刪除</button>
        </div>
      </div>`).join("");
    panel.innerHTML = `
      <div class="arev-row cat-add">
        <div class="arev-info"><input id="cat-new" type="text" placeholder="新類別名稱（新增）"></div>
        <div class="arev-actions"><button class="btn btn-accent" id="cat-add">新增</button></div>
      </div>
      <div class="arev-row review-filters" style="border-style:none;padding:6px 2px">
        <div class="arev-info"><input id="category-q" placeholder="搜尋分類" value="${esc(f.categoryQ)}"><select id="category-enabled-filter"><option value="">全部狀態</option><option value="1" ${f.categoryEnabled === "1" ? "selected" : ""}>啟用中</option><option value="0" ${f.categoryEnabled === "0" ? "selected" : ""}>已停用</option></select></div>
        <div class="arev-actions"><button class="btn" id="category-filter">套用篩選</button><button class="btn btn-ghost" id="category-reset">重設</button></div>
      </div>
      ${books.length ? `<div class="arev-row admin-bulk-category"><div class="arev-info"><strong>批次套用分類</strong><span class="arev-sub">勾選書籍後套用；不會變更作品狀態。</span></div><div class="arev-actions"><select id="admin-bulk-category" ${cats.length ? "" : "disabled"}><option value="">選擇分類</option>${cats.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select><button class="btn btn-accent" id="admin-bulk-category-apply" ${cats.length ? "" : "disabled"}>套用分類</button></div></div>${bookRows}` : ""}
      ${rows || `<div class="admin-status-panel admin-status-empty"><strong>目前沒有符合條件的分類</strong><span>可調整搜尋或篩選條件後重試。</span></div>`}
      ${paginationBar(categoryData)}`;
    panel.querySelector("#category-filter")?.addEventListener("click", () => { state.adminFilter = { ...state.adminFilter, categoryQ: panel.querySelector("#category-q").value.trim(), categoryEnabled: panel.querySelector("#category-enabled-filter").value }; state.adminPage = { ...state.adminPage, categories: 1 }; renderAdminPanel(); });
    panel.querySelector("#category-reset")?.addEventListener("click", () => { state.adminFilter = { ...state.adminFilter, categoryQ: "", categoryEnabled: "" }; state.adminPage = { ...state.adminPage, categories: 1 }; renderAdminPanel(); });
    panel.querySelector("#admin-bulk-category-apply")?.addEventListener("click", async (event) => {
      const applyButton = event.currentTarget;
      const selected = [...panel.querySelectorAll("[data-admin-book-select]:checked")].map((input) => input.value);
      const categoryId = panel.querySelector("#admin-bulk-category")?.value;
      if (!selected.length) return toast("請先選擇要套用的書籍", "err");
      if (!categoryId) return toast("請先選擇分類", "err");
      applyButton.disabled = true;
      applyButton.textContent = "套用中…";
      try {
        const result = await api("/api/admin/books/bulk-category", { method: "POST", body: { bookIds: selected, categoryId: Number(categoryId) } });
        toast(`已套用分類至 ${result.updated || selected.length} 本書`, "ok");
        renderAdmin();
      } catch (e) {
        applyButton.disabled = false;
        applyButton.textContent = "套用分類";
        toast(`批次套用分類失敗：${e.message}`, "err");
      }
    });
    $("#cat-add").addEventListener("click", async () => {
      const name = $("#cat-new").value.trim();
      if (!name) return;
      try {
        await api("/api/admin/categories", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
        toast("已新增", "ok");
        renderAdmin();
      } catch (e) { toast(`新增失敗：${e.message}`, "err"); }
    });
    panel.querySelectorAll("[data-cat-del]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!confirm("確定刪除這個類別？")) return;
        try {
          await api("/api/admin/categories/" + btn.dataset.catDel, { method: "DELETE" });
          toast("已刪除", "ok");
          renderAdmin();
        } catch (e) { toast(`刪除失敗：${e.message}`, "err"); }
      }));
    panel.querySelectorAll("[data-cat-toggle]").forEach((btn) => btn.addEventListener("click", async () => {
      try { await api(`/api/admin/categories/${btn.dataset.catToggle}`, { method: "PUT", body: { enabled: Number(btn.dataset.enabled) } }); toast("分類狀態已更新", "ok"); renderAdminPanel(); }
      catch (e) { toast(`更新分類狀態失敗：${e.message}`, "err"); }
    }));
    panel.querySelectorAll("[data-cat-edit]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        const name = prompt("編輯類別：");
        if (!name || !name.trim()) return;
        try {
          await api("/api/admin/categories/" + btn.dataset.catEdit, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name.trim() }) });
          toast("已更新", "ok");
          renderAdmin();
        } catch (e) { toast(`更新失敗：${e.message}`, "err"); }
      }));
    bindPager(panel, "categories");
  } else if (state.adminTab === "banners") {
    const page = state.adminPage.banners || 1;
    let bannerData = { items: [], total: 0, page: 1, total_pages: 0 };
    try { bannerData = (await api(`/api/admin/banners?page=${page}&page_size=20&sort=sort_order&order=asc`)) || bannerData; }
    catch (e) { panel.innerHTML = `<div class="admin-status-panel admin-status-error" role="alert"><strong>輪播載入失敗</strong><span>${esc(e.message)}</span><button class="btn" id="admin-banners-retry">重試</button></div>`; panel.querySelector("#admin-banners-retry")?.addEventListener("click", () => renderAdminPanel()); return; }
    const banners = bannerData.items || [];
    if (state.adminTab !== tab) return;
    panel.innerHTML = `<div class="arev-row cat-add"><div class="arev-info"><input id="banner-title" placeholder="標題"><input id="banner-subtitle" placeholder="副標（選填）"><label class="up-cat-all-label" style="margin-top:8px">輪播圖片（上傳或貼 URL）</label><div class="banner-upload-row"><input type="file" id="banner-file" accept="image/jpeg,image/png,image/webp"><span class="arev-sub" id="banner-file-hint">jpg/png/webp，至少 640x320，最大 5 MB</span></div><input id="banner-image" placeholder="桌面圖片 URL（上傳後自動帶入）"><input id="banner-image-mobile" placeholder="手機圖片 URL（可選）"><select id="banner-link-type"><option value="search">搜尋／站內目標</option><option value="book">作品</option><option value="route">站內 route</option></select><input id="banner-link" placeholder="導向目標（作品 bid、搜尋字串或 #/route）"><input id="banner-alt" placeholder="替代文字（預設使用標題）"><div class="banner-upload-row"><input id="banner-sort" type="number" value="0" min="-100000" max="100000" placeholder="排序"><input id="banner-start" type="datetime-local" aria-label="開始時間"><input id="banner-end" type="datetime-local" aria-label="結束時間"></div></div><div class="arev-actions"><img id="banner-preview" class="banner-thumb" alt="輪播預覽" hidden><button class="btn btn-accent" id="banner-add">新增輪播</button></div></div>${banners.map((b) => `<div class="arev-row" data-banner-id="${b.id}"><div class="arev-info"><strong>${esc(b.title)}</strong><span class="chip">${b.enabled ? "啟用中" : "已停用"}</span><span class="arev-sub">${esc(b.subtitle || "")} · 目標：${esc(b.linkType || b.link_type || "search")} ${esc(b.linkValue || b.link_value || "—")} · 排序 ${esc(b.sortOrder ?? b.sort_order ?? 0)}</span><span class="arev-sub">${b.startAt || b.start_at ? `開始 ${esc(b.startAt || b.start_at)} · ` : ""}${b.endAt || b.end_at ? `結束 ${esc(b.endAt || b.end_at)} · ` : ""}曝光 ${esc(b.impression_count ?? b.impressionCount ?? 0)} · 點擊 ${esc(b.click_count ?? b.clickCount ?? 0)}</span>${(b.image_desktop || b.image_mobile || b.imageDesktop || b.imageMobile) ? `<img class="banner-thumb" src="${esc(b.image_desktop || b.image_mobile || b.imageDesktop || b.imageMobile)}" alt="${esc(b.alt_text || b.altText || b.title)}" loading="lazy">` : ""}</div><div class="arev-actions"><button class="btn" data-banner-toggle="${b.id}" data-enabled="${b.enabled ? 0 : 1}">${b.enabled ? "停用" : "啟用"}</button><button class="btn btn-ghost btn-danger" data-banner-delete="${b.id}">刪除</button></div></div>`).join("")}${paginationBar(bannerData)}`;
    const fileInput = $("#banner-file");
    const imageInput = $("#banner-image");
    const mobileImageInput = $("#banner-image-mobile");
    const preview = $("#banner-preview");
    const fileHint = $("#banner-file-hint");
    if (fileInput) {
      fileInput.addEventListener("change", async () => {
        const file = fileInput.files && fileInput.files[0];
        if (!file) return;
        if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { toast("僅接受 jpg/png/webp", "err"); fileInput.value = ""; return; }
        const url = URL.createObjectURL(file);
        const img = new Image();
        img.onload = async () => {
          if (img.naturalWidth < 640 || img.naturalHeight < 320) { toast("圖片至少 640x320", "err"); URL.revokeObjectURL(url); fileInput.value = ""; return; }
          preview.src = url; preview.hidden = false;
          fileHint.textContent = "上傳中…";
          const fd = new FormData();
          fd.append("file", file);
          try {
            const data = await api("/api/admin/banners/upload", { method: "POST", body: fd });
            imageInput.value = data.desktop;
            if (mobileImageInput) mobileImageInput.value = data.mobile || "";
            fileHint.textContent = "已上傳：桌面版與手機版縮圖已產生，可直接新增輪播。";
            toast("輪播圖片已上傳", "ok");
          } catch (err) { toast(`上傳失敗：${err.message}`, "err"); fileHint.textContent = "上傳失敗，可改貼圖片 URL。"; }
        };
        img.onerror = () => { toast("無法讀取圖片", "err"); URL.revokeObjectURL(url); fileInput.value = ""; };
        img.src = url;
      });
    }
    $("#banner-add").addEventListener("click", async () => {
      const title = $("#banner-title").value.trim();
      const imageDesktop = $("#banner-image").value.trim();
      if (!title || !imageDesktop) return toast("輪播標題與圖片不可為空", "err");
      const start = $("#banner-start")?.value ? new Date($("#banner-start").value).toISOString() : null;
      const end = $("#banner-end")?.value ? new Date($("#banner-end").value).toISOString() : null;
      try { await api("/api/admin/banners", { method: "POST", body: { title, subtitle: $("#banner-subtitle").value.trim(), imageDesktop, imageMobile: $("#banner-image-mobile").value.trim(), linkType: $("#banner-link-type").value, linkValue: $("#banner-link").value.trim(), altText: $("#banner-alt").value.trim() || title, sortOrder: Number($("#banner-sort").value || 0), startAt: start, endAt: end, enabled: true } }); toast("輪播已新增", "ok"); renderAdminPanel(); } catch (e) { toast(`新增失敗：${e.message}`, "err"); }
    });
    panel.querySelectorAll("[data-banner-toggle]").forEach((button) => button.addEventListener("click", async () => { try { await api(`/api/admin/banners/${button.dataset.bannerToggle}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: Number(button.dataset.enabled) }) }); renderAdminPanel(); } catch (e) { toast(`更新失敗：${e.message}`, "err"); } }));
    panel.querySelectorAll("[data-banner-delete]").forEach((button) => button.addEventListener("click", async () => { if (!confirm("確定刪除輪播？")) return; try { await api(`/api/admin/banners/${button.dataset.bannerDelete}`, { method: "DELETE" }); renderAdminPanel(); } catch (e) { toast(`刪除失敗：${e.message}`, "err"); } }));
    bindPager(panel, "banners");
  } else if (state.adminTab === "jobs") {
    const f = state.adminFilter;
    const page = state.adminPage.jobs || 1;
    const qs = new URLSearchParams({ page, page_size: 20 });
    if (f.jobsStatus) qs.set("status", f.jobsStatus);
    if (f.jobsType) qs.set("job_type", f.jobsType);
    let data = { items: [], total: 0, page: 1, total_pages: 0 };
    try { data = (await api("/api/admin/jobs?" + qs)) || data; } catch (e) { panel.innerHTML = `<div class="ch-hintline">載入任務失敗：${esc(e.message)}</div>`; return; }
    if (state.adminTab !== tab) return;
    const rows = (data.items || []).map((job) => {
      const terminal = ["success", "failed", "cancelled"].includes(job.status);
      return `<div class="arev-row" data-job-id="${job.id}"><div class="arev-info"><strong>#${job.id} ${esc(job.job_type)}</strong><span class="status-chip ${job.status}">${esc(job.status)}</span><span class="arev-sub">${esc(job.created_at)} · ${job.progress}% ${job.error ? `· ${esc(job.error)}` : ""}</span></div><div class="arev-actions">${terminal ? `<button class="btn btn-ghost btn-danger" data-job-clear="${job.id}">清除</button>` : ""}</div></div>`;
    }).join("");
    panel.innerHTML = `
      <div class="arev-row cat-add" style="border-style:none;padding:6px 2px">
        <div class="arev-info">
          <select id="job-status-filter"><option value="">全部狀態</option><option value="pending" ${f.jobsStatus==="pending"?"selected":""}>待處理</option><option value="running" ${f.jobsStatus==="running"?"selected":""}>執行中</option><option value="success" ${f.jobsStatus==="success"?"selected":""}>成功</option><option value="failed" ${f.jobsStatus==="failed"?"selected":""}>失敗</option><option value="cancelled" ${f.jobsStatus==="cancelled"?"selected":""}>已取消</option></select>
          <span class="arev-sub" style="margin-left:8px">僅限清除 已完成/失敗/已取消 的歷史任務；不影響已生成音訊。</span>
        </div>
        <div class="arev-actions">
          <button class="btn btn-ghost" id="job-clear-success" ${(data.total||0)===0?"disabled":""}>批次清除已完成</button>
          <button class="btn btn-ghost" id="job-clear-failed" ${(data.total||0)===0?"disabled":""}>批次清除失敗/取消</button>
        </div>
      </div>
      ${rows || `<div class="ch-hintline" style="text-align:center;padding:30px">目前沒有符合條件的生成任務。</div>`}
      ${paginationBar(data)}`;
    $("#job-status-filter")?.addEventListener("change", (e) => { state.adminFilter = { ...state.adminFilter, jobsStatus: e.target.value }; state.adminPage = { ...state.adminPage, jobs: 1 }; renderAdminPanel(); });
    $("#job-clear-success")?.addEventListener("click", async () => {
      if (!confirm("確定清除所有「已完成」的任務歷史？不會刪除已生成音訊。")) return;
      try { await api("/api/admin/jobs/clear", { method: "POST", body: { scope: "success" } }); toast("已清除已完成任務歷史", "ok"); renderAdminPanel(); }
      catch (e) { toast(`清除失敗：${e.message}`, "err"); }
    });
    $("#job-clear-failed")?.addEventListener("click", async () => {
      if (!confirm("確定清除所有「失敗／已取消」的任務歷史？不會刪除已生成音訊。")) return;
      try { const r = await api("/api/admin/jobs/clear", { method: "POST", body: { scope: "failed" } }); toast(`已清除失敗/取消任務歷史（${r.cleared ?? "已處理"} 筆）`, "ok"); renderAdminPanel(); }
      catch (e) { toast(`清除失敗：${e.message}`, "err"); }
    });
    panel.querySelectorAll("[data-job-clear]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm("確定清除這筆任務歷史？不會刪除已生成音訊。")) return;
      try { await api(`/api/admin/jobs/${b.dataset.jobClear}`, { method: "DELETE" }); toast("任務歷史已清除", "ok"); renderAdminPanel(); }
      catch (e) { toast(`清除失敗：${e.message}`, "err"); }
    }));
    bindPager(panel, "jobs");
  } else if (state.adminTab === "tts") {
    const ttsRenderToken = ++_ttsRenderToken;
    let providers = [];
    try { providers = (await api("/api/admin/tts/providers")).items || []; }
    catch (e) { panel.innerHTML = `<div class="ch-hintline">載入 TTS provider 失敗：${esc(e.message)}</div>`; return; }
    if (state.adminTab !== tab || ttsRenderToken !== _ttsRenderToken) return;
    panel.innerHTML = `
      <div class="arev-row cat-add">
        <div class="arev-info">
          <strong>新增遠端 TTS 服務</strong>
          <span class="arev-sub">配置前可參考 <a href="/api/tts/spec" download>Provider API v1 規格</a></span>
          <input id="tts-name" placeholder="服務名稱，例如：主力語音服務">
          <input id="tts-url" placeholder="HTTPS API Base URL，例如：https://tts.example.com">
          <div class="banner-upload-row"><select id="tts-type"><option value="generic_http">Generic HTTP</option><option value="openai_compatible">OpenAI Compatible</option></select><select id="tts-adapter"><option value="generic_http">Legacy HTTP adapter</option><option value="cosyvoice_http">Provider API v1</option></select><select id="tts-auth"><option value="bearer">Bearer Token</option><option value="x-api-key">X-API-Key</option><option value="none">無認證</option></select></div>
          <div class="banner-upload-row"><input id="tts-voices-path" value="/voices" placeholder="Voice API path"><input id="tts-synth-path" value="/synthesize" placeholder="Synthesis API path"></div>
          <div class="banner-upload-row"><input id="tts-max-concurrency" type="number" min="1" max="100" value="1" placeholder="最大同時數"><span class="arev-sub">容量上限（1–100）</span></div>
          <input id="tts-key" type="password" autocomplete="new-password" placeholder="API Key（只會加密保存，不會顯示）">
        </div>
        <div class="arev-actions"><button class="btn btn-accent" id="tts-add">新增服務</button></div>
      </div>
      ${providers.length ? providers.map((p) => {
        const connLabel = p.lastStatus === "ok" ? "可用" : esc(p.lastStatus || "unknown");
        const connError = p.lastStatus !== "ok" && p.lastError ? ` · ${esc(p.lastError)}` : "";
        const adapterLabel = p.adapterKey === "cosyvoice_http" ? "Provider API v1" : (p.adapterKey || p.providerType);
        const capLabel = p.capabilitiesStatus === "supported" ? "支援 capabilities"
          : p.capabilitiesStatus === "unsupported" ? "不支援 capabilities" : "";
        const capText = capLabel ? ` · ${capLabel}` : "";
        return `<div class="arev-row" data-tts-id="${p.id}"><div class="arev-info"><strong>${esc(p.name)}</strong><span class="chip">${p.enabled ? "啟用" : "停用"}${p.isDefault ? " · 預設" : ""}</span><span class="arev-sub">${esc(p.baseUrl)} · ${esc(adapterLabel)} · ${p.hasSecret ? "已設定金鑰" : "未設定金鑰"}</span><span class="arev-sub">容量：${esc(p.activeCount ?? 0)} / ${esc(p.maxConcurrency ?? 1)} · 排隊：${esc(p.queueCount ?? 0)} · 可用：${esc(p.availableCapacity ?? 0)} · ${p.providerScope === "local" ? "本機" : "遠端"}</span><span class="arev-sub">連線狀態：${connLabel}${connError}${capText}${p.lastCheckedAt ? ` · ${esc(p.lastCheckedAt)}` : ""}</span></div><div class="arev-actions"><button class="btn" data-tts-test="${p.id}">測試連線</button><button class="btn" data-tts-edit="${p.id}">編輯</button><button class="btn" data-tts-default="${p.id}" ${p.isDefault ? "disabled" : ""}>設為預設</button><button class="btn" data-tts-toggle="${p.id}" data-enabled="${p.enabled ? 0 : 1}">${p.enabled ? "停用" : "啟用"}</button><button class="btn btn-ghost btn-danger" data-tts-delete="${p.id}">刪除</button></div></div>`;
      }).join("") : `<div class="ch-hintline" style="text-align:center;padding:30px">尚未設定遠端 TTS 服務。</div>`}`;
     const syncTtsAdapterDefaults = () => {
       const adapter = $("#tts-adapter")?.value || "generic_http";
       if ($("#tts-auth")) $("#tts-auth").value = "x-api-key";
       if ($("#tts-voices-path")) $("#tts-voices-path").value = adapter === "cosyvoice_http" ? "/v1/voices" : "/voices";
       if ($("#tts-synth-path")) $("#tts-synth-path").value = adapter === "cosyvoice_http" ? "/v1/synthesize" : "/synthesize";
     };
     $("#tts-adapter")?.addEventListener("change", syncTtsAdapterDefaults);
     syncTtsAdapterDefaults();
     $("#tts-add")?.addEventListener("click", async () => {
      const name = $("#tts-name").value.trim();
      if (!name) { toast("請填寫服務名稱", "err"); return; }
      const payload = { name, baseUrl: $("#tts-url").value.trim(), providerType: $("#tts-type").value, adapterKey: $("#tts-adapter").value, authScheme: $("#tts-auth").value, voicesPath: $("#tts-voices-path").value.trim(), synthPath: $("#tts-synth-path").value.trim(), maxConcurrency: Number($("#tts-max-concurrency").value || 1), apiKey: $("#tts-key").value };
      try { await api("/api/admin/tts/providers", { method: "POST", body: payload }); toast("TTS 服務已新增", "ok"); renderAdminPanel(); }
      catch (e) { toast(`新增 TTS 服務失敗：${e.message}`, "err"); }
    });
    panel.querySelectorAll("[data-tts-test]").forEach((btn) => btn.addEventListener("click", async () => {
      btn.disabled = true;
      try { const result = await api(`/api/admin/tts/providers/${btn.dataset.ttsTest}/test`, { method: "POST" }); toast(result.ok ? `連線成功，取得 ${(result.voices || []).length} 個聲音` : `連線失敗：${result.error}`, result.ok ? "ok" : "err"); renderAdminPanel(); }
      catch (e) { toast(`測試失敗：${e.message}`, "err"); btn.disabled = false; }
    }));
    panel.querySelectorAll("[data-tts-toggle]").forEach((btn) => btn.addEventListener("click", async () => {
      try { await api(`/api/admin/tts/providers/${btn.dataset.ttsToggle}`, { method: "PUT", body: { enabled: Number(btn.dataset.enabled) } }); renderAdminPanel(); }
      catch (e) { toast(`更新 TTS 服務失敗：${e.message}`, "err"); }
    }));
    panel.querySelectorAll("[data-tts-default]").forEach((btn) => btn.addEventListener("click", async () => {
      try { await api(`/api/admin/tts/providers/${btn.dataset.ttsDefault}`, { method: "PUT", body: { isDefault: true } }); toast("已設為預設 TTS 服務", "ok"); renderAdminPanel(); }
      catch (e) { toast(`設定預設服務失敗：${e.message}`, "err"); }
    }));
    panel.querySelectorAll("[data-tts-edit]").forEach((btn) => btn.addEventListener("click", async () => {
      const provider = providers.find((item) => String(item.id) === String(btn.dataset.ttsEdit));
      if (!provider) return;
      const name = prompt("服務名稱：", provider.name);
      if (name === null) return;
      const baseUrl = prompt("TTS API Base URL：", provider.baseUrl);
      if (baseUrl === null) return;
      const apiKey = prompt("新的 API Key（留空代表不更換）：");
      if (apiKey === null) return;
      const maxConcurrency = prompt("最大同時處理數（1–100）：", String(provider.maxConcurrency || 1));
      if (maxConcurrency === null) return;
      try { await api(`/api/admin/tts/providers/${provider.id}`, { method: "PUT", body: { name: name.trim(), baseUrl: baseUrl.trim(), apiKey, maxConcurrency: Number(maxConcurrency), expectedConfigVersion: provider.configVersion } }); toast("TTS 服務已更新", "ok"); renderAdminPanel(); }
      catch (e) { toast(`更新 TTS 服務失敗：${e.message}`, "err"); }
    }));
    panel.querySelectorAll("[data-tts-delete]").forEach((btn) => btn.addEventListener("click", async () => {
      if (!confirm("確定刪除這個 TTS 服務？")) return;
      try { await api(`/api/admin/tts/providers/${btn.dataset.ttsDelete}`, { method: "DELETE" }); toast("TTS 服務已刪除", "ok"); renderAdminPanel(); }
      catch (e) { toast(`刪除 TTS 服務失敗：${e.message}`, "err"); }
    }));
  } else if (state.adminTab === "ai") {
    const aiRenderToken = ++_aiRenderToken;
    let providers = [];
    try { providers = (await api("/api/admin/ai/providers")).items || []; }
    catch (e) { panel.innerHTML = `<div class="ch-hintline">載入 AI provider 失敗：${esc(e.message)}</div>`; return; }
    if (state.adminTab !== tab || aiRenderToken !== _aiRenderToken) return;
    const editing = providers.find((p) => String(p.id) === String(_aiEditId)) || null;
    const formLabel = editing ? `編輯 AI 服務：${esc(editing.name)}` : "新增 AI 服務";
    const submitLabel = editing ? "儲存變更" : "新增服務";
    panel.innerHTML = `
      <div class="arev-row cat-add">
        <div class="arev-info">
          <strong>${formLabel}</strong>
          <input id="ai-name" placeholder="服務名稱，例如：主力 AI 分析" value="${editing ? esc(editing.name) : ""}">
          <input id="ai-url" placeholder="API Base URL，例如：https://api.openai.com/v1" value="${editing ? esc(editing.baseUrl) : ""}">
          <div class="banner-upload-row">
            <select id="ai-type">${["openai_compatible", "openai", "deepseek"].map((t) => `<option value="${t}" ${editing && editing.providerType === t ? "selected" : ""}>${t}</option>`).join("")}</select>
            <input id="ai-model" placeholder="Model，例如：gpt-4o-mini" value="${editing ? esc(editing.model) : ""}">
          </div>
          <input id="ai-fallback" placeholder="Fallback Model（選填）" value="${editing ? esc(editing.fallbackModel || "") : ""}">
          <div class="banner-upload-row"><input id="ai-max-concurrency" type="number" min="1" max="100" value="${editing ? esc(editing.maxConcurrency || 1) : "1"}" placeholder="最大同時數"><span class="arev-sub">容量上限（1–100）</span></div>
          <input id="ai-key" type="password" autocomplete="new-password" placeholder="${editing ? "新的 API Key（留空代表不更換）" : "API Key（只會加密保存，不會顯示）"}">
          <label class="prefs-tog">啟用<input type="checkbox" id="ai-enabled" ${!editing || editing.enabled ? "checked" : ""}></label>
          <label class="prefs-tog">設為預設<input type="checkbox" id="ai-default" ${editing && editing.isDefault ? "checked" : ""}></label>
        </div>
        <div class="arev-actions">
          <button class="btn btn-accent" id="ai-submit">${submitLabel}</button>
          ${editing ? `<button class="btn btn-ghost" id="ai-cancel">取消編輯</button>` : ""}
        </div>
      </div>
      ${providers.length ? providers.map((p) => {
        const connLabel = p.lastStatus === "ok" ? "可用" : esc(p.lastStatus || "unknown");
        const connError = p.lastStatus !== "ok" && p.lastError ? ` · ${esc(p.lastError)}` : "";
        return `<div class="arev-row" data-ai-id="${p.id}"><div class="arev-info"><strong>${esc(p.name)}</strong><span class="chip">${p.enabled ? "啟用" : "停用"}${p.isDefault ? " · 預設" : ""}</span><span class="arev-sub">${esc(p.providerType)} · ${esc(p.baseUrl)} · ${esc(p.model)}${p.fallbackModel ? `（fallback：${esc(p.fallbackModel)}）` : ""} · ${p.hasSecret ? "已設定金鑰" : "未設定金鑰"}</span><span class="arev-sub">容量：${esc(p.activeCount ?? 0)} / ${esc(p.maxConcurrency ?? 1)} · 排隊：${esc(p.queueCount ?? 0)} · 可用：${esc(p.availableCapacity ?? 0)} · ${esc(p.healthState || "unknown")}</span><span class="arev-sub">連線狀態：${connLabel}${connError}${p.lastCheckedAt ? ` · ${esc(p.lastCheckedAt)}` : ""}</span></div><div class="arev-actions"><button class="btn" data-ai-test="${p.id}">測試連線</button><button class="btn" data-ai-edit="${p.id}">編輯</button><button class="btn" data-ai-default="${p.id}" ${p.isDefault ? "disabled" : ""}>設為預設</button><button class="btn" data-ai-toggle="${p.id}" data-enabled="${p.enabled ? 0 : 1}">${p.enabled ? "停用" : "啟用"}</button><button class="btn btn-ghost btn-danger" data-ai-delete="${p.id}">刪除</button></div></div>`;
      }).join("") : `<div class="ch-hintline" style="text-align:center;padding:30px">尚未設定 AI 分析服務。</div>`}`;
    $("#ai-submit")?.addEventListener("click", async () => {
      const name = $("#ai-name").value.trim();
      const baseUrl = $("#ai-url").value.trim();
      const model = $("#ai-model").value.trim();
      if (!name) { toast("請填寫服務名稱", "err"); return; }
      if (!baseUrl) { toast("請填寫 API Base URL", "err"); return; }
      if (!model) { toast("請填寫 Model", "err"); return; }
      const body = {
        name, providerType: $("#ai-type").value, baseUrl, model,
        fallbackModel: $("#ai-fallback").value.trim() || null,
        maxConcurrency: Number($("#ai-max-concurrency").value || 1),
        enabled: $("#ai-enabled").checked, isDefault: $("#ai-default").checked,
      };
      if (editing) body.expectedConfigVersion = editing.configVersion;
      const newKey = $("#ai-key").value;
      if (newKey) body.apiKey = newKey;
      try {
        if (editing) { await api(`/api/admin/ai/providers/${editing.id}`, { method: "PUT", body }); toast("AI 服務已更新", "ok"); }
        else { await api("/api/admin/ai/providers", { method: "POST", body }); toast("AI 服務已新增", "ok"); }
        _aiEditId = null;
        renderAdminPanel();
      } catch (e) { toast(`儲存 AI 服務失敗：${e.message}`, "err"); }
    });
    $("#ai-cancel")?.addEventListener("click", () => { _aiEditId = null; renderAdminPanel(); });
    panel.querySelectorAll("[data-ai-edit]").forEach((btn) => btn.addEventListener("click", () => { _aiEditId = btn.dataset.aiEdit; renderAdminPanel(); }));
    panel.querySelectorAll("[data-ai-test]").forEach((btn) => btn.addEventListener("click", async () => {
      btn.disabled = true;
      try { const result = await api(`/api/admin/ai/providers/${btn.dataset.aiTest}/test`, { method: "POST" }); toast(result.ok ? `連線成功，取得 ${result.models} 個模型` : `連線失敗：${result.error}`, result.ok ? "ok" : "err"); renderAdminPanel(); }
      catch (e) { toast(`測試失敗：${e.message}`, "err"); btn.disabled = false; }
    }));
    panel.querySelectorAll("[data-ai-default]").forEach((btn) => btn.addEventListener("click", async () => {
      try { await api(`/api/admin/ai/providers/${btn.dataset.aiDefault}`, { method: "PUT", body: { isDefault: true } }); toast("已設為預設 AI 服務", "ok"); renderAdminPanel(); }
      catch (e) { toast(`設定預設服務失敗：${e.message}`, "err"); }
    }));
    panel.querySelectorAll("[data-ai-toggle]").forEach((btn) => btn.addEventListener("click", async () => {
      try { await api(`/api/admin/ai/providers/${btn.dataset.aiToggle}`, { method: "PUT", body: { enabled: Number(btn.dataset.enabled) } }); renderAdminPanel(); }
      catch (e) { toast(`更新 AI 服務失敗：${e.message}`, "err"); }
    }));
    panel.querySelectorAll("[data-ai-delete]").forEach((btn) => btn.addEventListener("click", async () => {
      if (!confirm("確定刪除這個 AI 服務？")) return;
      try { await api(`/api/admin/ai/providers/${btn.dataset.aiDelete}`, { method: "DELETE" }); toast("AI 服務已刪除", "ok"); _aiEditId = null; renderAdminPanel(); }
      catch (e) { toast(`刪除 AI 服務失敗：${e.message}`, "err"); }
    }));
  } else if (state.adminTab === "audit") {
    const f = state.adminFilter;
    const page = state.adminPage.audit || 1;
    const qs = new URLSearchParams({ page, page_size: 50 });
    if (f.auditAction) qs.set("action", f.auditAction);
    if (f.auditActor) qs.set("actor", f.auditActor);
    if (f.auditCategory) qs.set("category", f.auditCategory);
    if (f.auditTargetType) qs.set("target_type", f.auditTargetType);
    if (f.auditDateFrom) qs.set("date_from", f.auditDateFrom);
    if (f.auditDateTo) qs.set("date_to", f.auditDateTo);
    let data = { items: [], total: 0, page: 1, total_pages: 0 };
    try { data = (await api("/api/admin/audit-logs?" + qs)) || data; } catch (e) { panel.innerHTML = `<div class="ch-hintline">載入稽核紀錄失敗：${esc(e.message)}</div>`; return; }
    if (state.adminTab !== tab) return;
    const rows = (data.items || []).map((item) => `<div class="arev-row"><div class="arev-info"><strong>${esc(item.action)}</strong><span class="arev-sub">${esc(item.actorLabel || item.username || "系統")} · ${esc(item.target_type || item.targetType)} ${esc(item.target_id || item.targetId)} · ${esc(item.created_at || item.createdAt)}</span><span class="arev-sub">${esc(item.detailsText || item.details || "{}")}</span></div><div class="arev-actions">${item.id ? `<button class="btn btn-ghost" data-audit-open="${esc(item.id)}">查看詳情</button>` : ""}</div></div>`).join("");
    const canExportAudit = state.me?.role === "super_admin";
    panel.innerHTML = `
      <div class="arev-row cat-add" style="border-style:none;padding:6px 2px">
        <div class="arev-info">
          <input id="audit-action" placeholder="動作名稱（如 create_tts_provider）" value="${esc(f.auditAction)}">
          <input id="audit-actor" placeholder="操作者帳號" value="${esc(f.auditActor)}">
          <select id="audit-category"><option value="">全部類別</option><option value="security" ${f.auditCategory === "security" ? "selected" : ""}>安全</option><option value="governance" ${f.auditCategory === "governance" ? "selected" : ""}>治理</option><option value="operations" ${f.auditCategory === "operations" ? "selected" : ""}>運維</option><option value="content" ${f.auditCategory === "content" ? "selected" : ""}>內容</option><option value="system" ${f.auditCategory === "system" ? "selected" : ""}>系統</option></select>
          <input id="audit-target-type" placeholder="目標類型" value="${esc(f.auditTargetType)}">
          <input id="audit-date-from" type="date" aria-label="稽核開始日期" value="${esc(f.auditDateFrom)}"><input id="audit-date-to" type="date" aria-label="稽核結束日期" value="${esc(f.auditDateTo)}">
        </div>
        <div class="arev-actions"><button class="btn" id="audit-filter">套用篩選</button><button class="btn btn-ghost" id="audit-reset">重設</button>${canExportAudit ? `<button class="btn btn-accent" id="audit-export">匯出稽核</button>` : ""}</div>
      </div>
      ${rows || `<div class="ch-hintline" style="text-align:center;padding:30px">尚無稽核紀錄。</div>`}
      ${paginationBar(data)}`;
    $("#audit-filter")?.addEventListener("click", () => { state.adminFilter = { ...state.adminFilter, auditAction: $("#audit-action").value.trim(), auditActor: $("#audit-actor").value.trim(), auditCategory: $("#audit-category").value, auditTargetType: $("#audit-target-type").value.trim(), auditDateFrom: $("#audit-date-from").value, auditDateTo: $("#audit-date-to").value }; state.adminPage = { ...state.adminPage, audit: 1 }; renderAdminPanel(); });
    $("#audit-reset")?.addEventListener("click", () => { state.adminFilter = { ...state.adminFilter, auditAction: "", auditActor: "", auditCategory: "", auditTargetType: "", auditDateFrom: "", auditDateTo: "" }; state.adminPage = { ...state.adminPage, audit: 1 }; renderAdminPanel(); });
    $("#audit-export")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const result = await api("/api/admin/audit-logs/export", { method: "POST", body: {
          action: f.auditAction, actor: f.auditActor, category: f.auditCategory,
          target_type: f.auditTargetType, date_from: f.auditDateFrom, date_to: f.auditDateTo,
          format: "json", max_rows: 1000,
        } });
        const blob = new Blob([JSON.stringify(result || {}, null, 2)], { type: "application/json;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = "audit-export.json";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        toast("稽核紀錄已匯出", "ok");
      } catch (error) {
        toast(`稽核匯出失敗：${error.message}`, "err");
      } finally {
        if (button.isConnected) button.disabled = false;
      }
    });
    panel.querySelectorAll("[data-audit-open]").forEach((button) => button.addEventListener("click", async () => {
      const auditId = button.dataset.auditOpen;
      button.disabled = true;
      try {
        const item = await api(`/api/admin/audit-logs/${encodeURIComponent(auditId)}`);
        if (state.adminTab !== tab) return;
        panel.innerHTML = `<div class="arev-row"><div class="arev-info"><strong>${esc(item.action)}</strong><span class="arev-sub">${esc(item.actorLabel || item.username || "系統")} · ${esc(item.targetType || "")} ${esc(item.targetId || "")} · ${esc(item.createdAt || "")}</span><span class="arev-sub">類別：${esc(item.category || "system")} · Policy：${esc(item.policyVersion || "legacy-v1")}</span><pre class="audit-detail-json">${esc(JSON.stringify(item.details || {}, null, 2))}</pre></div><div class="arev-actions"><button class="btn btn-ghost" id="audit-detail-back">返回稽核紀錄</button></div></div>`;
        $("#audit-detail-back")?.addEventListener("click", () => renderAdminPanel());
      } catch (error) {
        toast(`載入稽核詳情失敗：${error.message}`, "err");
        button.disabled = false;
      }
    }));
    bindPager(panel, "audit");
  } else if (state.adminTab === "authors") {
    const f = state.adminFilter;
    const page = state.adminPage.authors || 1;
    const qs = new URLSearchParams({ status: f.authorStatus || "pending", page, page_size: 20 });
    let data = { items: [], total: 0, page: 1, total_pages: 0 };
    try { data = (await api("/api/admin/author-applications?" + qs)) || data; } catch (e) { panel.innerHTML = `<div class="ch-hintline">載入作者申請失敗：${esc(e.message)}</div>`; return; }
    if (state.adminTab !== tab) return;
    const rows = (data.items || []).map((item) => `<div class="arev-row" data-application="${item.id}"><div class="arev-info"><strong>${esc(item.pen_name)}</strong><span class="chip">${item.status === "pending" ? "待審核" : item.status === "approved" ? "已核准" : "已退件"}</span><span class="arev-sub">帳號：${esc(item.username)} · ${esc(item.created_at)}</span><p class="arev-sub">${esc(item.bio)}</p></div><div class="arev-actions">${item.status === "pending" ? `<button class="btn btn-accent" data-author-approve="${item.id}">通過</button><button class="btn btn-danger" data-author-reject="${item.id}">退件</button>` : `<button class="btn btn-ghost" data-author-reopen="${item.id}" title="重新開放申請（回到待審核）">重新待審</button>`}</div></div>`).join("");
    panel.innerHTML = `
      <div class="arev-row cat-add" style="border-style:none;padding:6px 2px">
        <div class="arev-info">
          <select id="author-status-filter"><option value="pending" ${(f.authorStatus||"pending")==="pending"?"selected":""}>待審核</option><option value="approved" ${f.authorStatus==="approved"?"selected":""}>已核准</option><option value="rejected" ${f.authorStatus==="rejected"?"selected":""}>已退件</option></select>
          <span class="arev-sub" style="margin-left:8px">歷史申請保留以供追蹤，不提供刪除。</span>
        </div>
      </div>
      ${rows || `<div class="ch-hintline" style="text-align:center;padding:30px">目前沒有符合條件的作者申請。</div>`}
      ${paginationBar(data)}`;
    $("#author-status-filter")?.addEventListener("change", (e) => { state.adminFilter = { ...state.adminFilter, authorStatus: e.target.value }; state.adminPage = { ...state.adminPage, authors: 1 }; renderAdminPanel(); });
    panel.querySelectorAll("[data-author-approve]").forEach((button) => button.addEventListener("click", async () => { try { await api(`/api/admin/author-applications/${button.dataset.authorApprove}/approve`, { method: "POST" }); toast("作者申請已通過", "ok"); renderAdminPanel(); } catch (e) { toast(`審核失敗：${e.message}`, "err"); } }));
    panel.querySelectorAll("[data-author-reject]").forEach((button) => button.addEventListener("click", async () => { const reason = prompt("退件原因："); if (reason === null) return; try { await api(`/api/admin/author-applications/${button.dataset.authorReject}/reject`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason }) }); toast("申請已退件", "ok"); renderAdminPanel(); } catch (e) { toast(`退件失敗：${e.message}`, "err"); } }));
    panel.querySelectorAll("[data-author-reopen]").forEach((button) => button.addEventListener("click", async () => { try { await api(`/api/admin/author-applications/${button.dataset.authorReopen}/reopen`, { method: "POST" }); toast("申請已重新待審", "ok"); renderAdminPanel(); } catch (e) { toast(`操作失敗：${e.message}`, "err"); } }));
    bindPager(panel, "authors");
  } else if (state.adminTab === "reports") {
    const f = state.adminFilter;
    const page = state.adminPage.reports || 1;
    const qs = new URLSearchParams({ status: f.reportStatus || "open", page, page_size: 20, sort: "created_at", order: "asc" });
    if (f.reportTargetType) qs.set("target_type", f.reportTargetType);
    if (f.reportQ) qs.set("q", f.reportQ);
    let data = { items: [], total: 0, page: 1, total_pages: 0 };
    try { data = (await api("/api/admin/reports?" + qs)) || data; } catch (e) {
      panel.innerHTML = `<div class="admin-status-panel admin-status-error" role="alert"><strong>檢舉載入失敗</strong><span>${esc(e.message)}</span><button class="btn" id="admin-reports-retry">重試</button></div>`;
      panel.querySelector("#admin-reports-retry")?.addEventListener("click", () => renderAdminPanel());
      return;
    }
    if (state.adminTab !== tab) return;
    const reports = data.items || [];
    const rows = reports.map((item) => `<div class="arev-row"><div class="arev-info"><strong>#${item.id} ${esc(item.targetType || item.target_type)} ${esc(item.targetId || item.target_id)}</strong><span class="chip">${item.status === "resolved" ? "已處理" : "待處理"}</span><span class="arev-sub">${esc(item.username)} · ${esc(item.createdAt || item.created_at)}</span><p class="arev-sub">${esc(item.reason)}</p>${item.resolution ? `<p class="arev-sub">處理結果：${esc(item.resolution)}</p>` : ""}</div><div class="arev-actions">${item.status === "open" ? `<button class="btn" data-report-resolve="${item.id}">標記已處理</button>` : ""}</div></div>`).join("");
    panel.innerHTML = `<div class="arev-row cat-add review-filters" style="border-style:none;padding:6px 2px"><div class="arev-info"><input id="report-q" placeholder="搜尋檢舉原因或回報者" value="${esc(f.reportQ)}"><input id="report-target-type" placeholder="目標類型" value="${esc(f.reportTargetType)}"><select id="report-status-filter"><option value="open" ${f.reportStatus === "open" ? "selected" : ""}>待處理</option><option value="resolved" ${f.reportStatus === "resolved" ? "selected" : ""}>已處理</option><option value="" ${!f.reportStatus ? "selected" : ""}>全部</option></select></div><div class="arev-actions"><button class="btn" id="report-filter">套用篩選</button><button class="btn btn-ghost" id="report-reset">重設</button></div></div>${rows || `<div class="admin-status-panel admin-status-empty"><strong>${f.reportStatus === "open" ? "目前沒有待處理檢舉" : "目前沒有符合條件的檢舉"}</strong><span>調整狀態或搜尋條件後重試。</span></div>`}${paginationBar(data)}`;
    panel.querySelector("#report-filter")?.addEventListener("click", () => { state.adminFilter = { ...state.adminFilter, reportQ: panel.querySelector("#report-q").value.trim(), reportTargetType: panel.querySelector("#report-target-type").value.trim(), reportStatus: panel.querySelector("#report-status-filter").value }; state.adminPage = { ...state.adminPage, reports: 1 }; renderAdminPanel(); });
    panel.querySelector("#report-reset")?.addEventListener("click", () => { state.adminFilter = { ...state.adminFilter, reportQ: "", reportTargetType: "", reportStatus: "open" }; state.adminPage = { ...state.adminPage, reports: 1 }; renderAdminPanel(); });
    panel.querySelectorAll("[data-report-resolve]").forEach((button) => button.addEventListener("click", async () => { try { await api(`/api/admin/reports/${button.dataset.reportResolve}/resolve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ resolution: "管理員已檢視" }) }); toast("檢舉已處理", "ok"); renderAdminPanel(); } catch (e) { toast(`處理失敗：${e.message}`, "err"); } }));
    bindPager(panel, "reports");
  } else if (state.adminTab === "users") {
    const f = state.adminFilter;
    const page = state.adminPage.users || 1;
    const qs = new URLSearchParams({ page, page_size: 20 });
    if (f.userQ) qs.set("q", f.userQ);
    if (f.userRole) qs.set("role", f.userRole);
    if (f.userStatus) qs.set("status", f.userStatus);
    if (f.userAuthorStatus) qs.set("author_status", f.userAuthorStatus);
    let data = { items: [], total: 0, page: 1, total_pages: 0 };
    try { data = (await api("/api/admin/users?" + qs)) || data; } catch (e) { panel.innerHTML = `<div class="ch-hintline">載入使用者失敗：${esc(e.message)}</div>`; return; }
    if (state.adminTab !== tab) return;
    const rows = (data.items || []).map((u) => {
      const protectedAccount = u.role === "super_admin";
      const roleLabel = u.role === "super_admin" ? "Super Admin" : u.role === "admin" ? "管理員" : u.role === "reviewer" ? "Reviewer" : u.role === "author" ? "作者" : "讀者";
      return `
      <div class="arev-row" data-uid="${u.id}">
        <div class="arev-info">
          <strong>${esc(u.username)}</strong>
          <span class="chip">${roleLabel}</span>
          ${u.account_status === "disabled" ? `<span class="chip">已停用</span>` : u.account_status === "deleted" ? `<span class="chip">已刪除</span>` : ""}
          <span class="arev-sub">${esc(u.displayName || u.username)} · Email：${esc(u.email || "—")} · 註冊：${esc(u.created_at)} · 最後登入：${esc(u.last_login_at || "—")} · 書籍數：${u.ownedBookCount ?? u.books ?? "?"} · 作者申請：${esc(u.authorStatus || "none")}</span>
        </div>
        <div class="arev-actions">
          ${protectedAccount ? `<span class="arev-sub">受保護角色：請使用 privileged surface</span>` : u.account_status === "deleted" ? "" : `<button class="btn" data-urole="${u.id}" data-role="author">設為作者</button>
          <button class="btn" data-urole="${u.id}" data-role="reader">設為讀者</button>
          <button class="btn" data-urole="${u.id}" data-role="reviewer">設為 Reviewer</button>
          <button class="btn" data-urole="${u.id}" data-role="admin">設為管理員</button>
          <button class="btn btn-ghost" data-ureset="${u.id}">重設密碼</button>`}
          ${protectedAccount ? "" : u.account_status === "deleted" ? "" : u.account_status === "disabled"
            ? `<button class="btn" data-ustatus="${u.id}" data-status="active">啟用</button>`
            : `<button class="btn btn-ghost" data-ustatus="${u.id}" data-status="disabled">停用</button>`}
          ${protectedAccount || u.account_status === "deleted" ? "" : `<button class="btn btn-ghost btn-danger" data-udel="${u.id}">刪除</button>`}
        </div>
      </div>`;
    }).join("");
    panel.innerHTML = `
      <div class="arev-row cat-add" style="border-style:none;padding:6px 2px">
        <div class="arev-info">
          <input id="user-q" placeholder="搜尋帳號或 Email" value="${esc(f.userQ)}">
          <select id="user-role-filter"><option value="">全部角色</option><option value="reader" ${f.userRole==="reader"?"selected":""}>讀者</option><option value="author" ${f.userRole==="author"?"selected":""}>作者</option><option value="reviewer" ${f.userRole==="reviewer"?"selected":""}>Reviewer</option><option value="admin" ${f.userRole==="admin"?"selected":""}>管理員</option><option value="super_admin" ${f.userRole==="super_admin"?"selected":""}>Super Admin</option></select>
          <select id="user-status-filter"><option value="">全部狀態</option><option value="active" ${f.userStatus==="active"?"selected":""}>啟用</option><option value="disabled" ${f.userStatus==="disabled"?"selected":""}>已停用</option><option value="deleted" ${f.userStatus==="deleted"?"selected":""}>已刪除</option></select>
          <select id="user-author-status-filter"><option value="">作者申請：全部</option><option value="none" ${f.userAuthorStatus==="none"?"selected":""}>尚未申請</option><option value="pending" ${f.userAuthorStatus==="pending"?"selected":""}>申請審核中</option><option value="approved" ${f.userAuthorStatus==="approved"?"selected":""}>已核准</option><option value="rejected" ${f.userAuthorStatus==="rejected"?"selected":""}>已退件</option></select>
        </div>
        <div class="arev-actions"><button class="btn" id="user-filter">套用篩選</button><button class="btn btn-ghost" id="user-reset">重設</button></div>
      </div>
      ${rows || `<div class="ch-hintline" style="text-align:center;padding:30px 0">尚無符合條件的使用者。</div>`}
      ${paginationBar(data)}`;
    $("#user-filter")?.addEventListener("click", () => { state.adminFilter = { ...state.adminFilter, userQ: $("#user-q").value.trim(), userRole: $("#user-role-filter").value, userStatus: $("#user-status-filter").value, userAuthorStatus: $("#user-author-status-filter").value }; state.adminPage = { ...state.adminPage, users: 1 }; renderAdminPanel(); });
    $("#user-reset")?.addEventListener("click", () => { state.adminFilter = { ...state.adminFilter, userQ: "", userRole: "", userStatus: "", userAuthorStatus: "" }; state.adminPage = { ...state.adminPage, users: 1 }; renderAdminPanel(); });
    panel.querySelectorAll("[data-urole]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!confirm(`確定將此帳號角色變更為「${btn.dataset.role}」？系統會重新檢查目前角色與受保護帳號規則。`)) return;
        try {
          await api(`/api/admin/users/${btn.dataset.urole}/role`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role: btn.dataset.role }) });
          toast("已更新角色", "ok");
          renderAdminPanel();
        } catch (e) { toast(`更新角色失敗：${e.message}`, "err"); }
      }));
    panel.querySelectorAll("[data-ustatus]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        const on = btn.dataset.status === "disabled";
        if (on && !confirm("確定停用此帳號？停用後該使用者將無法登入。")) return;
        try {
          await api(`/api/admin/users/${btn.dataset.ustatus}/status`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: btn.dataset.status }) });
          toast(on ? "帳號已停用" : "帳號已啟用", "ok");
          renderAdminPanel();
        } catch (e) { toast(`更新狀態失敗：${e.message}`, "err"); }
      }));
    panel.querySelectorAll("[data-ureset]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        const newPw = prompt("輸入新密碼（至少 6 碼）：");
        if (!newPw) return;
        try {
          await api(`/api/admin/users/${btn.dataset.ureset}/password`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: newPw }) });
          toast("已重設", "ok");
        } catch (e) { toast(`重設失敗：${e.message}`, "err"); }
      }));
    panel.querySelectorAll("[data-udel]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!confirm("確定刪除這個使用者？")) return;
        try {
          await api(`/api/admin/users/${btn.dataset.udel}`, { method: "DELETE" });
          toast("已刪除", "ok");
          renderAdminPanel();
        } catch (e) { toast(`刪除失敗：${e.message}`, "err"); }
      }));
    bindPager(panel, "users");
  }
}

function bindAdminTabs() {
  document.querySelectorAll(".admin-tabs .chip").forEach((c) =>
    c.addEventListener("click", () => setAdminTab(c.dataset.atab)));
}

function bindOwnershipTransfer() {
  $("#ot-cancel")?.addEventListener("click", () => closeAppModal("#ownership-transfer-modal"));
  $("#ot-submit")?.addEventListener("click", submitOwnershipTransfer);
}

async function uploadFile(file) {
  const fd = new FormData();
  fd.append("file", file, file.name);
  fd.append("category", $("#upload-category").value);
  if ($("#upload-genre").value) fd.append("genreId", $("#upload-genre").value);
  fd.append("vocabLevel", $("#upload-vocab-level").value);
  const cats = [...document.querySelectorAll(".up-cat-check:checked")].map((cb) => cb.value);
  if (cats.length) fd.append("categories", cats.join(","));
  fd.append("splitMode", $("#upload-split-mode").value);
  fd.append("splitChars", $("#upload-split-chars").value || "3000");
  fd.append("hasPrologue", document.querySelector('input[name="upload-prologue"]:checked')?.value !== "false");
  if ($("#upload-author-profile")?.value) fd.append("authorProfileId", $("#upload-author-profile").value);
  toast("上傳中，正在拆解章節…");
  const b = await api("/api/books", { method: "POST", body: fd });
  state.books.push(b);
  toast(`完成，共 ${b.chapters.length} 章`, "ok");
  openBook(b.id);
}

async function loadAuthorProfileChoices() {
  const selects = [$("#upload-author-profile"), $("#nb-author-profile")].filter(Boolean);
  if (!selects.length || !state.authed) return;
  try {
    const data = await api("/api/auth/profile");
    const options = (data.authorProfiles || []).filter((a) => a.status === "active")
      .map((a) => `<option value="${esc(a.profileId)}">${esc(a.displayName)} · ${esc(a.slug)}</option>`).join("");
    selects.forEach((select) => {
      const first = select.id === "nb-author-profile" ? "公開作者身份（自動選擇）" : "自動選擇第一個作者身份";
      select.innerHTML = `<option value="">${first}</option>${options}`;
    });
  } catch (_) {
    selects.forEach((select) => { select.innerHTML = '<option value="">自動選擇</option>'; });
  }
}

async function loadLiteraryCategories() {
  try {
    const result = await api("/api/categories");
    const options = (result.categories || []).map((category) => `<option value="${category.id}">${esc(category.name)}</option>`).join("");
    [$("#upload-genre"), $("#be-category"), $("#nb-genre")].forEach((select) => {
      if (select) select.insertAdjacentHTML("beforeend", options);
    });
  } catch (_) {}
}
function downloadSampleTxt() {
  const sample = `第 1 章 新的開始

清晨的陽光灑進房間，他伸了個懶腰。

「早安，今天天氣真好。」她笑著說。

「是啊，那我們出發吧。」他背起背包。

第 2 章 旅途

路上風景很美，兩人沿途聊著天，不知不覺就走遠了。

「你看，前面那棵樹上有一隻鳥。」她指著前方。

他停下腳步，抬頭望去。`;
  const blob = new Blob([sample], { type: "text/plain;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "章節範例.txt";
  a.click();
  URL.revokeObjectURL(a.href);
}
function bindUpload() {
  const zone = $("#upload-zone"), input = $("#file-input");
  zone.addEventListener("click", (e) => {
    if (e.target.closest(".up-cat-row")) return; // 分類選擇區不觸發選檔
    input.click();
  });
  zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => { if (input.files[0]) uploadFile(input.files[0]); input.value = ""; });
  ["dragover", "dragenter"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("drag"); }));
  zone.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) uploadFile(f); });

  // 上傳分類互動
  const cat = $("#upload-category"), all = $("#up-cat-all"), list = $("#up-cat-list"), wrap = $("#up-vocab-level-wrap");
  const checkedCats = () => [...list.querySelectorAll(".up-cat-check:checked")].map((cb) => cb.value);
  function update() {
    // 視「主分類或標籤含 vocab/bilingual」顯示單字難度：不只依主分類判斷
    const cats = cat.value === "all" ? checkedCats() : [cat.value, ...checkedCats()];
    const vocabOn = cats.includes("vocab") || cats.includes("bilingual");
    wrap.style.display = vocabOn ? "" : "none";
    if (all.checked) {
      list.style.display = "none";
      list.querySelectorAll(".up-cat-check").forEach((cb) => { cb.checked = true; });
    } else {
      list.style.display = "";
    }
  }
  // 主分類切換時，對應標籤也一併勾選，避免「主分類與分類標籤不同步」
  cat.addEventListener("change", () => {
    const cb = list.querySelector(`.up-cat-check[value="${cat.value}"]`);
    if (cb && !all.checked) cb.checked = true;
    update();
  });
  all.addEventListener("change", update);
  list.addEventListener("change", update);
  update();

  // 範例檔下載（不觸發選檔）
  const sampleBtn = $("#btn-sample-txt");
  if (sampleBtn) sampleBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    downloadSampleTxt();
  });

  // 分段方式：僅「依字數」時顯示每段字數欄位
  const splitMode = $("#upload-split-mode"), splitCharsWrap = $("#up-split-chars-wrap");
  const updateSplit = () => { splitCharsWrap.hidden = splitMode.value !== "chars"; };
  splitMode.addEventListener("change", updateSplit);
  updateSplit();
}

/* ---------------- 章節 ---------------- */
async function openBook(id) {
  const destination = "#/detail/" + encodeURIComponent(id);
  if (location.hash.split("?")[0] !== destination) { location.hash = destination; return; }
  const intent = ++bookLoadIntent;
  const routeHash = location.hash;
  const accountId = state.me?.id;
  const current = () => intent === bookLoadIntent && location.hash === routeHash && state.me?.id === accountId;
  closeNavigationModals();
  stopPoll();
  try {
    const book = await api("/api/books/" + id);
    if (!current()) return;
    state.book = book;
  } catch (e) {
    if (!current()) return;
    toast(`載入作品失敗：${e.message}`, "err");
    location.hash = "#/bookshelf";
    return;
  }
  if (!canEditBook(state.book)) {
    // 讀者／訪客：不顯示作者章節管理 UI，改走公開作品頁
    state.book = null;
    location.hash = "#/book/" + encodeURIComponent(id);
    return;
  }
  state.voicePrefs = state.book.voicePrefs || {};
  state.expandedSeq = null;
  state.analysisCache = {};
  showView("chapters");
  renderBreadcrumb();
  renderChapters();
  startPoll();
  // Phase 15a：手動建立作品後直接引導寫第一章
  if (state.pendingFirstChapter && canEditBook() && !(state.book.chapters || []).length) {
    state.pendingFirstChapter = false;
    openAddChapterModal();
  }
}
function goChapters() {
  if (!state.book) return;
  state.chapterAudio.pause();
  showView("chapters");
  renderBreadcrumb();
  renderChapters();
}
function goHome() {
  state.chapterAudio.pause();
  stopPoll();
  state.book = null;
  showView("bookshelf");
  renderBreadcrumb();
  loadBooks();
}

let bookLoadIntent = 0;
function startPoll() {
  if (state.pollTimer) return;
  state.pollTimer = setInterval(async () => {
    if (!state.book) return;
    const bookId = state.book.id;
    const routeHash = location.hash;
    const accountId = state.me?.id;
    try {
      const book = await api("/api/books/" + bookId);
      if (state.book?.id !== bookId || location.hash !== routeHash || state.me?.id !== accountId) return;
      state.book = book;
      renderPoll();
      const wf = state.book.workflow;
      const busy = wf && Object.values(wf.chapters || {}).some((ch) => ch.state === "analysis_running" || ch.state === "audio_generating");
      // 批次執行中（batchActive）不因單次非 busy 快照停止輪詢，
      // 確保 job 完成後 workflow/chapter 狀態自動刷新
      if (!busy && !state.batchActive) stopPoll();
    } catch (e) { stopPoll(); }
  }, 2000);
}
function stopPoll() { clearInterval(state.pollTimer); state.pollTimer = null; }

function chapterSnap(c) {
  const wc = wfChapter(c.seq);
  const ex = state.expandedSeq === c.seq ? 1 : 0;
  return [c.seq, wc.state || "", wc.nextAction || "", JSON.stringify(wc.analysisProgress || {}), c.title, c.chars, ex].join("|");
}
function renderPoll() {
  // 快照比對：無任何變化時不重繪，避免每 2 秒整頁重建造成的閃爍與互動中斷
  const snap = (state.book.chapters || []).map(chapterSnap).join("\n");
  if (snap === state._pollSnap) return;
  renderChapters();
}

function waitChapterWorkflow(seq, doneStates) {
  doneStates = Array.isArray(doneStates) ? doneStates : [doneStates];
  return new Promise((resolve) => {
    const iv = setInterval(async () => {
      try {
        const b = await api("/api/books/" + state.book.id);
        state.book = b;
        renderPoll();
        const wc = wfChapter(seq);
        if (!wc || !wc.state || doneStates.includes(wc.state) || wc.state === "provider_unavailable") {
          clearInterval(iv);
          resolve(wc);
        }
      } catch (e) { clearInterval(iv); resolve(null); }
    }, 2000);
  });
}

function wfChapter(seq) {
  const wf = state.book && state.book.workflow;
  const ch = wf && wf.chapters && wf.chapters[seq];
  return ch || { state: null, nextAction: null, providerUnavailable: false, analysisProgress: null };
}

const ANALYSIS_STAGE_LABEL = {
  queued: "排隊中",
  analyzing: "分析中",
  normalizing: "整理分析結果",
  validating: "驗證分析結果",
  saving: "保存分析結果",
  deriving_roster: "整理角色名冊",
  ready: "分析完成",
  failed: "分析失敗",
};

function analysisProgressLabel(wc) {
  const p = wc && wc.analysisProgress;
  if (!p) return "分析中";
  if (p.legacyProgress) return "分析狀態待恢復";
  if (p.retryCount > 0 && p.currentStage === "queued") return "正在自動重試";
  const stage = ANALYSIS_STAGE_LABEL[p.currentStage] || "分析中";
  if (p.currentStage === "analyzing") {
    const chunks = `${p.completedChunks || 0}/${p.totalChunks || 0}`;
    const running = p.runningChunks ? ` · 處理中 ${p.runningChunks} 段` : "";
    return `${stage} ${p.progressPercent || 0}% · 已完成 ${chunks} 段${running}`;
  }
  return `${stage} ${p.progressPercent || 0}%`;
}

const BADGE_LABEL = {
  pending: "未分析", analyzing: "分析中", analyzed: "已分析",
  generating: "生成中", ready: "可播放", error: "失敗",
};
const BADGE_CLASS = {
  pending: "pending", analyzing: "analyzing", analyzed: "analyzed",
  generating: "generating", ready: "ready", error: "error",
};
const WF_BADGE = {
  needs_analysis: ["pending", "待分析"],
  analysis_running: ["analyzing", "分析中"],
  analysis_failed: ["error", "分析失敗"],
  needs_voice_configuration: ["pending", "待設聲線"],
  ready_to_generate: ["analyzed", "可生成"],
  audio_generating: ["generating", "生成中"],
  audio_ready: ["ready", "可播放"],
  audio_stale: ["analyzed", "需重新生成"],
  audio_failed: ["error", "生成失敗"],
  provider_unavailable: ["error", "服務暫停"],
  needs_content_review: ["pending", "待檢視"],
  ready_to_submit: ["ready", "可送審"],
};

function chapterBadge(c) {
  const wc = wfChapter(c.seq);
  const m = wc.state ? (WF_BADGE[wc.state] || ["pending", "處理中"]) : ["pending", "處理中"];
  return { cls: m[0], label: m[1] };
}

function updateHeaderStats() {
  const b = state.book;
  const chapters = b.chapters || [];
  const wf = b.workflow || {};
  const total = chapters.length;
  const analyzed = wf.analyzed || 0;
  const ready = wf.ready || 0;
  const busy = Object.values(wf.chapters || {}).some((ch) => ch.state === "analysis_running" || ch.state === "audio_generating");

  $("#ch-title").textContent = b.title;
  $("#ch-stats").textContent = `${total} 章 · 已分析 ${analyzed} · 可播放 ${ready}`;
  $("#ch-synopsis").textContent = b.synopsis || "";
  const follow = $("#btn-follow");
  follow.hidden = !state.authed || !!canEditBook(b);
  follow.textContent = b.follow ? "已追蹤" : "追書";
  const eff = effCategory(b);
  $("#ch-cat").textContent = `語言型別：${CAT_LABEL[eff] || eff}`;
  const lv = b.vocabLevel;
  const lvEl = $("#ch-cat-level");
  lvEl.hidden = !(lv && lv !== "AUTO");
  if (lv && lv !== "AUTO") lvEl.textContent = `難度 ${lv}`;

  const manager = canEditBook(b);
  const generationOperator = isAdmin();
  $(".ch-progress-card").classList.toggle("guest-hide", !manager);
  $("#toggle-auto-tts").closest(".toggle-switch").classList.toggle("guest-hide", !generationOperator);

  // Phase 15b：provider outage 以產品狀態呈現，不暴露底層錯誤
  const banner = $("#ch-provider-banner");
  if (banner) {
    if (wf.providerUnavailable) {
      banner.hidden = false;
      banner.innerHTML = `<strong>朗讀服務暫時無法使用</strong><span>平台語音服務尚未就緒，請稍後再試或聯絡管理員。這不是你的作品設定問題。</span>`;
    } else {
      banner.hidden = true;
    }
  }

  const ap = total ? Math.round((analyzed / total) * 100) : 0;
  const rp = total ? Math.round((ready / total) * 100) : 0;
  $("#ring-fg").style.strokeDasharray = `${(ap / 100) * 125.6} 125.6`;
  $("#ring-pct").textContent = `${ap}%`;
  $("#bar-analyze").style.width = ap + "%";
  $("#bar-audio").style.width = rp + "%";
  $("#num-analyze").textContent = `${analyzed}/${total}`;
  $("#num-audio").textContent = `${ready}/${total}`;

  const corrOn = !!(b.settings && b.settings.ttsCorrect);
  if ($("#bulk-ttscorrect").checked !== corrOn) $("#bulk-ttscorrect").checked = corrOn;

  $("#topbar-status").innerHTML = busy
    ? `<span class="status-pill working"><span class="dot"></span>處理中</span>`
    : `<span class="status-pill"><span class="dot"></span>${ready}/${total} 章可播放</span>`;

  const mode = wf.mode || "single";
  renderStepper(mode, analyzed, ready);
  $("#ch-hintline").innerHTML = generationOperator
    ? (mode === "single"
      ? `流程：<b>① 選擇朗讀聲線</b> → <b>② 生成音訊</b> → ③ 播放。`
      : `流程：<b>① 分析角色</b> → <b>② 設定各角色聲線</b> → <b>③ 生成音訊</b> → ④ 播放。`)
    : `目前由 Reviewer 依核准的內容申請執行分析與音訊生成；你可以繼續編輯作品，並在「申請紀錄」查看進度。`;

  // 批次按鈕：單一模式不需分析（隱藏）；count 與可執行狀態以共用 predicate 呈現
  const anBtn = $("#btn-analyze-all");
  const cancelBatchBtn = $("#btn-cancel-analyze-all");
  if (anBtn) {
    anBtn.hidden = mode !== "multi" || !generationOperator;
    if (mode === "multi") {
      const n = (chapters || []).filter(chapterNeedsAnalysis).length;
      anBtn.textContent = state.batchActive ? "批次分析中…" : (n ? `分析剩餘 ${n} 章` : "分析剩餘");
      anBtn.disabled = state.batchActive || !n;
    }
  }
  if (cancelBatchBtn) {
    const visible = generationOperator && mode === "multi" && state.batchActive && !!state.batchJobId;
    cancelBatchBtn.hidden = !visible;
    cancelBatchBtn.disabled = state.batchCancelInFlight;
    cancelBatchBtn.textContent = state.batchCancelInFlight ? "取消中…" : "取消批次分析";
  }
  const ttBtn = $("#btn-tts-all");
  if (ttBtn) {
    ttBtn.hidden = !generationOperator;
    const n = (chapters || []).filter(chapterNeedsGeneration).length;
    ttBtn.textContent = n ? `生成剩餘 ${n} 章` : "生成剩餘音訊";
    ttBtn.disabled = !n;
  }
  const correction = $("#bulk-ttscorrect")?.closest(".corr-row");
  if (correction) correction.hidden = !generationOperator;
  const voicesButton = $("#btn-book-voices");
  if (voicesButton) voicesButton.hidden = !generationOperator;
  renderAudioSetup();
}

function renderChapters() {
  updateHeaderStats();
  updateChapterListDiff();
  state._pollSnap = (state.book.chapters || []).map(chapterSnap).join("\n");
}

function renderStepper(mode, analyzed, ready) {
  const labels = mode === "single" ? ["選擇聲線", "生成音訊", "邊聽邊學"] : ["分析語者", "綁定聲線", "生成音訊", "邊聽邊學"];
  const done = mode === "single"
    ? [ready > 0, ready > 0, ready > 0]
    : [analyzed > 0, analyzed > 0, ready > 0, ready > 0];
  let activeSet = false;
  document.querySelectorAll("#stepper .step").forEach((step, i) => {
    if (i >= labels.length) { step.hidden = true; return; }
    step.hidden = false;
    step.querySelector(".step-label").textContent = labels[i];
    step.classList.toggle("done", done[i] === true);
    if (!done[i] && !activeSet) { step.classList.add("active"); activeSet = true; }
    else step.classList.remove("active");
  });
  document.querySelectorAll("#stepper .step-line").forEach((line, i) => {
    line.hidden = mode === "single" && i >= 2;
  });
}

function passesFilter(c) {
  if (state.filter === "all") return true;
  const s = wfChapter(c.seq).state;
  if (state.filter === "error") return s === "analysis_failed" || s === "audio_failed" || s === "provider_unavailable";
  const map = {
    pending: ["needs_analysis"],
    analyzed: ["analysis_running", "needs_voice_configuration", "ready_to_generate", "audio_generating", "audio_ready", "audio_stale"],
    ready: ["audio_generating", "audio_ready", "ready_to_submit"],
  };
  return (map[state.filter] || []).includes(s);
}

// 共用 canonical eligibility predicate：批次按鈕／filter／章節動作一律以 workflow state 為準。
function chapterNeedsAnalysis(c) {
  const stateName = wfChapter(c.seq).state;
  return stateName === "needs_analysis" || stateName === "analysis_failed";
}
function chapterNeedsGeneration(c) {
  const s = wfChapter(c.seq).state;
  return s === "ready_to_generate" || s === "audio_failed";
}

function rowHtml(c) {
  const reorder = canEditBook() ? `
    <span class="ch-reorder">
      <button class="ch-reorder-btn" data-up="${c.seq}" title="上移">▲</button>
      <button class="ch-reorder-btn" data-down="${c.seq}" title="下移">▼</button>
      <button class="ch-reorder-btn danger" data-delch="${c.seq}" title="刪除本章">×</button>
    </span>` : "";
  const badge = chapterBadge(c);
  const wc = wfChapter(c.seq);
  const errLine = (wc.state === "audio_failed" || wc.state === "analysis_failed") && wc.error
    ? `<div class="ch-error">${esc(wc.error)}</div>` : "";

  return `
    <div class="chapter-item ${state.expandedSeq === c.seq ? "expanded" : ""}" data-seq="${c.seq}" data-snap="${esc(chapterSnap(c))}">
      <div class="ch-row" data-toggle="${c.seq}">
        <span class="chev"><svg viewBox="0 0 24 24" width="14" height="14" fill="none"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
        <span class="ch-no">${chapterDisplayNumber(state.book, c.seq)}</span>
        <span class="ch-title" title="${esc(c.title)}">${esc(c.title)}</span>
        <span class="ch-chars">${fmtChars(c.chars)}字</span>
        <span class="badge ${badge.cls}">${badge.label}</span>
        ${reorder}
        ${canEditBook() ? `<button class="btn btn-ghost" data-edit-ch="${c.seq}">編輯</button>` : ""}
        <span class="ch-actions">${rowActions(c)}</span>
      </div>
      ${errLine}
      <div class="ch-detail" data-detail="${c.seq}"></div>
    </div>`;
}

function updateChapterListDiff() {
  const list = $("#chapter-list");
  const chapters = (state.book.chapters || []).filter(passesFilter);
  // Phase 15a：空書時提供明確「新增第一章」入口
  if (!chapters.length) {
    list.innerHTML = canEditBook()
      ? `<div class="ch-hintline" style="text-align:center;padding:30px 0"><p style="margin:0 0 12px">這本作品還沒有章節。</p><button class="btn btn-accent" id="btn-add-first-chapter" type="button">＋ 新增第一章</button></div>`
      : `<div class="ch-hintline" style="text-align:center;padding:30px 0">這本作品還沒有章節。</div>`;
    const firstBtn = $("#btn-add-first-chapter");
    if (firstBtn) firstBtn.addEventListener("click", openAddChapterModal);
    return;
  }
  const prev = new Map();
  list.querySelectorAll(".chapter-item").forEach((el) => prev.set(+el.dataset.seq, el));
  const frag = document.createDocumentFragment();
  const changed = new Set();
  for (const c of chapters) {
    const snap = chapterSnap(c);
    const old = prev.get(c.seq);
    if (old && old.dataset.snap === snap) {
      frag.appendChild(old);
      prev.delete(c.seq);
      continue;
    }
    const wrap = document.createElement("div");
    wrap.innerHTML = rowHtml(c);
    const el = wrap.firstElementChild;
    bindRowListeners(el);
    frag.appendChild(el);
    if (old) prev.delete(c.seq);
    changed.add(c.seq);
  }
  list.innerHTML = "";
  list.appendChild(frag);
  if (state.expandedSeq != null && changed.has(state.expandedSeq)) renderDetailIfNeeded(state.expandedSeq);
}

function bindRowListeners(row) {
  row.querySelectorAll("[data-toggle]").forEach((r) =>
    r.addEventListener("click", () => toggleExpand(+r.dataset.toggle)));
  row.querySelectorAll("[data-v4act]").forEach((btn) =>
    btn.addEventListener("click", (e) => { e.stopPropagation(); runWorkflowAction(btn.dataset.v4act, +btn.dataset.seq, btn.dataset.force === "1"); }));
  row.querySelectorAll("[data-play]").forEach((btn) =>
    btn.addEventListener("click", (e) => { e.stopPropagation(); openPlayer(+btn.dataset.play); }));
  row.querySelectorAll("[data-preview-text]").forEach((btn) =>
    btn.addEventListener("click", (e) => { e.stopPropagation(); openPlayer(+btn.dataset.previewText); }));
  row.querySelectorAll("[data-up]").forEach((btn) =>
    btn.addEventListener("click", (e) => { e.stopPropagation(); moveChapter(+btn.dataset.up, -1); }));
  row.querySelectorAll("[data-down]").forEach((btn) =>
    btn.addEventListener("click", (e) => { e.stopPropagation(); moveChapter(+btn.dataset.down, 1); }));
  row.querySelectorAll("[data-delch]").forEach((btn) =>
    btn.addEventListener("click", (e) => { e.stopPropagation(); deleteChapter(+btn.dataset.delch); }));
  row.querySelectorAll("[data-edit-ch]").forEach((btn) =>
    btn.addEventListener("click", (e) => { e.stopPropagation(); openChapterEdit(+btn.dataset.editCh); }));
}

function runWorkflowAction(action, seq, force = false) {
  if (action === "analyze") return analyzeChapter(seq, force);
  if (action === "cancel-analysis") return cancelAnalysis(seq);
  if (action === "generate") return generateChapter(seq, force);
  if (action === "configure-voices") {
    state.expandedSeq = seq;
    renderChapters();
    renderDetailIfNeeded(seq);
    return;
  }
  if (action === "set-voice") return openAudioSetup();
}

function rowActions(c) {
  const wc = wfChapter(c.seq);
  const manager = isAdmin();
  const play = `<button class="btn btn-accent" data-play="${c.seq}">播放</button>`;
  const preview = `<button class="btn btn-ghost" data-preview-text="${c.seq}" title="無音檔時可直接閱讀文字">預覽文字</button>`;
  if (wc.state === "provider_unavailable") {
    return `<span class="ch-hint" title="朗讀服務暫時無法使用">服務暫停</span>`;
  }
  if (!manager) {
    if (wc.state === "audio_ready") return play;
    return "";
  }
  if (wc.state === "audio_ready") return play + `<button class="btn btn-ghost" data-v4act="generate" data-force="1" data-seq="${c.seq}" title="重新生成音訊">重生成</button>`;
  if (wc.state === "audio_failed") return `<button class="btn btn-accent" data-v4act="generate" data-force="1" data-seq="${c.seq}">重試生成</button>` + preview;
  if (wc.state === "audio_generating") return `<span class="spin"></span>`;
  if (wc.state === "analysis_running") {
    return `<span class="ch-analysis-progress">${esc(analysisProgressLabel(wc))}</span>`
      + `<button class="btn btn-ghost" data-v4act="cancel-analysis" data-seq="${c.seq}">取消分析</button>`;
  }
  if (wc.nextAction === "analyze") return `<button class="btn btn-accent" data-v4act="analyze" data-force="${wc.state === "analysis_failed" ? "1" : "0"}" data-seq="${c.seq}">${wc.state === "analysis_failed" ? "重新分析" : "開始分析角色"}</button>`;
  if (wc.nextAction === "configure_voices") return `<button class="btn btn-accent" data-v4act="configure-voices" data-seq="${c.seq}">設定各角色聲線</button>`;
  if (wc.nextAction === "set_voice") return `<button class="btn btn-accent" data-v4act="set-voice" data-seq="${c.seq}">選擇朗讀聲線</button>`;
  if (wc.nextAction === "generate") return `<button class="btn btn-accent" data-v4act="generate" data-seq="${c.seq}">生成音訊</button>` + preview;
  return preview;
}

function toggleExpand(seq) {
  state.expandedSeq = state.expandedSeq === seq ? null : seq;
  renderChapters();
}

/* 章節結構變動（新增/刪除/排序）後重新載入；seq 可能重整，捨棄展開狀態與快取 */
async function refreshBook() {
  const bookId = state.book?.id;
  const routeHash = location.hash;
  const accountId = state.me?.id;
  if (!bookId) return;
  try {
    state.expandedSeq = null;
    state.analysisCache = {};
    state._pollSnap = "";
    const book = await api("/api/books/" + bookId);
    if (state.book?.id !== bookId || location.hash !== routeHash || state.me?.id !== accountId) return;
    state.book = book;
    renderChapters();
  } catch (e) {
    toast(`讀取失敗：${e.message}`, "err");
  }
}

async function moveChapter(seq, dir) {
  const chapters = state.book.chapters;
  const idx = chapters.findIndex((c) => c.seq === seq);
  const other = idx + dir;
  if (idx < 0 || other < 0 || other >= chapters.length) return;
  const order = chapters.map((c) => c.seq);
  [order[idx], order[other]] = [order[other], order[idx]];
  try {
    await api(`/api/books/${state.book.id}/chapters/reorder`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order }),
    });
    toast("已調整章節順序，全書需重新分析", "ok");
    await refreshBook();
  } catch (e) { toast(`調整順序失敗：${e.message}`, "err"); }
}

async function deleteChapter(seq) {
  const ch = state.book.chapters.find((c) => c.seq === seq);
  const name = ch ? ch.title : `第 ${seq} 章`;
  if (!confirm(`確定刪除章節「${name}」？刪除後全書章節順序重整，需重新分析。`)) return;
  try {
    await api(`/api/books/${state.book.id}/chapters/${seq}`, { method: "DELETE" });
    toast("已刪除章節", "ok");
    await refreshBook();
  } catch (e) { toast(`刪除失敗：${e.message}`, "err"); }
}

// 每次開啟皆綁定作品、帳號與原文快照；非同步回應不能跨編輯工作階段。
let chapterEditor = null;
function readChapterDraft(editor) {
  try { return sessionStorage.getItem(editor.draftKey); } catch (_) { return null; }
}
function clearSavedChapterDraft(editor, snapshot) {
  // 儲存期間離開再開啟，可能已產生新草稿；舊請求不得清除它。
  try { if (sessionStorage.getItem(editor.draftKey) === snapshot) sessionStorage.removeItem(editor.draftKey); } catch (_) { /* 不影響伺服器儲存。 */ }
}
function chapterEditorCurrent(editor) {
  return chapterEditor === editor && !$("#chapter-modal").hidden &&
    state.book?.id === editor.bookId && state.me?.id === editor.accountId && location.hash === editor.route;
}
function chapterEditorDirty() {
  return chapterEditor?.loaded && ($("#new-ch-title").value !== chapterEditor.title || $("#new-ch-text").value !== chapterEditor.text);
}
function updateChapterEditor() {
  const editor = chapterEditor;
  if (!editor) return;
  const dirty = chapterEditorDirty();
  $("#chapter-modal").dataset.unsaved = String(Boolean(dirty || editor.busy));
  $("#new-ch-title").disabled = !editor.loaded || editor.busy;
  $("#new-ch-text").disabled = !editor.loaded || editor.busy;
  $("#new-ch-submit").disabled = !editor.loaded || editor.busy || !$("#new-ch-text").value.trim();
  $("#new-ch-cancel").disabled = editor.busy;
  $("#chapter-draft-discard").disabled = editor.busy;
  $("#new-ch-submit").textContent = editor.busy ? "儲存中…" : editor.seq === null ? "加入" : "儲存";
  $("#chapter-editor-count").textContent = `${Array.from($("#new-ch-text").value).length.toLocaleString()} 字`;
}
function cacheChapterDraft() {
  const editor = chapterEditor;
  if (!editor?.loaded || editor.busy) return;
  try {
    if (chapterEditorDirty()) sessionStorage.setItem(editor.draftKey, JSON.stringify({
      title: $("#new-ch-title").value, text: $("#new-ch-text").value, baseline: editor.baseline,
    }));
    else sessionStorage.removeItem(editor.draftKey);
    $("#chapter-editor-status").textContent = chapterEditorDirty() ? "未儲存 · 已暫存於此分頁" : "尚無未儲存修改";
  } catch (_) {
    $("#chapter-editor-status").textContent = "無法暫存，請儲存後再離開";
  }
  updateChapterEditor();
}
function applyChapterOriginal(editor, original) {
  editor.title = original.title || "";
  editor.text = original.text || "";
  editor.baseline = {};
  if (original.chapterKey != null) editor.baseline.expectedChapterKey = original.chapterKey;
  if (original.textHash != null) editor.baseline.expectedTextHash = original.textHash;
  if (editor.seq !== null) editor.baseline.expectedTitle = editor.title;
  editor.loaded = true;
  $("#new-ch-title").value = editor.title;
  $("#new-ch-text").value = editor.text;
  $("#chapter-editor-status").textContent = "內容已載入";
  $("#chapter-draft-discard").hidden = true;
  try {
    const draft = JSON.parse(sessionStorage.getItem(editor.draftKey) || "null");
    if (draft && typeof draft.title === "string" && typeof draft.text === "string") {
      $("#new-ch-title").value = draft.title;
      $("#new-ch-text").value = draft.text;
      // 保留暫存時的版本條件，不能把過期草稿偽裝成目前版本。
      editor.baseline = draft.baseline || editor.baseline;
      $("#chapter-editor-status").textContent = "已找回此分頁的未儲存文字，請確認後儲存";
      $("#chapter-draft-discard").hidden = false;
    }
  } catch (_) { /* 暫存不可用時仍可正常編輯。 */ }
  updateChapterEditor();
}
async function loadChapterOriginal(editor) {
  editor.loaded = false;
  $("#chapter-editor-status").textContent = "正在載入原章節內容…";
  $("#chapter-editor-retry").hidden = true;
  $("#chapter-draft-discard").hidden = true;
  updateChapterEditor();
  try {
    const data = await api(`/api/books/${editor.bookId}/chapters/${editor.seq}`);
    if (!chapterEditorCurrent(editor)) return;
    if (typeof data?.chapter?.text !== "string") throw new Error("未取得原章節內容");
    applyChapterOriginal(editor, data.chapter);
    $("#new-ch-title").focus();
    void loadChapterRevisions(editor);
  } catch (e) {
    if (!chapterEditorCurrent(editor)) return;
    $("#chapter-editor-status").textContent = `載入失敗：${e.message}`;
    $("#chapter-editor-retry").hidden = false;
    updateChapterEditor();
  }
}
function beginChapterEditor(seq) {
  if (!state.book) return;
  const editor = { bookId: state.book.id, accountId: state.me?.id, route: location.hash, seq,
    loaded: false, busy: false, title: "", text: "", baseline: {},
    draftKey: `storylingo.chapter-draft:${state.me?.id}:${state.book.id}:${seq ?? "new"}` };
  chapterEditor = editor;
  $("#new-ch-title").value = "";
  $("#new-ch-text").value = "";
  $("#chapter-editor-book").textContent = state.book.title;
  $("#chapter-modal .modal-title").textContent = seq === null ? "新增章節" : "編輯章節";
  $("#chapter-revisions").hidden = true;
  $("#chapter-revisions").open = false;
  $("#chapter-editor-retry").hidden = true;
  openAppModal("#chapter-modal", { focus: "#new-ch-title" });
  if (seq === null) applyChapterOriginal(editor, {});
  else void loadChapterOriginal(editor);
}
function openChapterEdit(seq) { beginChapterEditor(seq); }
function openAddChapterModal() { beginChapterEditor(null); }

function bindChapterModal() {
  const modal = $("#chapter-modal");
  $("#btn-add-chapter").addEventListener("click", openAddChapterModal);
  $("#new-ch-cancel").addEventListener("click", () => closeAppModal("#chapter-modal"));
  modal.addEventListener("click", (e) => { if (e.target === modal) closeAppModal("#chapter-modal"); });
  ["#new-ch-title", "#new-ch-text"].forEach((sel) => $(sel).addEventListener("input", cacheChapterDraft));
  $("#chapter-editor-retry").addEventListener("click", () => { if (chapterEditor && !chapterEditor.busy) void loadChapterOriginal(chapterEditor); });
  $("#chapter-draft-discard").addEventListener("click", () => {
    const editor = chapterEditor;
    if (!editor || editor.busy || !confirm("確定捨棄未儲存文字並載入伺服器版本？需要保留的內容請先複製。")) return;
    try { sessionStorage.removeItem(editor.draftKey); } catch (_) { return; }
    if (editor.seq === null) applyChapterOriginal(editor, {});
    else void loadChapterOriginal(editor);
  });
  window.addEventListener("beforeunload", (e) => {
    if (chapterEditorDirty() || chapterEditor?.busy) { e.preventDefault(); e.returnValue = ""; }
  });
  modal.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); $("#new-ch-submit").click(); }
  });
  $("#new-ch-submit").addEventListener("click", async () => {
    const editor = chapterEditor;
    if (!editor || !chapterEditorCurrent(editor) || !editor.loaded || editor.busy) return;
    const title = $("#new-ch-title").value.trim();
    const text = $("#new-ch-text").value;
    if (!text.trim()) { $("#new-ch-text").focus(); return; }
    const draftSnapshot = readChapterDraft(editor);
    editor.busy = true;
    updateChapterEditor();
    $("#chapter-editor-status").textContent = "正在儲存，請稍候…";
    try {
      await api(editor.seq !== null
        ? `/api/books/${editor.bookId}/chapters/${editor.seq}`
        : `/api/books/${editor.bookId}/chapters`, {
        method: editor.seq !== null ? "PUT" : "POST",
        body: { title, text, ...editor.baseline },
      });
      clearSavedChapterDraft(editor, draftSnapshot);
      if (!chapterEditorCurrent(editor)) return;
      closeAppModal("#chapter-modal", { saved: true });
      toast(editor.seq !== null ? "章節已儲存" : "章節已新增，可回作品管理安排下一步", "ok");
      await refreshBook();
    } catch (e) {
      if (!chapterEditorCurrent(editor)) return;
      $("#chapter-editor-status").textContent = `儲存失敗：${e.message}。你的文字仍保留在編輯器中。`;
      if (e.status === 409) $("#chapter-draft-discard").hidden = false;
      toast("章節未儲存，請查看編輯器內的說明", "err");
    } finally {
      if (chapterEditorCurrent(editor)) { editor.busy = false; updateChapterEditor(); }
    }
  });
}

function bindFollow() {
  $("#btn-follow").addEventListener("click", async () => {
    if (!state.book || !state.authed) return openLogin(true);
    try {
      const path = state.book.follow ? "unfollow" : "follow";
      await api(`/api/books/${state.book.id}/${path}`, { method: "POST" });
      state.book.follow = !state.book.follow;
      updateHeaderStats();
      toast(state.book.follow ? "已加入追書" : "已取消追書", "ok");
    } catch (e) { toast(`追書操作失敗：${e.message}`, "err"); }
  });
}

/* FE-017 版本歷史：編輯章節時載入並可還原 */
async function loadChapterRevisions(editor) {
  const box = $("#chapter-revisions");
  const list = $("#chapter-revisions-list");
  try {
    const data = await api(`/api/books/${editor.bookId}/chapters/${editor.seq}/revisions`);
    if (!chapterEditorCurrent(editor)) return;
    const revs = data?.revisions || [];
    box.hidden = !revs.length;
    list.innerHTML = revs.map((r) => `<div class="rev-item"><span class="rev-meta">${esc(fmtDate(r.created_at))} · ${Number(r.chars) || 0} 字</span><button class="btn btn-ghost btn-xs" data-rev-restore="${Number(r.id)}">還原此版</button></div>`).join("");
    list.querySelectorAll("[data-rev-restore]").forEach((btn) => btn.addEventListener("click", () => restoreRevision(editor, Number(btn.dataset.revRestore))));
  } catch (_) {
    if (chapterEditorCurrent(editor)) {
      box.hidden = false;
      list.textContent = "版本歷史暫時無法載入。可繼續編輯，稍後重新開啟再試。";
    }
  }
}
async function restoreRevision(editor, revisionId) {
  if (!chapterEditorCurrent(editor) || editor.busy) return;
  if (!confirm("確定還原到此版本？伺服器目前的內容會保留為歷史版本；編輯器內未儲存的文字將被取代。")) return;
  const draftSnapshot = readChapterDraft(editor);
  editor.busy = true;
  updateChapterEditor();
  try {
    await api(`/api/books/${editor.bookId}/chapters/${editor.seq}/revisions/${revisionId}/restore`, { method: "POST" });
    clearSavedChapterDraft(editor, draftSnapshot);
    if (!chapterEditorCurrent(editor)) return;
    // Book summary 不含原文，必須重新讀取 chapter contract。
    await loadChapterOriginal(editor);
    if (!chapterEditorCurrent(editor)) return;
    toast("版本已還原", "ok");
    await refreshBook();
  } catch (e) {
    if (chapterEditorCurrent(editor)) $("#chapter-editor-status").textContent = `還原失敗：${e.message}`;
  } finally {
    if (chapterEditorCurrent(editor)) { editor.busy = false; updateChapterEditor(); }
  }
}

const EMOTION_LABEL = {
  happy: "開心", sad: "悲傷", angry: "生氣", afraid: "害怕",
  surprised: "驚訝", calm: "平靜", excited: "興奮", tender: "溫柔",
};

function emotionSummaryOf(segments) {
  const counts = {};
  (segments || []).forEach((s) => {
    const e = s.emotion && s.emotion.value;
    if (e && e !== "neutral") counts[e] = (counts[e] || 0) + 1;
  });
  const parts = Object.entries(counts).map(([k, n]) => `${EMOTION_LABEL[k] || k}×${n}`);
  return parts.join("、");
}

function compatibilitySegments(analysis) {
  const chars = Object.fromEntries((analysis.characters || []).map((c) => [c.character_id, c.canonical_name]));
  return (analysis.segments || analysis.legacySegments || []).map((s) => {
    const out = { ...s };
    out.id = out.id || out.segment_id;
    const genericSpeaker = ["unknown", "speaker", "generic", "narrator", "說話者", "語者", "角色", "某人", "有人", "旁白", "未解析語者"]
      .includes(String(out.speaker || "").trim().toLowerCase());
    if (out.speaker_id === "speaker:narrator" || ["narrator", "旁白"].includes(String(out.speaker || "").trim().toLowerCase())) out.speaker = "旁白";
    else if (out.speaker_id === "speaker:unresolved" || !out.speaker || genericSpeaker) {
      out.speaker = chars[out.speaker_id] || (out.speaker_id === "speaker:narrator" ? "旁白" : "待辨識語者");
    }
    if (out.emotion && out.emotion.label && !out.emotion.value) out.emotion = { ...out.emotion, value: out.emotion.label };
    return out;
  });
}

function vocabularyItemsFromAnalysis(analysis) {
  const learned = analysis?.learning?.vocab;
  if (Array.isArray(learned)) return learned.filter((item) => item && item.en);
  if (analysis?.canonical) return [];
  return (analysis?.segments || [])
    .filter((segment) => segment.type === "vocab")
    .map((segment) => segment.vocab || {})
    .filter((item) => item.en);
}

// Phase 15b：優先取 V4 canonical analysis（GET /chapters/{seq}/analysis）；
// 舊章節（legacy analysis）fallback 至既有 storage analysis。
async function getChapterAnalysis(seq) {
  if (state.analysisCache[seq]) return state.analysisCache[seq];
  const wc = wfChapter(seq);
  const readyStates = ["needs_voice_configuration", "ready_to_generate", "audio_generating", "audio_ready"];
  if (wc.state && readyStates.includes(wc.state)) {
    try {
      const res = await api(`/api/books/${state.book.id}/chapters/${seq}/analysis`);
      if (res && res.status === "ready") {
        const analysis = {
          speakers: res.speakers || [],
          characters: res.characters || [],
          segments: compatibilitySegments(res),
          learning: res.learning || {},
          canonical: true,
          emotionSummary: emotionSummaryOf(res.segments),
        };
        state.analysisCache[seq] = analysis;
        return analysis;
      }
    } catch (e) { /* fallback legacy */ }
  }
  const data = await api(`/api/books/${state.book.id}/chapters/${seq}`);
  const analysis = data.analysis || {};
  state.analysisCache[seq] = analysis;
  return analysis;
}

async function renderDetailIfNeeded(seq) {
  const detail = document.querySelector(`[data-detail="${seq}"]`);
  if (!detail) return;
  const b = state.book;
  const ch = b.chapters.find((c) => c.seq === seq);
  if (!ch) return;

  const wc = wfChapter(seq);
  const key = (wc.state || "") + "|" + (wc.nextAction || "") + "|" + (wc.error || "") + (wc.providerUnavailable ? ":pu" : "");
  if (detail.dataset.rendered === key) return; // 同一狀態不重建（避免打斷使用者操作）
  detail.dataset.rendered = key;

  if (wc.state === "analysis_running") {
    detail.innerHTML = `<div class="detail-empty"><span class="spin" style="margin-right:8px"></span>${esc(analysisProgressLabel(wc))}`
      + (isAdmin() ? `<div class="detail-actions" style="margin-top:12px"><button class="btn btn-ghost" data-v4act="cancel-analysis" data-seq="${seq}">取消分析</button></div>` : "")
      + `</div>`;
    bindRowListeners(detail);
    return;
  }
  if (wc.state === "analysis_failed") {
    detail.innerHTML = `<div class="detail-empty"><strong>AI 分析失敗</strong><div class="ch-error">${esc(wc.error || "分析失敗")}</div><div style="margin-top:10px">${isAdmin() ? "請點擊「重新分析」再次嘗試。" : "請由 Reviewer 依申請流程重新處理。"}</div></div>`;
    return;
  }
  if (wc.state === "provider_unavailable") {
    detail.innerHTML = `<div class="detail-empty">朗讀服務暫時無法使用，請稍後再試或聯絡管理員。</div>`;
    return;
  }
  if (wc.state === "needs_analysis" || !wc.state) {
    detail.innerHTML = `<div class="detail-empty">${isAdmin() ? "此章尚未分析。點擊「開始分析角色」後，這裡會列出偵測到的角色並可設定聲線。" : "此章尚未分析；請由 Reviewer 依核准的內容申請處理。"}</div>`;
    return;
  }

  let analysis;
  try { analysis = await getChapterAnalysis(seq); }
  catch (e) { detail.innerHTML = `<div class="detail-empty">讀取分析失敗：${esc(e.message)}</div>`; return; }

  const speakers = analysis.speakers || [];
  const vocabs = vocabularyItemsFromAnalysis(analysis);
  const voices = b.voices || {};

  if (!isAdmin()) {
    // 讀者／作者／Reviewer：只顯示單字與播放，不顯示語者綁定／分析／生成。
    // Author 仍可在章節列編輯自己的內容，但生成操作須走申請與 Reviewer 流程。
    let gh = `<div class="detail-section">
      <div class="detail-title">本章單字 ${vocabs.length ? `<span class="count-badge">${vocabs.length}</span>` : ""}</div>`;
    if (vocabs.length) {
      gh += `<div class="vocab-chips">${vocabs.map((v) =>
        `<span class="vocab-chip"><span class="en">${esc(v.en || "")}</span><span class="zh">${esc(v.zh || "")}</span><span class="lv">${esc(v.level || "A1")}</span></span>`).join("")}</div>`;
    } else {
      gh += `<span class="detail-empty">本章沒有單字。</span>`;
    }
    gh += `</div><div class="detail-section"><div class="detail-title">操作</div><div class="detail-actions">`;
    if (ch.audio === "ready") gh += `<button class="btn btn-accent" data-play="${seq}">播放本章</button>`;
    gh += `</div></div>`;
    detail.innerHTML = gh;
    detail.querySelectorAll("[data-play]").forEach((btn) =>
      btn.addEventListener("click", () => openPlayer(+btn.dataset.play)));
    detail.querySelectorAll(".vocab-chip").forEach((chip) =>
      chip.addEventListener("click", (e) => {
        e.stopPropagation();
        showVocabPopup(chip.querySelector(".en").textContent, chip.querySelector(".zh").textContent, chip.querySelector(".lv").textContent);
      }));
    return;
  }

  const hasSpeech = speakers.length > 0;
  const spkLang = effCategory(b) === "en" ? "en" : "zh";
  const isMulti = ((b.workflow && b.workflow.mode) || "single") === "multi";
  const spkList = (state.voices || []).filter((v) => voiceSupportsLanguage(v, spkLang));
  const langWarn = !state.voices.length
    ? `<div class="tts-setup-warning"><strong>目前朗讀服務尚未提供可用的聲線</strong><span>請管理員先到「管理中心 → TTS 服務」新增 provider、測試連線並設為啟用。</span></div>`
    : !spkList.length
      ? `<div class="tts-setup-warning"><strong>目前朗讀服務沒有支援此語言的聲線</strong><span>此作品的語言（${CAT_LABEL[effCategory(b)] || effCategory(b)}）在目前朗讀服務中沒有可選聲線，無法綁定語者。</span></div>`
      : "";

  let html = "";
  if (hasSpeech) {
    html += `${langWarn}<div class="detail-section">
      <div class="detail-title">角色聲線 <span class="count-badge">${speakers.length} 位角色</span></div>
      <div class="speaker-grid">`;
    speakers.forEach((sp) => {
      const color = colorOf(sp.name);
      const gender = sp.gender || "未知";
      const age = sp.age || "未知";
      const gCls = gender === "男" ? "m" : gender === "女" ? "f" : "u";
      const cur = typeof voices[sp.name] === "string" ? voices[sp.name].trim() : "";
      const unassigned = !cur;
      const conf = sp.conflicts || [];
      const confBadge = conf.length
        ? `<span class="conflict-badge" title="${esc(conf.map((c) => `第${c.ch}章判定：${c.gender}/${c.age}`).join("；"))}">與後續章節不同</span>`
        : "";
      html += `<div class="speaker-card${unassigned ? " unassigned" : ""}">
        <div class="sp-head">
          <span class="sp-avatar" style="background:${color}">${esc(sp.name.slice(0, 1))}</span>
          <span class="sp-name">${esc(sp.name)}</span>
          <span class="sp-gender ${gCls}">${gender}</span>
          ${age !== "未知" ? `<span class="sp-age">${esc(age)}</span>` : ""}
          ${confBadge}
        </div>
        <div class="sp-select-row">
          <select data-spk="${esc(sp.name)}">${voiceOptions(spkLang, cur, true)}</select>
          <button class="btn btn-ghost btn-icon sp-preview" data-preview="1" title="試聽">${PLAY_ICON_SM}</button>
        </div>
        ${unassigned ? `<span class="sp-unassigned-badge">未指定聲線</span>` : ""}
      </div>`;
    });
    html += `</div>
      ${needsEnglishVoice(b) ? `<div class="speaker-card" style="margin-top:12px">
        <div class="sp-head">
          <span class="sp-avatar" style="background:#3fb950">En</span>
          <span class="sp-name">英語教學聲線</span>
        </div>
        <div class="sp-select-row">
          <select data-spk="_english">${voiceOptions("en", voices["_english"])}</select>
          <button class="btn btn-ghost btn-icon sp-preview" data-preview="1" title="試聽">${PLAY_ICON_SM}</button>
        </div>
      </div>` : ""}
      <div class="detail-actions" style="margin-top:14px">
        <button class="btn" data-ai-match-voices="${seq}">AI 匹配語者（僅填補未指定）</button>
        <button class="btn btn-ghost" data-ai-match-all="${seq}">全部重置並重新 AI 匹配</button>
        <button class="btn btn-accent" data-save-voices="${seq}">儲存全部聲線</button>
      </div>
    </div>`;
  }

  if (analysis.canonical) {
    html += `<div class="detail-section"><div class="detail-title">本章情緒</div><span class="detail-empty">${esc(analysis.emotionSummary || "無特別情緒標記")}</span></div>`;
  }

  html += `<div class="detail-section">
    <div class="detail-title">本章單字 ${vocabs.length ? `<span class="count-badge">${vocabs.length}</span>` : ""}</div>`;
  if (vocabs.length) {
    html += `<div class="vocab-chips">${vocabs.map((v) =>
      `<span class="vocab-chip"><span class="en">${esc(v.en || "")}</span><span class="zh">${esc(v.zh || "")}</span><span class="lv">${esc(v.level || "A1")}</span></span>`).join("")}</div>`;
  } else {
    html += `<span class="detail-empty">${analysis.canonical ? "本章沒有學習資料。" : "本章未挑到適合教學的簡單單字。"}</span>`;
  }
  html += `</div>`;

  html += `<div class="detail-section">
    <div class="detail-title">操作</div>
    <label class="corr-row">
      <input type="checkbox" data-ttscorrect="${seq}" ${(b.settings && b.settings.ttsCorrect) ? "checked" : ""}>
      <span class="corr-name">TTS 發音校正</span>
      <span class="corr-hint">套用文字差異.xlsx 校正唸法（顯示文字不變）</span>
    </label>
    <div class="detail-actions">`;
  if (wc.state === "audio_ready") {
    html += `<button class="btn btn-accent" data-play="${seq}">播放本章</button>`;
    html += `<button class="btn" data-v4act="generate" data-force="1" data-seq="${seq}">重新生成音訊</button>`;
  } else if (wc.nextAction === "generate") {
    html += `<button class="btn btn-accent" data-v4act="generate" data-seq="${seq}">生成音訊</button>`;
    html += `<button class="btn btn-ghost" data-preview-text="${seq}" title="無音檔時可直接閱讀文字">閱讀文字</button>`;
  } else if (wc.nextAction === "configure_voices") {
    html += `<span class="detail-empty">請先在上方為每位角色選擇聲線並儲存，接著就能生成音訊。</span>`;
  }
  if (isMulti) html += `<button class="btn btn-ghost" data-reanalyze="${seq}">重新分析</button>`;
  html += `</div></div>`;

  detail.innerHTML = html;
  detail.querySelectorAll("[data-ttscorrect]").forEach((cb) =>
    cb.addEventListener("change", async () => {
      await api(`/api/books/${state.book.id}/settings`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ttsCorrect: cb.checked }),
      });
      state.book = await api("/api/books/" + state.book.id);
      toast(cb.checked ? "已開啟發音校正，重新生成音訊即套用" : "已關閉發音校正", "ok");
    }));
  detail.querySelectorAll("[data-preview]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const sel = btn.closest(".speaker-card").querySelector("select");
      previewVoice(sel.value, btn);
    }));
  detail.querySelectorAll("[data-save-voices]").forEach((btn) =>
    btn.addEventListener("click", () => saveVoicesFromDetail(+btn.dataset.saveVoices)));
detail.querySelectorAll("[data-ai-match-voices]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const seq = +btn.dataset.aiMatchVoices;
      btn.disabled = true;
      btn.textContent = "匹配中…";
      try {
        const res = await api(`/api/books/${state.book.id}/voices/ai-match`, {
          method: "POST", body: { prefs: state.voicePrefs },
        });
        const matches = res.matches || [];
        const nv = { ...state.book.voices };
        for (const m of matches) {
          if (m.voice_id) nv[m.speaker] = m.voice_id;
        }
        state.book.voices = nv;
        const dt = document.querySelector(`[data-detail="${seq}"]`);
        if (dt) dt.dataset.rendered = "";
        renderDetailIfNeeded(seq);
        // AI matching is persisted by the API; refresh the book so every view
        // (including the player/sidebar) receives the same mapping immediately.
        state.book = await api("/api/books/" + state.book.id);
        if (dt) dt.dataset.rendered = "";
        await renderDetailIfNeeded(seq);
        const total = res.total ?? matches.length;
        const assigned = res.assigned ?? matches.filter((m) => m.voice_id).length;
        if (assigned > 0) toast(`已自動匹配 ${assigned} / ${total} 個角色，未匹配者仍待設定`, "ok");
        else toast(`沒有可自動匹配的角色：${res.message || "請手動設定聲線"}`, "warning");
      } catch (e) {
        toast(`AI 匹配失敗：${e.message}`, "err");
      } finally {
        btn.disabled = false;
        btn.textContent = "AI 匹配語者（僅填補未指定）";
      }
    }));
  detail.querySelectorAll("[data-ai-match-all]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const seq = +btn.dataset.aiMatchAll;
      if (!confirm("將清除所有已手動選擇的語者，並對全部角色重新 AI 匹配。確定嗎？")) return;
      btn.disabled = true;
      btn.textContent = "重置並匹配中…";
      try {
        const en = state.book.voices["_english"] || "en-US-JennyNeural";
        state.book.voices = { "_english": en };
        const res = await api(`/api/books/${state.book.id}/voices/ai-match`, {
          method: "POST", body: { prefs: state.voicePrefs, voices: { _english: en } },
        });
        const matches = res.matches || [];
        const nv = { "_english": en };
        for (const m of matches) {
          if (m.voice_id) nv[m.speaker] = m.voice_id;
        }
        state.book.voices = nv;
        const dt = document.querySelector(`[data-detail="${seq}"]`);
        if (dt) dt.dataset.rendered = "";
        renderDetailIfNeeded(seq);
        state.book = await api("/api/books/" + state.book.id);
        if (dt) dt.dataset.rendered = "";
        await renderDetailIfNeeded(seq);
        const total = res.total ?? matches.length;
        const assigned = res.assigned ?? matches.filter((m) => m.voice_id).length;
        if (assigned > 0) toast(`已自動匹配 ${assigned} / ${total} 個角色，未匹配者仍待設定`, "ok");
        else toast(`沒有可自動匹配的角色：${res.message || "請手動設定聲線"}`, "warning");
      } catch (e) {
        toast(`重置匹配失敗：${e.message}`, "err");
      } finally {
        btn.disabled = false;
        btn.textContent = "全部重置並重新 AI 匹配";
      }
    }));
  detail.querySelectorAll("[data-play]").forEach((btn) =>
    btn.addEventListener("click", () => openPlayer(+btn.dataset.play)));
  detail.querySelectorAll("[data-preview-text]").forEach((btn) =>
    btn.addEventListener("click", () => openPlayer(+btn.dataset.previewText)));
  detail.querySelectorAll("[data-v4act]").forEach((btn) =>
    btn.addEventListener("click", () => runWorkflowAction(
      btn.dataset.v4act, +btn.dataset.seq, btn.dataset.force === "1")));
  detail.querySelectorAll("[data-reanalyze]").forEach((btn) =>
    btn.addEventListener("click", () => {
      delete state.analysisCache[seq];
      delete state.expandedSeq;
      analyzeChapter(seq, true);
    }));
  detail.querySelectorAll(".vocab-chip").forEach((chip) =>
    chip.addEventListener("click", (e) => {
      e.stopPropagation();
      showVocabPopup(chip.querySelector(".en").textContent, chip.querySelector(".zh").textContent, chip.querySelector(".lv").textContent);
    }));
}

const PLAY_ICON_SM = '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';

function voiceSupportsLanguage(voice, language) {
  const values = Array.isArray(voice.languages) && voice.languages.length
    ? voice.languages : (voice.lang ? [voice.lang] : []);
  return values.some((value) => String(value).split(/[-_]/)[0].toLowerCase() === language);
}

function voiceOptions(lang, current, showUsage = false) {
  const list = state.voices.filter((v) => voiceSupportsLanguage(v, lang));
  // current="" 表示未指定，讓第一個 option（value=""）被選中
  const val = (current !== undefined && current !== null && current !== "") ? current : "";
  // 計算各語者被使用次數（排除 _english）
  const usage = {};
  if (showUsage && state.book && state.book.voices) {
    for (const [_, vid] of Object.entries(state.book.voices)) {
      if (vid && vid !== val) usage[vid] = (usage[vid] || 0) + 1;
    }
  }
  const opts = ['<option value="">未指定</option>'];
  for (const v of list) {
    const u = usage[v.id] ? `（用${usage[v.id]}次）` : "";
    const sel = v.id === val ? "selected" : "";
    opts.push(`<option value="${v.id}" ${sel}>${esc(v.name)}${u}</option>`);
  }
  return opts.join("");
}

async function saveVoicesFromDetail(seq) {
  const detail = document.querySelector(`[data-detail="${seq}"]`);
  const nv = { ...(state.book.voices || {}) };
  detail.querySelectorAll("select[data-spk]").forEach((sel) => { nv[sel.dataset.spk] = sel.value; });
  nv["_english"] = nv["_english"] || "en-US-JennyNeural";
  await api(`/api/books/${state.book.id}/voices`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ voices: nv, prefs: state.voicePrefs }),
  });
  state.book = await api("/api/books/" + state.book.id);
  renderChapters();
  toast("聲線已儲存，重新「生成音訊」即套用", "ok");
}

function setPreviewButtonState(button, stateName) {
  if (!button) return;
  if (!button.dataset.previewIdleHtml) button.dataset.previewIdleHtml = button.innerHTML;
  button.dataset.previewState = stateName;
  if (stateName === "loading") {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.textContent = "載入中…";
    button.title = "試聽載入中";
  } else if (stateName === "playing") {
    button.disabled = true;
    button.removeAttribute("aria-busy");
    button.textContent = "播放中…";
    button.title = "試聽播放中";
  } else {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.innerHTML = button.dataset.previewIdleHtml;
    button.title = "試聽";
  }
}

function finishPreview(button, message, kind) {
  setPreviewButtonState(button, "idle");
  if (state.previewButton === button) state.previewButton = null;
  state.previewInFlight = false;
  if (message) toast(message, kind);
}

function requestPreviewAudio(voiceId, text, button, messages = {}) {
  if (state.previewInFlight) {
    toast("試聽正在準備或播放中，請稍候。", "info");
    return;
  }
  state.previewInFlight = true;
  state.previewButton = button || null;
  setPreviewButtonState(button, "loading");
  state.previewAudio.pause();
  state.previewAudio.onended = () => finishPreview(button, "試聽播放結束", "info");
  state.previewAudio.onerror = () => finishPreview(button, messages.failure || "試聽失敗，請稍後再試。", "err");
  NovelApi.request("/api/preview-tts", {
    method: "POST", body: { voice: voiceId, text }, responseType: "blob",
  }).then(async (blob) => {
    setPreviewBlob(blob);
    await state.previewAudio.play();
    setPreviewButtonState(button, "playing");
    toast(messages.success || "試聽播放中", "ok");
  }).catch(() => finishPreview(button, messages.failure || "試聽失敗，請稍後再試。", "err"));
}

function previewVoice(voiceId, button) {
  if (!voiceId) { toast("請先選擇聲線", "err"); return; }
  const f5v = (state.voices || []).find((v) => v.id === voiceId && v.region === "F5");
  if (f5v && f5v.f5Broken) { toast("F5 語者缺參考音檔，無法試聽", "err"); return; }
  const text = voiceId.startsWith("en")
    ? "Hello! This is the English teaching voice."
    : "你好，這是我為你朗讀小說時的聲音。";
  if (f5v) toast("F5 試聽生成中（首次較慢），請稍候…", "info");
  requestPreviewAudio(voiceId, text, button);
}

function showVocabPopup(en, zh, lv) {
  const popup = $("#vocab-popup");
  popup.querySelector(".vocab-popup-word").textContent = en || "";
  popup.querySelector(".vocab-popup-zh").textContent = zh || "";
  popup.querySelector(".vocab-popup-lv").textContent = lv || "A1";
  popup.hidden = false;
  const voiceId = (state.book && state.book.voices && state.book.voices["_english"]) || "en-US-JennyNeural";
  const f5v = (state.voices || []).find((v) => v.id === voiceId && v.region === "F5");
  if (f5v && f5v.f5Broken) { toast("英語聲線為 F5 自訂聲線但缺參考音檔，無法播放發音", "err"); return; }
  if (f5v) toast("發音生成中（F5，首次較慢）…", "info");
  requestPreviewAudio(voiceId, en || "", null, { success: "發音播放中", failure: "發音播放失敗，請稍後再試。" });
}

async function analyzeChapter(seq, force = false) {
  // POST 會立即回傳 queued，而 worker 可能在下一個 polling tick 前完成/失敗。
  // 先建立本地 processing guard，避免作者連點建立多筆相同 analysis job，
  // 並讓畫面不會在 API response 與第一次 workflow refresh 之間退回「未分析」。
  if (state.analysisInFlight.has(seq)) {
    toast("本章分析已在處理中", "info");
    return;
  }
  state.analysisInFlight.add(seq);
  const workflow = state.book && state.book.workflow;
  const chapterWorkflow = workflow && workflow.chapters && workflow.chapters[seq];
  const previousChapterWorkflow = chapterWorkflow
    ? { ...chapterWorkflow, analysisProgress: chapterWorkflow.analysisProgress
        ? { ...chapterWorkflow.analysisProgress } : null }
    : null;
  if (chapterWorkflow) {
    chapterWorkflow.state = "analysis_running";
    chapterWorkflow.nextAction = null;
    chapterWorkflow.error = "";
    chapterWorkflow.analysisProgress = {
      ...(chapterWorkflow.analysisProgress || {}),
      currentStage: "queued",
      progressPercent: 0,
      lastError: null,
    };
    renderChapters();
  }
  try {
    await api(`/api/books/${state.book.id}/chapters/${seq}/analysis`, {
      method: "POST", body: force ? { force: true } : {},
    });
  } catch (e) {
    if (chapterWorkflow && previousChapterWorkflow) {
      Object.assign(chapterWorkflow, previousChapterWorkflow);
      renderChapters();
    }
    toast(`分析失敗：${e.message}`, "err");
    state.analysisInFlight.delete(seq);
    return;
  }
  toast(`${chapterDisplayLabel(state.book, seq)}分析中…`);
  startPoll();
  await waitChapterWorkflow(seq, ["needs_voice_configuration", "ready_to_generate", "analysis_failed"]);
  const wc = wfChapter(seq);
  if (wc.state === "needs_voice_configuration" || wc.state === "ready_to_generate") {
    delete state.analysisCache[seq];
    state.expandedSeq = seq; // 自動展開，方便接著設定聲線
    toast(wc.state === "needs_voice_configuration" ? "分析完成，請設定各角色聲線" : "分析完成", "ok");
    renderChapters();
    if (state.autoGen && wc.state === "ready_to_generate") await generateChapter(seq);
  } else if (wc.state === "analysis_failed") {
    toast(`分析失敗：${wc.error || "請重試"}`, "err");
    state.expandedSeq = seq;
    renderChapters();
  } else {
    toast("分析未完成，請稍後重試", "err");
    renderChapters();
  }
  state.analysisInFlight.delete(seq);
}

async function cancelAnalysis(seq) {
  if (state.analysisCancelInFlight.has(seq)) return;
  if (!confirm("確定取消本章分析？已完成的分析結果不會成為 ready，之後可重新分析。")) return;
  state.analysisCancelInFlight.add(seq);
  document.querySelectorAll(`[data-v4act="cancel-analysis"][data-seq="${seq}"]`).forEach((button) => {
    button.disabled = true;
    button.textContent = "取消中…";
  });
  try {
    await api(`/api/books/${state.book.id}/chapters/${seq}/analysis/cancel`, { method: "POST" });
    state.analysisInFlight.delete(seq);
    delete state.analysisCache[seq];
    await refreshBook();
    toast("已取消分析，可重新分析", "ok");
  } catch (e) {
    toast(`取消分析失敗：${e.message}`, "err");
  } finally {
    state.analysisCancelInFlight.delete(seq);
    document.querySelectorAll(`[data-v4act="cancel-analysis"][data-seq="${seq}"]`).forEach((button) => {
      button.disabled = false;
      button.textContent = "取消分析";
    });
  }
}

async function generateChapter(seq, force = false) {
  try {
    await api(`/api/books/${state.book.id}/chapters/${seq}/audio-generations`, {
      method: "POST", body: force ? { force: true } : {},
    });
  } catch (e) {
    toast(`音訊生成失敗：${e.message}`, "err");
    return;
  }
  toast(`${chapterDisplayLabel(state.book, seq)}生成音訊中…`);
  startPoll();
  await waitChapterWorkflow(seq, ["audio_ready", "ready_to_generate", "audio_failed"]);
  const wc = wfChapter(seq);
  const ok = wc && wc.state === "audio_ready";
  if (wc && wc.state === "audio_failed") {
    toast(`${chapterDisplayLabel(state.book, seq)}生成失敗：${wc.error || "請重試"}`, "err");
  } else {
    toast(ok ? `${chapterDisplayLabel(state.book, seq)}音訊完成` : `${chapterDisplayLabel(state.book, seq)}音訊未完成，請重試`, ok ? "ok" : "err");
  }
  renderChapters();
}

async function analyzeAll() {
  if (state.batchActive) {
    toast("本書批次分析已在處理中", "info");
    return;
  }
  const todo = (state.book.chapters || []).filter(chapterNeedsAnalysis);
  if (!todo.length) { toast("沒有需要分析的章節", "ok"); return; }
  state.batchActive = true;
  state.batchCancelled = false;
  state.batchJobId = null;
  renderChapters();
  try {
    const created = await api(`/api/books/${state.book.id}/analysis/batch`, { method: "POST" });
    state.batchJobId = created.jobId;
    renderChapters();
    toast(`批次分析剩餘 ${todo.length} 章中…`);
    startPoll();
    await waitAllDoneWorkflow();
    toast(state.batchCancelled ? "批次分析已取消，可重新分析" : "批次分析完成", state.batchCancelled ? "info" : "ok");
  } catch (e) {
    toast(`批次分析啟動失敗：${e.message}`, "err");
  }
  state.batchActive = false;
  state.batchJobId = null;
  state.batchCancelInFlight = false;
  renderChapters();
}

async function cancelAnalysisBatch() {
  if (!state.batchActive || !state.batchJobId || state.batchCancelInFlight) return;
  if (!confirm("確定取消全書批次分析？已完成章節會保留，未完成章節可之後重新分析。")) return;
  state.batchCancelInFlight = true;
  renderChapters();
  try {
    await api(`/api/books/${state.book.id}/analysis/batch/cancel`, { method: "POST" });
    state.batchCancelled = true;
    await refreshBook();
    toast("批次分析已取消，可重新分析未完成章節", "ok");
  } catch (e) {
    state.batchCancelInFlight = false;
    toast(`取消批次分析失敗：${e.message}`, "err");
    renderChapters();
  }
}

async function ttsAll() {
  const todo = (state.book.chapters || []).filter(chapterNeedsGeneration);
  if (!todo.length) { toast("沒有需要生成音訊的章節", "ok"); return; }
  state.batchActive = true;
  try {
    await api(`/api/books/${state.book.id}/audio-generations/batch`, { method: "POST" });
    toast(`批次生成 ${todo.length} 章音訊中…`);
    startPoll();
    await waitAllDoneWorkflow();
    toast("批次音訊生成完成", "ok");
  } catch (e) {
    toast(`批次生成啟動失敗：${e.message}`, "err");
  }
  state.batchActive = false;
  renderChapters();
}

// 批次執行中：連續兩次快照都不是 busy 即視為完成（含批次太快完成、從未看到 busy 的情況）。
// 為避免 worker 尚未開始（job 仍在 queued）就被誤判完成，
// 需「曾看到 busy」或已過 8 秒緩衝期，才允許進入穩定判定。
function waitAllDoneWorkflow() {
  let stable = 0;
  let busySeen = false;
  const started = Date.now();
  return new Promise((resolve) => {
    const iv = setInterval(async () => {
      try {
        if (state.batchCancelled) { clearInterval(iv); resolve(); return; }
        const b = await api("/api/books/" + state.book.id);
        state.book = b;
        renderPoll();
        const wf = b.workflow;
        const busy = wf && Object.values(wf.chapters || {}).some((ch) => ch.state === "analysis_running" || ch.state === "audio_generating");
        if (busy) { busySeen = true; stable = 0; }
        else if (busySeen || Date.now() - started > 8000) {
          stable += 1;
          if (stable >= 2) { clearInterval(iv); resolve(); }
        } else {
          stable = 0;
        }
        if (Date.now() - started > 240000) { clearInterval(iv); resolve(); }
      } catch (e) { clearInterval(iv); resolve(); }
    }, 2000);
  });
}

function bindChapterBar() {
  // init() runs for every route; the chapter toolbar only exists on the book detail view.
  // Missing route-specific controls must not abort rendering of pages such as #/mine.
  if (!$("#bulk-ttscorrect")) return;
  $("#bulk-ttscorrect").addEventListener("change", async () => {
    const on = $("#bulk-ttscorrect").checked;
    try {
      await api(`/api/books/${state.book.id}/settings`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ttsCorrect: on }),
      });
      state.book = await api("/api/books/" + state.book.id);
      toast(on ? "已開啟發音校正，生成音訊即套用" : "已關閉發音校正", "ok");
    } catch (e) {
      $("#bulk-ttscorrect").checked = !on;
      toast(`設定失敗：${e.message}`, "err");
    }
  });
  $("#btn-analyze-all").addEventListener("click", async () => {
    $("#btn-analyze-all").disabled = true;
    try { await analyzeAll(); } finally { $("#btn-analyze-all").disabled = false; }
  });
  $("#btn-cancel-analyze-all").addEventListener("click", cancelAnalysisBatch);
  $("#btn-tts-all").addEventListener("click", async () => {
    $("#btn-tts-all").disabled = true;
    try { await ttsAll(); } finally { $("#btn-tts-all").disabled = false; }
  });
  $("#btn-book-voices").addEventListener("click", () => {
    const panel = $("#book-voices-panel");
    panel.hidden = !panel.hidden;
    if (!panel.hidden) renderBookVoicesPanel();
  });
  $("#toggle-auto-tts").addEventListener("change", (e) => { state.autoGen = e.target.checked; });
  document.querySelectorAll("#ch-filter .chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      state.filter = chip.dataset.filter;
      document.querySelectorAll("#ch-filter .chip").forEach((c) => c.classList.toggle("active", c === chip));
      renderChapters();
    }));
}

/* ---------- 朗讀設定（Phase 15b：audioMode／defaultVoiceId） ---------- */

function openAudioSetup() {
  const setup = $("#audio-setup");
  if (!setup) return;
  setup.hidden = false;
  setup.scrollIntoView({ behavior: "smooth", block: "center" });
  const voice = $("#sel-default-voice");
  if (voice && !voice.hidden) voice.focus();
}

function renderAudioSetup() {
  const setup = $("#audio-setup");
  if (!setup) return;
  const b = state.book;
  if (!isAdmin()) { setup.hidden = true; return; }
  setup.hidden = false;
  const mode = (b.workflow && b.workflow.mode) || "single";
  const modeSel = $("#sel-audio-mode");
  if (modeSel && modeSel.value !== mode) modeSel.value = mode;
  const singleWrap = $("#single-voice-field");
  if (singleWrap) singleWrap.hidden = mode !== "single";
  const voiceSel = $("#sel-default-voice");
  if (voiceSel) {
    const cur = b.defaultVoiceId || "";
    const spkLang = effCategory(b) === "en" ? "en" : "zh";
    voiceSel.innerHTML = voiceOptions(spkLang, cur);
    const langHint = $("#voice-lang-hint");
    if (langHint) {
      const available = (state.voices || []).filter((v) => voiceSupportsLanguage(v, spkLang));
      if (!state.voices.length) {
        langHint.hidden = false;
        langHint.textContent = "目前朗讀服務尚未提供可用的聲線，請管理員先到「管理中心 → TTS 服務」新增並測試連線。";
      } else if (!available.length) {
        langHint.hidden = false;
        langHint.textContent = "目前朗讀服務沒有支援此語言的聲線，無法設定朗讀聲線。";
      } else {
        langHint.hidden = true;
      }
    }
  }
}

function bindAudioSetup() {
  const modeSel = $("#sel-audio-mode");
  const voiceSel = $("#sel-default-voice");
  if (modeSel) {
    modeSel.addEventListener("change", async () => {
      try {
        await api(`/api/books/${state.book.id}`, { method: "PUT", body: { audioMode: modeSel.value } });
        toast("朗讀方式已更新", "ok");
        await refreshBook();
      } catch (e) { toast(`更新失敗：${e.message}`, "err"); }
    });
  }
  if (voiceSel) {
    voiceSel.addEventListener("change", async () => {
      try {
        await api(`/api/books/${state.book.id}`, { method: "PUT", body: { defaultVoiceId: voiceSel.value || null } });
        toast("朗讀聲線已更新", "ok");
        await refreshBook();
      } catch (e) { toast(`更新失敗：${e.message}`, "err"); }
    });
  }
  $("#btn-preview-default-voice")?.addEventListener("click", () => {
    if (!voiceSel) return;
    previewVoice(voiceSel.value, $("#btn-preview-default-voice"));
  });
}

/* 全書語者聲線面板：一次綁定所有已分析章節累積的角色 */
function renderBookVoicesPanel() {
  const panel = $("#book-voices-panel");
  const b = state.book;
  const voices = b.voices || {};
  const spks = Object.keys(voices).filter((k) => k !== "_english")
    .sort((x, y) => {
      const cx = ((b.speakerInfo || {})[x] || {}).count || 0;
      const cy = ((b.speakerInfo || {})[y] || {}).count || 0;
      return cy - cx || x.localeCompare(y, "zh-Hant");
    });
  if (!spks.length) {
    panel.innerHTML = `<div class="detail-title">全書語者聲線</div>
      <div class="detail-empty">尚未分析出角色。先分析章節後，這裡會列出全書所有角色一次綁定。</div>`;
    return;
  }
  const spkLang = effCategory(b) === "en" ? "en" : "zh";
  const spkList = (state.voices || []).filter((v) => voiceSupportsLanguage(v, spkLang));
    const zhList = (state.voices || []).filter((v) => voiceSupportsLanguage(v, "zh"));
    const preferenceVoices = (state.voices || []).filter((v) => voiceSupportsLanguage(v, spkLang));
  const zhUsage = {};
  for (const [_, vid] of Object.entries(voices)) { if (vid) zhUsage[vid] = (zhUsage[vid] || 0) + 1; }
  const langWarn = !state.voices.length
    ? `<div class="tts-setup-warning"><strong>目前朗讀服務尚未提供可用的聲線</strong><span>請管理員先到「管理中心 → TTS 服務」新增 provider、測試連線並設為啟用。</span></div>`
    : !spkList.length
      ? `<div class="tts-setup-warning"><strong>目前朗讀服務沒有支援此語言的聲線</strong><span>此作品的語言（${CAT_LABEL[effCategory(b)] || effCategory(b)}）在目前朗讀服務中沒有可選聲線，無法綁定語者。</span></div>`
      : "";
  let html = `${langWarn}<div class="detail-title">全書語者聲線 <span class="count-badge">${spks.length} 位角色</span></div>
    <div class="detail-hint">依出現次數排序（越上面越常出現，建議優先挑好聽的音色）。一次綁定全書聲線；性別/年齡為跨章節彙整，跨章不一致會標示。未指定的語者會以<b style="color:var(--red)">紅色</b>提醒。</div>
    <div class="speaker-grid">`;
  spks.forEach((n) => {
    const color = colorOf(n);
    const cur = voices[n] || "";
    const unassigned = !cur;
    const si = (b.speakerInfo || {})[n] || {};
    const cnt = si.count || 0;
    const conf = si.conflicts || [];
    const confBadge = conf.length
      ? `<span class="conflict-badge" title="${esc(conf.map((c) => `第${c.ch}章判定：${c.gender}/${c.age}`).join("；"))}">跨章不同</span>`
      : "";
    html += `<div class="speaker-card${unassigned ? " unassigned" : ""}">
      <div class="sp-head">
        <span class="sp-avatar" style="background:${color}">${esc(n.slice(0, 1))}</span>
        <span class="sp-name">${esc(n)}<span class="sp-count" title="跨章節出現次數">${cnt} 次</span></span>
        ${si.gender ? `<span class="sp-gender ${si.gender === "男" ? "m" : si.gender === "女" ? "f" : "u"}">${esc(si.gender)}</span>` : ""}
        ${si.age && si.age !== "未知" ? `<span class="sp-age">${esc(si.age)}</span>` : ""}
        ${confBadge}
      </div>
      <div class="sp-select-row">
        <select data-bspk="${esc(n)}">${voiceOptions(spkLang, cur, true)}</select>
        <button class="btn btn-ghost btn-icon sp-preview" data-preview="1" title="試聽">${PLAY_ICON_SM}</button>
      </div>
      ${unassigned ? `<span class="sp-unassigned-badge">未指定聲線</span>` : ""}
    </div>`;
  });
  html += `</div>
    ${needsEnglishVoice(b) ? `<div class="speaker-card" style="margin-top:12px">
      <div class="sp-head">
        <span class="sp-avatar" style="background:#3fb950">En</span>
        <span class="sp-name">英語教學聲線</span>
      </div>
      <div class="sp-select-row">
        <select data-bspk="_english">${voiceOptions("en", voices["_english"])}</select>
        <button class="btn btn-ghost btn-icon sp-preview" data-preview="1" title="試聽">${PLAY_ICON_SM}</button>
      </div>
    </div>` : ""}
    <div class="prefs-title">AI 匹配語音偏好 <span class="count-badge">${preferenceVoices.length} 個語音</span></div>
    <div class="detail-hint">「保留不匹配」：不參與 AI 匹配（例如你想留給主角/旁白的手選語音）。「僅一次」：AI 只把它配給一個角色，避免大家同一個聲音。</div>
    <div class="prefs-list">${preferenceVoices.map((v) => {
      const p = state.voicePrefs[v.id] || {};
      const exclude = p.exclude ? "checked" : "";
      // backend 欄位 reuse=true 代表可重用；UI checkbox 表示「僅一次」，故反向映射。
      const singleUse = p.reuse !== true ? "checked" : "";
      const cnt = zhUsage[v.id] || 0;
      return `<div class="prefs-row">
        <span class="prefs-name">${esc(v.name)}${cnt ? `<span class="sp-count">用${cnt}次</span>` : ""}</span>
        <label class="prefs-tog">保留不匹配<input type="checkbox" data-exclude="${esc(v.id)}" ${exclude}></label>
        <label class="prefs-tog">僅一次<input type="checkbox" data-reuse="${esc(v.id)}" ${singleUse}></label>
      </div>`;
    }).join("")}</div>
    <div class="detail-actions" style="margin-top:14px">
      <button class="btn" id="book-voices-ai-match">AI 匹配語者（僅填補未指定）</button>
      <button class="btn btn-ghost" id="book-voices-ai-match-all">全部重置並重新 AI 匹配</button>
      <button class="btn btn-accent" id="book-voices-save">儲存全部聲線</button>
    </div>`;
  panel.innerHTML = html;
  if (!state.voices.length) {
    panel.insertAdjacentHTML("afterbegin", `<div class="tts-setup-warning"><strong>目前沒有可用聲線</strong><span>請管理員先到「管理中心 → TTS 服務」新增遠端 provider、測試連線並設為啟用；正式環境不提供 Edge/F5 聲線。</span></div>`);
  }

  panel.querySelectorAll("[data-preview]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const sel = btn.closest(".speaker-card").querySelector("select");
      previewVoice(sel.value, btn);
    }));
$("#book-voices-ai-match").addEventListener("click", async () => {
    const btn = $("#book-voices-ai-match");
    btn.disabled = true;
    btn.textContent = "匹配中…";
    try {
      const res = await api(`/api/books/${state.book.id}/voices/ai-match`, { method: "POST", body: { prefs: state.voicePrefs } });
      const matches = res.matches || [];
      // 先更新 state.book.voices 再重新渲染
      const nv = { ...state.book.voices };
      for (const m of matches) {
        if (m.voice_id) nv[m.speaker] = m.voice_id;
      }
        state.book.voices = nv;
        renderBookVoicesPanel();
        state.book = await api("/api/books/" + state.book.id);
        renderBookVoicesPanel();
      const total = res.total ?? matches.length;
      const assigned = res.assigned ?? matches.filter((m) => m.voice_id).length;
      if (assigned > 0) toast(`已自動匹配 ${assigned} / ${total} 個角色，未匹配者仍待設定`, "ok");
      else toast(`沒有可自動匹配的角色：${res.message || "請手動設定聲線"}`, "warning");
    } catch (e) {
      toast(`AI 匹配失敗：${e.message}`, "err");
    } finally {
      btn.disabled = false;
      btn.textContent = "AI 匹配語者（僅填補未指定）";
    }
  });
  $("#book-voices-ai-match-all").addEventListener("click", async () => {
    const btn = $("#book-voices-ai-match-all");
    if (!confirm("將清除所有已手動選擇的語者，並對全部角色重新 AI 匹配。確定嗎？")) return;
    btn.disabled = true;
    btn.textContent = "重置並匹配中…";
    try {
      // 清除所有語者（保留 _english）
      const en = state.book.voices["_english"] || "en-US-JennyNeural";
      state.book.voices = { "_english": en };
      // 呼叫 AI 匹配（此時全部角色都是未指定）
      const res = await api(`/api/books/${state.book.id}/voices/ai-match`, { method: "POST", body: { prefs: state.voicePrefs, voices: { _english: en } } });
      const matches = res.matches || [];
      const nv = { "_english": en };
      for (const m of matches) {
        if (m.voice_id) nv[m.speaker] = m.voice_id;
      }
        state.book.voices = nv;
        renderBookVoicesPanel();
        state.book = await api("/api/books/" + state.book.id);
        renderBookVoicesPanel();
      const total = res.total ?? matches.length;
      const assigned = res.assigned ?? matches.filter((m) => m.voice_id).length;
      if (assigned > 0) toast(`已自動匹配 ${assigned} / ${total} 個角色，未匹配者仍待設定`, "ok");
      else toast(`沒有可自動匹配的角色：${res.message || "請手動設定聲線"}`, "warning");
    } catch (e) {
      toast(`重置匹配失敗：${e.message}`, "err");
    } finally {
      btn.disabled = false;
      btn.textContent = "全部重置並重新 AI 匹配";
    }
  });
  $("#book-voices-save").addEventListener("click", async () => {
    const nv = { ...voices };
    panel.querySelectorAll("select[data-bspk]").forEach((sel) => { nv[sel.dataset.bspk] = sel.value; });
    nv["_english"] = nv["_english"] || "en-US-JennyNeural";
    await api(`/api/books/${state.book.id}/voices`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voices: nv, prefs: state.voicePrefs }),
    });
    state.book = await api("/api/books/" + state.book.id);
    state.voicePrefs = state.book.voicePrefs || state.voicePrefs;
    delete state.analysisCache; state.analysisCache = {};
    toast("全書聲線已儲存，重新生成音訊即套用", "ok");
  });
  panel.querySelectorAll("input[data-exclude]").forEach((cb) =>
    cb.addEventListener("change", () => {
      const v = cb.dataset.exclude;
      state.voicePrefs[v] = state.voicePrefs[v] || {};
      state.voicePrefs[v].exclude = cb.checked;
    }));
  panel.querySelectorAll("input[data-reuse]").forEach((cb) =>
    cb.addEventListener("change", () => {
      const v = cb.dataset.reuse;
      state.voicePrefs[v] = state.voicePrefs[v] || {};
      // 「僅一次」勾選時，後端 reuse 必須為 false。
      state.voicePrefs[v].reuse = !cb.checked;
    }));
}

/* ---------------- 播放器 ---------------- */
async function openPlayer(seq, autoplay = false) {
  try {
    state.book = await api("/api/books/" + state.book.id);
  } catch (e) {
    toast(`載入作品失敗：${e.message}`, "err");
    location.hash = "#/bookshelf";
    return;
  }
  const ch = state.book.chapters.find((c) => c.seq === seq);
  if (!ch) { toast("找不到章節", "err"); return; }
  const hasAudio = ch.audio === "ready";
  const hasAnalysis = ch.status === "analyzed" || ch.audio === "ready";
  if (!hasAudio && !hasAnalysis) { toast("請先分析章節", "err"); return; }
  if (!hasAudio && !isGuest() && state.book && isAdmin()) {
    // 已分析但無音檔：文字預覽模式（管理員）
  } else if (!hasAudio) {
    toast("請先為本章生成音訊", "err");
    return;
  }

  state.currentSeq = seq;
  state.currentChapterTitle = ch.title;
  state.textOnly = !hasAudio;
  showView("player");
  renderBreadcrumb();
  $("#pl-ch-num").textContent = chapterDisplayLabel(state.book, seq);
  $("#pl-title").textContent = ch.title;
  bindMediaSession();
  updateMediaSession();
  $("#pl-time").textContent = state.textOnly ? "文字預覽" : "";
  $("#btn-prev").hidden = !chaptersBefore(seq);
  $("#btn-next").hidden = !chaptersAfter(seq);
  setPlayGlyph(false);
  renderPlayerChapterList();
  hideChapterList();

  const data = await api(`/api/books/${state.book.id}/chapters/${seq}`);
  state.analysisCache[seq] = data.analysis || {};
  state.segments = data.analysis ? compatibilitySegments(data.analysis) : [];

  applyTiming((data.timing && data.timing.segments) ? data.timing.segments.map((s) => s.dur) : []);
  state.currentSeg = -1;

  renderTranscript();
  renderVoiceMapping();
  renderVocabSidebar();

  if (state.textOnly) {
    // 文字預覽模式：無音檔，隱藏音訊控制
    state.chapterAudio.pause();
    $("#btn-play").disabled = true;
    $("#btn-play").classList.add("textonly");
    $("#toggle-autonext").closest(".toggle-switch").hidden = true;
    $("#audio-wave").hidden = true;
    return;
  }
  $("#btn-play").disabled = false;
  $("#btn-play").classList.remove("textonly");
  $("#toggle-autonext").closest(".toggle-switch").hidden = false;
  $("#audio-wave").hidden = false;

  // 音訊 URL 加 ?v= 快取破壞：每次開啟都強制取最新 mp3，避免舊檔 + 新 timing 造成線性漂移
  state.chapterAudio.src = `/api/books/${state.book.id}/audio/${seq}?v=${Date.now()}`;
  state.chapterAudio.playbackRate = state.playbackRate;
  state.chapterAudio.load();
  // 記錄聆聽進度（進入本章即更新，播放中再隨時間精準更新）
  const resumeTo = state.pendingSeek != null ? state.pendingSeek : 0;
  state.pendingSeek = null;
  state._lastSaveTs = 0;
  if (resumeTo > 0) setProgress(state.book.id, seq, resumeTo);
  else if (!state.textOnly) setProgress(state.book.id, seq, 0);
  bindAudio();
  if (autoplay) {
    const go = () => {
      try {
        if (resumeTo > 0 && isFinite(state.chapterAudio.duration) &&
            resumeTo >= 0.5 && resumeTo < state.chapterAudio.duration - 0.3) {
          state.chapterAudio.currentTime = resumeTo;
        }
      } catch (e) {}
      state.chapterAudio.play().catch(() => {});
    };
    state.chapterAudio.oncanplay = () => { state.chapterAudio.oncanplay = null; go(); };
    setTimeout(go, 300);
  }
}

function applyTiming(durs) {
  state.cumStart = [];
  state.segmentDurations = [];
  let t = 0;
  for (const raw of durs) {
    const d = Math.max(0, Number(raw) || 0);
    state.cumStart.push(t);
    state.segmentDurations.push(d);
    t += d;
  }
  state.totalDur = t;
}

function timedSegmentAt(time) {
  let previous = -1;
  for (let i = 0; i < state.segmentDurations.length; i++) {
    const duration = state.segmentDurations[i];
    if (duration <= 0) continue;
    const start = state.cumStart[i];
    if (time < start) return previous;
    if (time < start + duration) return i;
    previous = i;
  }
  return previous;
}

function viewable(ch) { return ch.audio === "ready" || (isAdmin() && ch.status === "analyzed"); }
function chaptersBefore(seq) { return state.book.chapters.some((c) => viewable(c) && c.seq < seq); }
function chaptersAfter(seq) { return state.book.chapters.some((c) => viewable(c) && c.seq > seq); }

/* 播放器章節切換清單 */
function renderPlayerChapterList() {
  const list = $("#pl-chapter-list");
  const b = state.book;
  const chapters = b.chapters || [];
  list.innerHTML = chapters.map((c) => {
    const ready = c.audio === "ready";
    const canView = viewable(c);
    const active = c.seq === state.currentSeq;
    const badgeInfo = chapterBadge(c);
    const status = ready ? "可播放" : badgeInfo.label;
    const badge = active
      ? `<span class="pl-chip-cur">目前</span>`
      : ready ? `<span class="pl-chip">${status}</span>` : `<span class="pl-chip ${canView ? "" : "dim"}">${status}</span>`;
    return `
      <div class="pl-chip-row ${active ? "cur" : ""} ${canView ? "playable" : "locked"}" data-goto="${c.seq}">
        <span class="pl-chip-no">${chapterDisplayNumber(state.book, c.seq)}</span>
        <span class="pl-chip-title">${esc(c.title)}</span>
        ${badge}
      </div>`;
  }).join("");

  list.querySelectorAll("[data-goto]").forEach((row) =>
    row.addEventListener("click", () => {
      const seq = +row.dataset.goto;
      hideChapterList();
      if (seq !== state.currentSeq && row.classList.contains("playable")) openPlayer(seq);
    }));
}

function showChapterList() {
  const list = $("#pl-chapter-list");
  renderPlayerChapterList();
  list.hidden = false;
  const active = list.querySelector(".pl-chip-row.cur");
  if (active) active.scrollIntoView({ block: "nearest" });
}
function hideChapterList() { $("#pl-chapter-list").hidden = true; }
function toggleChapterList() { $("#pl-chapter-list").hidden ? showChapterList() : hideChapterList(); }
function nextSeq(seq) {
  const next = state.book.chapters.filter((c) => viewable(c) && c.seq > seq)
    .sort((a, b) => a.seq - b.seq);
  return next.length ? next[0].seq : null;
}
function prevChapter() {
  const list = state.book.chapters.filter((c) => viewable(c));
  const idx = list.findIndex((c) => c.seq === state.currentSeq);
  if (idx > 0) openPlayer(list[idx - 1].seq);
}
function nextChapter() {
  const list = state.book.chapters.filter((c) => viewable(c));
  const idx = list.findIndex((c) => c.seq === state.currentSeq);
  if (idx >= 0 && idx < list.length - 1) openPlayer(list[idx + 1].seq);
}

function renderTranscript() {
  const tl = $("#transcript");
  tl.innerHTML = state.segments.map((seg, i) => {
    if (seg.type === "vocab") return renderVocabCard(seg, i);
    const who = seg.speaker || "旁白";
    const color = colorOf(who);
    if (seg.type === "bilingual") {
      return `
      <div class="seg seg-bilingual" data-i="${i}" style="border-left-color:${color}">
        <div class="who" style="color:${color}">${esc(who)}</div>
        <div class="body">${esc(seg.zh || "")}</div>
        <div class="bili-en">${esc(seg.en || "")}</div>
      </div>`;
    }
    return `
    <div class="seg ${seg.type === "dialogue" ? "" : ""}" data-i="${i}" style="border-left-color:${color}">
      <div class="who" style="color:${color}">${esc(who)}</div>
      <div class="body">${esc(seg.text)}</div>
    </div>`;
  }).join("");
}

function renderVocabCard(seg, i) {
  const v = seg.vocab || {};
  const lines = (seg.utterances || []).map((u) => u.text).join("　");
  return `
  <div class="vocab-card" data-i="${i}">
    <div class="vocab-top">
      <span class="vocab-en">${esc(v.en || "")}</span>
      <span class="vocab-level">${esc(v.level || "A1")}</span>
    </div>
    <div class="vocab-spell">${esc(v.spelling || "")}</div>
    <div class="vocab-zh">${esc(v.zh || "")}</div>
    <div class="vocab-ex">${esc(v.example || "")}</div>
    <div class="vocab-lines">${esc(lines)}</div>
    <div class="vocab-actions"><button class="btn" data-replay="${i}">重聽教學</button></div>
  </div>`;
}

function renderVoiceMapping() {
  const speakers = [];
  const seen = new Set();
  state.segments.forEach((seg) => {
    const n = seg.speaker || "旁白";
    if (!seen.has(n)) { seen.add(n); speakers.push(n); }
  });
  const voices = state.book.voices || {};
  const el = $("#voice-mapping");
  if (!speakers.length) { el.innerHTML = `<div class="sidebar-empty">無資料</div>`; return; }
  const voicename = (id) => {
    const v = state.voices.find((x) => x.id === id);
    return v ? v.name : "未指定聲線";
  };
  el.innerHTML = speakers.map((s) => {
    const color = colorOf(s);
    const normalized = String(s || "").trim().toLowerCase();
    const unresolved = ["unknown", "speaker", "generic", "未解析語者", "待辨識語者"].includes(normalized);
    // 未解析語者一律沿用 canonical 旁白 mapping；不可採用歷史資料中殘留的
    // provider/Edge voice id，否則播放頁會把失效聲線誤顯示成未解析語者的聲線。
    const voiceId = (s === "旁白" || unresolved) ? (voices["旁白"] || "") : (voices[s] || "");
    return `<div class="vmap-row">
      <span class="sp-avatar" style="width:24px;height:24px;font-size:11px;background:${color}">${esc(s.slice(0, 1))}</span>
      <span class="vmap-name">${esc(s)}</span>
      <span class="vmap-voice">${esc(voicename(voiceId))}</span>
    </div>`;
  }).join("");
}

function renderVocabSidebar() {
  const vocs = vocabularyItemsFromAnalysis(state.analysisCache[state.currentSeq] || {segments: state.segments});
  const el = $("#vocab-list");
  if (!vocs.length) { el.innerHTML = `<div class="sidebar-empty">本章沒有單字</div>`; return; }
  el.innerHTML = vocs.map((v) => `
    <div class="vmini" data-vocab-en="${esc(v.en || "")}" data-vocab-zh="${esc(v.zh || "")}">
      <span class="en">${esc(v.en || "")}</span> <span style="color:var(--dim);font-size:12px">${esc(v.zh || "")}</span>
      <div class="ex">${esc(v.example || "")}</div>
    </div>`).join("");
  el.querySelectorAll("[data-vocab-en]").forEach((item) =>
    item.addEventListener("click", (e) => {
      e.stopPropagation();
      showVocabPopup(item.dataset.vocabEn, item.dataset.vocabZh || "", "");
    }));
}

function seekRel(dt) {
  if (state.textOnly) return;
  state.chapterAudio.currentTime = Math.max(0, state.chapterAudio.currentTime + dt);
}

/* ---------------- 外接裝置：螢幕常亮（Wake Lock）+ 系統媒體資訊（Media Session） ---------------- */
let _wakeLock = null;
async function requestWakeLock() {
  try {
    if (!state.wakeOn || !("wakeLock" in navigator) || _wakeLock) return;
    _wakeLock = await navigator.wakeLock.request("screen");
  } catch (e) { _wakeLock = null; }
}
function releaseWakeLock() {
  try { if (_wakeLock) { _wakeLock.release(); _wakeLock = null; } } catch (e) {}
}

let _msBound = false;
function bindMediaSession() {
  if (_msBound || !("mediaSession" in navigator)) return;
  _msBound = true;
  const setAct = (action, h) => { try { navigator.mediaSession.setActionHandler(action, h); } catch (e) {} };
  setAct("play", () => { if (state.chapterAudio.paused) state.chapterAudio.play(); });
  setAct("pause", () => { if (!state.chapterAudio.paused) state.chapterAudio.pause(); });
  setAct("previoustrack", () => prevChapter());
  setAct("nexttrack", () => nextChapter());
  setAct("seekto", (d) => { if (d.seekTime != null && !state.textOnly) state.chapterAudio.currentTime = d.seekTime; });
  setAct("seekbackward", () => seekRel(-10));
  setAct("seekforward", () => seekRel(10));
}
function updateMediaSession() {
  if (!("mediaSession" in navigator) || !state.book) return;
  const ch = state.book.chapters.find((x) => x.seq === state.currentSeq);
  const num = chapterDisplayLabel(state.book, state.currentSeq);
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: state.textOnly ? (state.currentChapterTitle || "") : `${num} ${state.currentChapterTitle || ""}`,
      artist: state.book.title,
      album: state.textOnly ? "小說朗讀（文字預覽）" : "小說朗讀",
    });
    navigator.mediaSession.playbackState = state.chapterAudio.paused ? "paused" : "playing";
  } catch (e) {}
}

function setPlayGlyph(playing) {
  $("#play-glyph").innerHTML = playing
    ? '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>'
    : '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
  $("#audio-wave").classList.toggle("playing", playing);
}

function bindAudio() {
  const a = state.chapterAudio;
  a.onloadedmetadata = () => {
    // 防舊檔 + 新 timing 混搭造成的線性漂移：音檔總長與 cross-ta 對不上時重新抓 timing
    if (state.cumStart.length && isFinite(a.duration) &&
        Math.abs(a.duration - state.totalDur) > 0.25) {
      api(`/api/books/${state.book.id}/chapters/${state.currentSeq}`)
        .then((data) => {
          const durs = (data.timing && data.timing.segments)
            ? data.timing.segments.map((s) => s.dur) : [];
          applyTiming(durs);
        }).catch(() => {});
    }
  };
  a.ontimeupdate = () => {
    if (!state.cumStart.length) return;
    const t = a.currentTime;
    const seg = timedSegmentAt(t);
    if (seg !== state.currentSeg) {
      state.currentSeg = seg;
      highlightSeg(seg);
      const s = state.segments[seg];
      if (s && s.type === "vocab") toast(`單字卡：${(s.vocab || {}).en || ""}`, "info");
    }
    $("#pl-time").textContent = fmtTime(t) + " / " + fmtTime(state.totalDur);
    // 聆聽進度定時記憶（節流，min 2 秒一次，避免頻繁寫 localStorage）
    if (state.book && Date.now() - state._lastSaveTs > 2000) {
      setProgress(state.book.id, state.currentSeq, a.currentTime);
      state._lastSaveTs = Date.now();
    }
  };
  a.onplay = () => { setPlayGlyph(true); requestWakeLock(); updateMediaSession(); };
  a.onpause = () => { setPlayGlyph(false); releaseWakeLock(); updateMediaSession(); if (state.book) setProgress(state.book.id, state.currentSeq, a.currentTime); };
  a.onended = () => {
    setPlayGlyph(false);
    releaseWakeLock();
    $("#pl-time").textContent = fmtTime(state.totalDur);
    if (state.autoNext) {
      const n = nextSeq(state.currentSeq);
      if (n != null) openPlayer(n, true);
    }
  };
}

function highlightSeg(i) {
  const tl = $("#transcript");
  tl.querySelectorAll(".seg.active, .vocab-card.active").forEach((el) => el.classList.remove("active"));
  if (i < 0) return;
  const el = tl.querySelector(`[data-i="${i}"]`);
  if (el) {
    el.classList.add("active");
    el.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

function seekTo(i) {
  if (state.textOnly) return;
  const start = state.cumStart[i] || 0;
  state.chapterAudio.currentTime = start;
  state.chapterAudio.play();
}

function togglePlay() {
  if (state.textOnly) return;
  const a = state.chapterAudio;
  if (a.paused) { a.play(); } else { a.pause(); }
}

function bindPlayer() {
  $("#btn-play").addEventListener("click", togglePlay);
  $("#btn-prev").addEventListener("click", prevChapter);
  $("#btn-next").addEventListener("click", nextChapter);
  $("#btn-back-chapters").addEventListener("click", () => {
    state.chapterAudio.pause();
    goChapters();
  });
  $("#toggle-autonext").addEventListener("change", (e) => { state.autoNext = e.target.checked; });
  $("#toggle-wake").addEventListener("change", (e) => {
    state.wakeOn = e.target.checked;
    releaseWakeLock();
    if (state.wakeOn && !state.chapterAudio.paused) requestWakeLock();
    try { localStorage.setItem("novel_keep_awake", state.wakeOn ? "1" : "0"); } catch (err) {}
  });
  $("#pl-speed").addEventListener("change", (e) => {
    state.playbackRate = parseFloat(e.target.value) || 1;
    state.chapterAudio.playbackRate = state.playbackRate;
    try { localStorage.setItem("novel_play_rate", String(state.playbackRate)); } catch (err) {}
  });
  $("#pl-chapter-btn").addEventListener("click", (e) => { e.stopPropagation(); toggleChapterList(); });
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#pl-chapter-list") && !e.target.closest("#pl-chapter-btn")) hideChapterList();
  });
  $("#transcript").addEventListener("click", (e) => {
    const replay = e.target.closest("[data-replay]");
    if (replay) { seekTo(+replay.dataset.replay); return; }
    const s = e.target.closest("[data-i]");
    if (s) seekTo(+s.dataset.i);
  });
}

/* ---------------- 快捷鍵 ---------------- */
function bindKeys() {
  document.addEventListener("keydown", (e) => {
    if (state.view !== "player") return;
    const tag = (e.target.tagName || "").toUpperCase();
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
    if (e.code === "Space") { e.preventDefault(); togglePlay(); }
    else if (e.key === "ArrowLeft") seekRel(-5);
    else if (e.key === "ArrowRight") seekRel(5);
    else if (e.key === "ArrowUp") prevChapter();
    else if (e.key === "ArrowDown") nextChapter();
  });
}

/* 單字發音 popup */
function bindVocabPopup() {
  $("#vocab-popup").querySelector(".vocab-popup-close").addEventListener("click", () => {
    $("#vocab-popup").hidden = true;
  });
  $("#vocab-popup").querySelector(".vocab-popup-play").addEventListener("click", () => {
    const en = $("#vocab-popup").querySelector(".vocab-popup-word").textContent;
    const voiceId = (state.book && state.book.voices && state.book.voices["_english"]) || "en-US-JennyNeural";
    const f5v = (state.voices || []).find((v) => v.id === voiceId && v.region === "F5");
    if (f5v && f5v.f5Broken) { toast("英語聲線為 F5 自訂聲線但缺參考音檔", "err"); return; }
    if (en) {
      state.previewAudio.pause();
      NovelApi.request("/api/preview-tts", {
        method: "POST", body: { voice: voiceId, text: en }, responseType: "blob",
      }).then((blob) => {
        setPreviewBlob(blob);
        state.previewAudio.play();
      }).catch(() => toast("發音播放失敗", "err"));
    }
  });
  document.addEventListener("click", (e) => {
    if ($("#vocab-popup").hidden) return;
    if (!e.target.closest("#vocab-popup") && !e.target.closest(".vocab-chip")) {
      $("#vocab-popup").hidden = true;
    }
  });
}

/* ---------------- 登入／註冊 ---------------- */
function isAdmin() { return !!(state.me && ["admin", "super_admin"].includes(state.me.role)); }
function canEditBook(b) {
  const m = state.me;
  if (!m) return false;
  if (["admin", "super_admin"].includes(m.role)) return true;
  return m.role === "author" && (!b || b.ownerId === m.id || b.ownerId === undefined);
}

function updateAuthUI() {
  const btn = $("#btn-login");
  const who = $("#auth-user");
  const profileButton = $("#btn-profile");
  if (state.authed && state.me) {
    btn.textContent = "登出";
    btn.title = "登出";
    const roleLabel = state.me.role === "super_admin" ? "Super Admin" : state.me.role === "admin" ? "管理員" : state.me.role === "reviewer" ? "Reviewer" : state.me.role === "author" ? "作者" : "讀者";
    who.hidden = false;
    if (profileButton) profileButton.hidden = false;
    who.textContent = `${state.me.username}（${roleLabel}）`;
  } else {
    btn.textContent = "登入";
    btn.title = "登入";
    who.hidden = true;
    who.textContent = "";
    if (profileButton) profileButton.hidden = true;
  }
  const googleButton = $("#btn-google-login");
  const googleNote = $("#google-unavailable-note");
  const googleAvailable = Boolean(state.authMethods?.google?.available);
  if (googleButton) googleButton.hidden = !googleAvailable;
  if (googleNote) googleNote.hidden = googleAvailable;
  document.body.classList.toggle("guest", !state.authed);
  document.body.classList.toggle("role-author", state.authed && state.me && state.me.role === "author");
  document.body.classList.toggle("role-reader", state.authed && state.me && state.me.role === "reader");
  const navMine = $("#nav-mine");
  const navAdmin = $("#nav-admin");
  const navApply = $("#nav-apply-author");
  const navRequests = $("#nav-requests");
  const navNotifications = $("#nav-notifications");
  const navWorkspace = $("#nav-workspace-group");
  if (navMine) navMine.hidden = !(state.authed && state.me && ["author", "admin", "super_admin"].includes(state.me.role));
  if (navRequests) navRequests.hidden = !(state.authed && state.me);
  if (navAdmin) navAdmin.hidden = !isAdmin();
  if (navAdmin) navAdmin.hidden = !(state.authed && state.me && ["reviewer", "admin", "super_admin"].includes(state.me.role));
  if (navApply) navApply.hidden = !(state.authed && state.me && state.me.role === "reader");
  if (navNotifications) navNotifications.hidden = !(state.authed && state.me);
  if (navWorkspace) {
    navWorkspace.hidden = !(state.authed && state.me);
    const workspaceLinks = [navApply, navMine, navRequests, navAdmin].filter((link) => link && !link.hidden);
    navWorkspace.open = Boolean(state.authed && state.me && workspaceLinks.length);
  }
  if (!state.authed) window.StoryLingoPublicNav?.close();
  window.StoryLingoPublicNav?.sync();
  document.querySelectorAll(".admin-tabs .chip").forEach((tab) => {
    tab.hidden = state.me?.role === "reviewer" && !["review", "generation"].includes(tab.dataset.atab);
  });
}

async function refreshVoiceCatalog() {
  if (!state.me || !["author", "admin", "super_admin"].includes(state.me.role)) {
    state.voices = [];
    return;
  }
  try { state.voices = (await api("/api/voices")).voices || []; } catch (_) { state.voices = []; }
}

function openLogin(force) {
  if (state.authed) return;
  resetAuthModal();
  openAppModal("#login-modal", { focus: force ? "#login-username" : "#login-password" });
}

async function submitAuth() {
  const username = $("#login-username").value.trim();
  const password = $("#login-password").value;
  const remember = $("#login-remember").checked;
  try {
    await api("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, remember }),
    });
    const me = await api("/api/auth/me");
    state.authed = !!(me && me.authed);
    state.me = (me && me.user) || null;
    state.authMethods = me?.authMethods || { google: { available: false } };
    updateMinePrincipal(state.me);
    await refreshVoiceCatalog();
    await loadAuthorProfileChoices();
    closeAppModal("#login-modal");
    $("#login-password").value = "";
    updateAuthUI();
    toast("登入成功", "ok");
    document.dispatchEvent(new CustomEvent("storylingo:auth-changed"));
    location.hash = "#/home";
    route();
  } catch (e) {
    toast(`登入失敗：${e.message}`, "err");
  }
}

function beginGoogleLogin() {
  if (!state.authMethods?.google?.available) {
    toast("Google 登入目前尚未開放，請使用帳號密碼。", "info");
    return;
  }
  if (!window.confirm("繼續使用 Google 登入即表示你已閱讀並同意服務條款、隱私權政策與年齡要求。")) return;
  const returnPath = location.pathname + "#/home";
  window.location.assign("/api/auth/oauth/google/start?return_path=" + encodeURIComponent(returnPath) +
    "&termsAccepted=true&privacyAccepted=true&ageConfirmed=true");
}

async function submitForgotPassword() {
  const email = $("#forgot-email")?.value.trim() || "";
  try {
    await api("/api/auth/forgot-password", { method: "POST", body: { email } });
    toast("如果帳號符合條件，系統會寄出重設說明", "ok");
  } catch (error) {
    toast("無法送出重設請求：" + error.message, "err");
  }
}

function resetAuthModal() {
  ["#login-username", "#login-password", "#login-remember", "#btn-forgot-password",
    "#btn-register-open", "#btn-login-submit"].forEach((selector) => {
    const el = $(selector);
    if (el) el.hidden = false;
  });
  ["#forgot-password-panel", "#reset-password-panel"].forEach((selector) => {
    const el = $(selector);
    if (el) el.hidden = true;
  });
  setResetPasswordFeedback("");
}

function showResetPasswordPanel() {
  ["#login-username", "#login-password", "#login-remember", "#btn-forgot-password",
    "#btn-register-open", "#btn-login-submit", "#forgot-password-panel"].forEach((selector) => {
    const el = $(selector);
    if (el) el.hidden = true;
  });
  $("#reset-password-panel").hidden = false;
  setResetPasswordFeedback("");
}

function setResetPasswordFeedback(message) {
  const feedback = $("#reset-password-feedback");
  if (!feedback) return;
  feedback.textContent = message || "";
  feedback.hidden = !message;
}

async function submitResetPassword() {
  const password = $("#reset-password-new")?.value || "";
  const confirm = $("#reset-password-confirm")?.value || "";
  if (password !== confirm) {
    setResetPasswordFeedback("兩次密碼不一致，請重新確認。");
    toast("兩次密碼不一致", "err");
    return;
  }
  setResetPasswordFeedback("");
  try {
    await api("/api/auth/reset-password", {
      method: "POST", body: { token: _resetPasswordToken, newPassword: password },
    });
    _resetPasswordToken = "";
    closeAppModal("#login-modal");
    history.replaceState(null, "", "#/home");
    resetAuthModal();
    toast("密碼已更新，請重新登入", "ok");
  } catch (error) {
    const detail = String(error?.data?.detail || error?.message || "");
    const passwordValidation = /^(密碼格式不正確|密碼至少 \d+ 個字元|密碼不可超過 \d+ 個字元)$/.test(detail);
    if (error?.status === 400) {
      setResetPasswordFeedback(passwordValidation ? detail : RESET_LINK_ERROR_MESSAGE);
      return;
    }
    toast("目前無法更新密碼，請稍後再試。", "err");
  }
}

async function submitRegister() {
  const username = $("#register-username").value.trim();
  const email = $("#register-email").value.trim();
  const password = $("#register-password").value;
  const confirm = $("#register-confirm").value;
  const termsAccepted = $("#register-terms")?.checked;
  const privacyAccepted = $("#register-privacy")?.checked;
  const ageConfirmed = $("#register-age")?.checked;
  if (password !== confirm) {
    toast("兩次密碼不一致", "err");
    $("#register-confirm").focus();
    return;
  }
  if (!termsAccepted || !privacyAccepted || !ageConfirmed) {
    toast("請先完成註冊政策確認", "err");
    return;
  }
  try {
    await api("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, email, password, termsAccepted, privacyAccepted, ageConfirmed }),
    });
    closeAppModal("#register-modal");
    $("#register-password").value = "";
    $("#register-confirm").value = "";
    toast("註冊成功，請到信箱完成驗證後再登入", "ok");
    openAppModal("#login-modal", { focus: "#login-username" });
  } catch (e) {
    toast(`註冊失敗：${e.message}`, "err");
  }
}

function bindAuth() {
  const btn = $("#btn-login");
  btn.addEventListener("click", () => {
    if (state.authed) {
      api("/api/auth/logout", { method: "POST" }).then(() => {
        state.authed = false;
        state.me = null;
        updateMinePrincipal(state.me);
        updateAuthUI();
        toast("已登出", "ok");
        document.dispatchEvent(new CustomEvent("storylingo:auth-changed"));
        location.hash = "#/home";
        route();
      }).catch((e) => { toast(`登出失敗：${e.message}`, "err"); });
      return;
    }
    openAppModal("#login-modal", { focus: "#login-username" });
  });
  $("#btn-register-open").addEventListener("click", () => {
    closeAppModal("#login-modal");
    openAppModal("#register-modal", { focus: "#register-username" });
  });
  $("#btn-login-cancel").addEventListener("click", () => { closeAppModal("#login-modal"); });
  $("#btn-login-submit").addEventListener("click", submitAuth);
  $("#btn-google-login")?.addEventListener("click", beginGoogleLogin);
  $("#btn-forgot-password")?.addEventListener("click", () => {
    const panel = $("#forgot-password-panel");
    if (panel) panel.hidden = !panel.hidden;
    if (panel && !panel.hidden) $("#forgot-email")?.focus();
  });
  $("#btn-forgot-submit")?.addEventListener("click", submitForgotPassword);
  $("#btn-reset-submit")?.addEventListener("click", submitResetPassword);
  $("#login-password").addEventListener("keydown", (e) => { if (e.key === "Enter") submitAuth(); });
  $("#login-username").addEventListener("keydown", (e) => { if (e.key === "Enter") submitAuth(); });
  $("#btn-register-cancel").addEventListener("click", () => {
    closeAppModal("#register-modal");
    openAppModal("#login-modal", { focus: "#login-username" });
  });
  $("#btn-register-submit").addEventListener("click", submitRegister);
  $("#register-confirm").addEventListener("keydown", (e) => { if (e.key === "Enter") submitRegister(); });
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#auth-control")) {
      closeAppModal("#login-modal");
      closeAppModal("#register-modal");
    }
  });
}

/* ---------------- 初始化 ---------------- */
async function init() {
  // 畫面回到前景且仍在播放時，重新取得螢幕常亮鎖（瀏覽器在切到背景時會自動釋放）
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !state.chapterAudio.paused) requestWakeLock();
  });
  // 先綁定登入控制，再等待初始 API，避免公開頁面在初始化期間點擊登入無反應。
  bindAuth();
  bindProfileSettings();
  try { state.voices = (await api("/api/voices")).voices; } catch (e) {}
  try {
    const r = parseFloat(localStorage.getItem("novel_play_rate"));
    if ([0.75, 0.85, 1, 1.25, 1.5, 2].includes(r)) state.playbackRate = r;
    const sp = $("#pl-speed");
    if (sp) sp.value = String(state.playbackRate);
    const w = localStorage.getItem("novel_keep_awake");
    if (w !== null) state.wakeOn = w === "1";
    const tw = $("#toggle-wake");
    if (tw) tw.checked = state.wakeOn;
  } catch (e) {}
  try {
    const me = await api("/api/auth/me");
    state.authed = !!(me && me.authed);
    state.me = (me && me.user) || null;
    state.authMethods = me?.authMethods || { google: { available: false } };
    updateMinePrincipal(state.me);
    await refreshVoiceCatalog();
    await loadAuthorProfileChoices();
  } catch (e) { state.authed = false; state.me = null; updateMinePrincipal(state.me); }
  updateAuthUI();
  bindShelfFilters();
  bindUpload();
  bindChapterBar();
  bindChapterModal();
  bindNewBook();
  bindApplyAuthor();
  bindAudioSetup();
  bindFollow();
  bindBookStatsModal();
  bindPlayer();
  bindKeys();
  bindVocabPopup();
  bindBookEdit();
  bindAdminTabs();
  bindOwnershipTransfer();
  await loadLiteraryCategories();
  window.addEventListener("hashchange", route);
  $("#btn-home").addEventListener("click", () => { location.hash = "#/home"; });
  const initialRoute = (location.hash || "#/home").replace(/^#\/?/, "").split("?", 1)[0].split("/")[0];
  if (!["home", "search", "category", "rankings", "audiobooks", "book", "read", "shelf", "notifications", "author", "privacy", "settings"].includes(initialRoute)) {
    await loadBooks();
  }
  route();
}

init();
