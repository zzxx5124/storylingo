"use strict";

/* 公開小說平台介面。既有 app.js 的朗讀管理流程保持相容，公開頁面由本模組接管。 */
const platformRoot = document.querySelector("#platform-root");
const platformState = {
  me: null, book: null, reader: null, readerController: null, progressTimer: null, heroTimer: null, heroBanners: [],
  routeGeneration: 0, queryGeneration: 0, authRequestGeneration: 0, principalGeneration: 0, principalKey: "",
};
const pEsc = (value) => window.NovelEscape.esc(value);

async function pApi(path, options = {}) {
  // 統一委派給 NovelApi（FE-005）：CSRF、credentials、JSON、錯誤映射集中處理
  return NovelApi.request(path, options);
}

function pPageHeader(title, description = "", action = "") {
  if (window.NovelUI?.pageHeader) {
    return window.NovelUI.pageHeader({ title, description, eyebrow: "NOVEL READER", action, className: "platform-page-header" });
  }
  return `<header class="platform-page-heading"><div><p class="platform-eyebrow">NOVEL READER</p><h1>${pEsc(title)}</h1>${description ? `<p>${pEsc(description)}</p>` : ""}</div>${action ? `<div>${action}</div>` : ""}</header>`;
}

function pState(type, title, message = "", action = "", className = "") {
  if (window.NovelUI?.statusState) {
    return window.NovelUI.statusState({ type, title, message, action, className: `platform-state ${className}`.trim() });
  }
  return `<section class="platform-state platform-${pEsc(type)} ${pEsc(className)}" role="status"><h2>${pEsc(title)}</h2>${message ? `<p>${pEsc(message)}</p>` : ""}${action ? `<div>${action}</div>` : ""}</section>`;
}

function pFilterForm({ id, label, controls, submitLabel = "搜尋" }) {
  return `<form class="platform-search-bar platform-advanced-search foundation-filter-bar ui-filter-bar" id="${pEsc(id)}" aria-label="${pEsc(label)}"><div class="foundation-filter-controls">${controls}</div><div class="foundation-filter-actions"><button class="platform-button platform-button-primary ui-button" type="submit">${pEsc(submitLabel)}</button></div></form>`;
}

function pCover(book, size = "card") {
  // 只在 payload 確實帶封面時請求圖片；無封面一律用文字佔位，不發 /cover 404 請求
  const url = book.cover || book.coverImage;
  const fallback = pEsc((book.title || "小").slice(0, 1));
  return url
    ? `<div class="platform-cover platform-cover-${size} platform-cover-frame" aria-label="${pEsc(book.title || "")} 封面"><img class="platform-cover-image" src="${pEsc(url)}" alt="${pEsc(book.title)} 封面" loading="lazy" onerror="this.onerror=null;this.hidden=true;this.nextElementSibling.hidden=false"><span class="platform-cover-fallback" hidden aria-label="無封面">${fallback}</span></div>`
    : `<div class="platform-cover platform-cover-${size} cover-empty" aria-label="無封面">${fallback}</div>`;
}

function publicAuthor(book) {
  return book?.author || null;
}

function authorLabel(book) {
  const author = publicAuthor(book);
  return author?.displayName || book.owner || book.author || "匿名作者";
}

function authorMarkup(book, className = "platform-card-author") {
  const author = publicAuthor(book);
  const label = pEsc(authorLabel(book));
  const status = author?.status === "suspended" ? '<span class="platform-author-status">暫停更新</span>'
    : author?.status === "tombstone" ? '<span class="platform-author-status">帳號已結束</span>' : "";
  if (author?.link && author.slug) {
    return '<a class="' + className + ' platform-author-link" data-author-link href="#/author/'
      + encodeURIComponent(author.slug) + '">' + label + "</a>" + status;
  }
  return '<p class="' + className + '">' + label + "</p>" + status;
}

function pCard(book) {
  const id = book.id || book.bid;
  const genre = book.categoryName || book.litCount || (book.categories || []).join("、") || "小說";
  const languageLabels = { zh: "中文", vocab: "外語學習", bilingual: "雙語", en: "英文", other: "其它" };
  const language = languageLabels[book.category] || "語言閱讀";
  const tags = Array.isArray(book.tags) ? book.tags.slice(0, 2) : [];
  const href = `#/book/${encodeURIComponent(String(id))}`;
  return `<article class="platform-card" aria-label="閱讀 ${pEsc(book.title || "未命名")}">
    <a class="platform-card-main" href="${pEsc(href)}" aria-label="閱讀 ${pEsc(book.title || "未命名")}">${pCover(book)}<div class="platform-card-body"><h3>${pEsc(book.title || "未命名")}</h3><p class="platform-card-meta"><span>${pEsc(language)}</span><span>${pEsc(genre)}</span><span>${pEsc(book.serial || "連載")}</span></p></div></a>
    <div class="platform-card-footer">${authorMarkup(book)}<p class="platform-card-summary">${pEsc(book.synopsis || "開始閱讀這部作品")}</p>${tags.length ? `<div class="platform-card-tags">${tags.map((tag) => `<span>${pEsc(tag)}</span>`).join("")}</div>` : ""}</div>
  </article>`;
}

function pAudiobookCard(book) {
  const id = book.id || book.bid;
  const availability = book.availability === "partial" ? "部分章節可聆聽" : "可聆聽";
  const counts = Number.isFinite(Number(book.playableChapterCount)) && Number.isFinite(Number(book.publicChapterCount))
    ? `<span class="platform-audiobook-count">${pEsc(book.playableChapterCount)} / ${pEsc(book.publicChapterCount)} 章</span>` : "";
  const listenRoute = window.StoryLingoAudiobooks.safeListenRoute(book);
  const listen = listenRoute
    ? `<a class="platform-button platform-button-small platform-card-listen" data-listen-route href="${pEsc(listenRoute)}">開始聆聽</a>` : "";
  const href = `#/book/${encodeURIComponent(String(id))}`;
  return `<article class="platform-card audiobook-card" aria-label="開啟有聲書 ${pEsc(book.title || "未命名")}">
    <a class="platform-card-main" href="${pEsc(href)}" aria-label="開啟有聲書 ${pEsc(book.title || "未命名")}">${pCover(book)}<div class="platform-card-body"><h3>${pEsc(book.title || "未命名")}</h3><p class="platform-card-meta"><span>${pEsc(book.languageType === "zh" ? "中文" : book.languageType === "vocab" ? "外語學習" : book.languageType === "bilingual" ? "雙語" : book.languageType === "en" ? "英文" : "其它")}</span><span>${pEsc(book.categoryName || "小說")}</span></p><p class="platform-audiobook-availability" aria-label="有聲書狀態：${pEsc(availability)}"><strong>${pEsc(availability)}</strong>${counts}</p></div></a>
    <div class="platform-card-footer">${authorMarkup(book)}<p class="platform-card-summary">${pEsc(book.synopsis || "開啟作品，從可播放章節開始聆聽。")}</p>${listen}</div>
  </article>`;
}

function supportsLearning(book) {
  // category 是作品唯一的語言型別；categories 是內容分類，不得覆蓋作者選定的型別。
  return ["vocab", "bilingual"].includes(String(book?.category || "").toLowerCase());
}

function hasAnalyzedChapter(book) {
  // 公開書籍 payload 的 chapter.status 是既有分析 ready 投影；未分析作品沒有學習資料可供入口使用。
  return Array.isArray(book?.chapters) && book.chapters.some((chapter) => chapter?.status === "analyzed");
}

function bookHasPrologue(book) {
  // 舊書沒有設定時維持既有 seq=0 顯示為序章。
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

function readerModeLabel(mode) {
  if (mode === "listen") return "聽書跟讀";
  if (mode === "learning") return "閱讀＋學習";
  return "純文字閱讀";
}

function normalizeDetailProgress(raw) {
  if (!raw || typeof raw !== "object") return null;
  const seqValue = raw.chapter_seq ?? raw.chapterSeq ?? raw.seq;
  const seq = Number(seqValue);
  if (!Number.isInteger(seq) || seq < 0) return null;
  const percent = Math.max(0, Math.min(100, Number(raw.percent) || 0));
  const position = Math.max(0, Number(raw.position) || 0);
  const audioPosition = Math.max(0, Number(raw.audio_position_seconds ?? raw.audioPositionSeconds) || 0);
  const rawMode = String(raw.last_mode ?? raw.lastMode ?? raw.mode ?? "read").toLowerCase();
  const mode = rawMode === "listen" ? "listen" : rawMode === "learning" ? "learning" : "reading";
  return { seq, percent, position, audioPosition, mode };
}

async function loadDetailProgress(bid, chapters) {
  let raw = null;
  try {
    if (platformState.me) {
      const data = await pApi("/api/me/progress");
      raw = (data.items || []).find((item) => String(item.book_id ?? item.bookId ?? item.bid) === String(bid));
    } else {
      raw = JSON.parse(localStorage.getItem(window.NovelReader.storageKey(bid)) || "null");
    }
  } catch (_) {
    return null;
  }
  const progress = normalizeDetailProgress(raw);
  if (!progress) return null;
  const chapter = chapters.find((item) => Number(item.seq) === progress.seq);
  return chapter ? { ...progress, chapter } : null;
}

function pSection(title, items, action = "") {
  const content = items && items.length
    ? `<div class="platform-card-grid">${items.map(pCard).join("")}</div>`
    : pState("empty", "目前沒有可顯示的作品", "可以先探索其他公開內容。", action ? `<a class="platform-button" href="${pEsc(action)}">查看全部</a>` : `<a class="platform-button" href="#/search">開始探索</a>`, "platform-empty");
  return `<section class="platform-section"><div class="platform-section-head"><div><p class="platform-eyebrow">DISCOVER</p><h2>${pEsc(title)}</h2></div>${action ? `<a href="${pEsc(action)}">查看全部</a>` : ""}</div>${content}</section>`;
}

function pPagedSection(title, payload, hrefForPage, label) {
  const meta = window.StoryLingoPagination.normalize(payload);
  return pSection(title, meta.items) + window.StoryLingoPagination.render(meta, { label, hrefForPage });
}

function pLayout(content, title = "語閱 StoryLingo", { showHeader = true, pageClass = "" } = {}) {
  const classes = ["platform-page", pageClass].filter(Boolean).join(" ");
  platformRoot.innerHTML = `<div class="${pEsc(classes)}">${showHeader ? pPageHeader(title) : ""}${content}</div>`;
  bindPublicLinks(platformRoot);
}

function bindPublicLinks(root = platformRoot) {
  root?.querySelectorAll("[data-book]").forEach((el) => {
    if (el.dataset.bookBound) return;
    el.dataset.bookBound = "1";
    const open = () => { location.hash = `#/book/${encodeURIComponent(el.dataset.book)}`; };
    el.addEventListener("click", open);
    el.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } });
  });
  root?.querySelectorAll("[data-author-link]").forEach((el) => {
    if (el.dataset.authorBound) return;
    el.dataset.authorBound = "1";
    el.addEventListener("click", (event) => event.stopPropagation());
    el.addEventListener("keydown", (event) => event.stopPropagation());
  });
  root?.querySelectorAll("[data-listen-route]").forEach((el) => {
    if (el.dataset.listenBound) return;
    el.dataset.listenBound = "1";
    el.addEventListener("click", (event) => event.stopPropagation());
    el.addEventListener("keydown", (event) => event.stopPropagation());
  });
}

function pShow() {
  const platform = document.querySelector("#view-platform");
  if (platform) platform.hidden = false;
  ["view-bookshelf", "view-chapters", "view-player", "view-mine", "view-admin"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.hidden = true;
  });
  document.body.classList.add("platform-mode");
  window.StoryLingoBootstrap?.viewVisible("platform");
}

function pHide() {
  const platform = document.querySelector("#view-platform");
  if (platform) platform.hidden = true;
  document.body.classList.remove("platform-mode");
  clearInterval(platformState.heroTimer);
  clearTimeout(platformState.progressTimer);
}

async function loadMe() {
  const requestGeneration = ++platformState.authRequestGeneration;
  let nextUser = null;
  try {
    const result = await pApi("/api/auth/me");
    nextUser = result.authed ? result.user : null;
  } catch (_) {
    nextUser = null;
  }
  if (requestGeneration !== platformState.authRequestGeneration) return;
  const nextKey = nextUser ? JSON.stringify([nextUser.id, nextUser.role, nextUser.accountStatus || nextUser.account_status, nextUser.sessionVersion || nextUser.session_version]) : "guest";
  if (nextKey !== platformState.principalKey) {
    platformState.principalGeneration += 1;
    platformState.principalKey = nextKey;
  }
  platformState.me = nextUser;
  const user = document.querySelector("#auth-user");
  if (user) user.textContent = platformState.me?.username || "";
}

function loginPrompt() {
  NovelToast.toast("請先登入，才能收藏或追蹤作品。", "info");
  const button = document.querySelector("#btn-login");
  // 避免原始收藏／追蹤 click 冒泡到全域 modal 關閉器後立刻把登入窗關掉。
  if (button) setTimeout(() => button.click(), 0);
}

function hero(banners) {
  platformState.heroBanners = banners || [];
  if (!banners || !banners.length) {
    return `<div class="platform-hero platform-hero-fallback" aria-labelledby="platform-hero-title"><div class="platform-hero-copy"><p class="platform-eyebrow">READ AT YOUR PACE</p><h2 id="platform-hero-title"><span class="hero-brand-cn">語閱</span><span class="hero-brand-en">StoryLingo</span></h2><p>在故事裡閱讀，也在閱讀裡學習語言。免費閱讀公開小說，想純閱讀或搭配語音學習都可以。</p><a class="platform-button platform-button-primary" href="#/search">開始探索</a></div><div class="hero-mark" aria-hidden="true">閱</div></div>`;
  }
  const first = banners[0];
  return `<div class="platform-hero" data-banner-id="${first.id}" style="--hero-image:url('${pEsc(first.imageDesktop || first.imageMobile)}')"><button class="platform-hero-arrow prev" data-hero-step="-1" aria-label="上一張輪播">‹</button><div class="platform-hero-copy"><p class="platform-eyebrow">EDITOR'S PICK</p><h2 data-hero-title>${pEsc(first.title)}</h2><p data-hero-subtitle>${pEsc(first.subtitle || "今天開始閱讀一個新的世界")}</p><a class="platform-button platform-button-primary" data-hero-link href="${first.linkType === "book" ? `#/book/${encodeURIComponent(first.linkValue)}` : "#/search"}">立即閱讀</a></div><button class="platform-hero-arrow next" data-hero-step="1" aria-label="下一張輪播">›</button><div class="platform-hero-dots">${banners.slice(0, 5).map((_, i) => `<button class="${i === 0 ? "active" : ""}" data-hero-index="${i}" aria-label="第 ${i + 1} 張輪播"></button>`).join("")}</div></div>`;
}

function bindHero() {
  clearInterval(platformState.heroTimer);
  if (platformState.heroBanners.length < 1 || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const heroEl = document.querySelector(".platform-hero[data-banner-id]");
  if (!heroEl) return;
  let index = 0;
  const paint = (next) => {
    index = (next + platformState.heroBanners.length) % platformState.heroBanners.length;
    const banner = platformState.heroBanners[index];
    heroEl.dataset.bannerId = banner.id;
    heroEl.style.setProperty("--hero-image", `url("${banner.imageDesktop || banner.imageMobile || ""}")`);
    heroEl.querySelector("[data-hero-title]").textContent = banner.title || "推薦作品";
    heroEl.querySelector("[data-hero-subtitle]").textContent = banner.subtitle || "今天開始閱讀一個新的世界";
    heroEl.querySelector("[data-hero-link]").href = banner.linkType === "book" ? `#/book/${encodeURIComponent(banner.linkValue)}` : "#/search";
    heroEl.querySelectorAll("[data-hero-index]").forEach((dot) => dot.classList.toggle("active", Number(dot.dataset.heroIndex) === index));
  };
  heroEl.querySelectorAll("[data-hero-step]").forEach((button) => button.addEventListener("click", () => paint(index + Number(button.dataset.heroStep))));
  heroEl.querySelectorAll("[data-hero-index]").forEach((button) => button.addEventListener("click", () => paint(Number(button.dataset.heroIndex))));
  heroEl.addEventListener("mouseenter", () => clearInterval(platformState.heroTimer));
  heroEl.addEventListener("mouseleave", () => { platformState.heroTimer = setInterval(() => paint(index + 1), 5000); });
  platformState.heroTimer = setInterval(() => paint(index + 1), 5000);
}

function pUniqueBooks(items, seen) {
  return (items || []).filter((book) => {
    const id = book?.id ?? book?.bid;
    if (id == null || seen.has(String(id))) return false;
    seen.add(String(id));
    return true;
  });
}

function pHomeSection(title, items, seen, action = "") {
  const unique = pUniqueBooks(items, seen);
  return unique.length ? pSection(title, unique, action) : "";
}

function pHomeCard(book) {
  const href = `#/book/${encodeURIComponent(String(book.id ?? book.bid))}`;
  return `<article class="platform-card home-story"><a class="platform-card-main" aria-label="閱讀 ${pEsc(book.title || "未命名")}" href="${pEsc(href)}">${pCover(book)}<div class="platform-card-body"><h3>${pEsc(book.title || "未命名")}</h3><p class="platform-card-meta">${pEsc(book.serial || "連載")}</p><p class="platform-card-summary">${pEsc(book.synopsis || "開啟作品，查看簡介與章節目錄。")}</p><span class="home-story-cta">查看作品 →</span></div></a><div class="platform-card-footer">${authorMarkup(book)}</div></article>`;
}

async function renderHome(routeToken = platformState.routeGeneration) {
  pShow();
  clearInterval(platformState.heroTimer);
  const requestGeneration = ++platformState.queryGeneration;
  const principal = platformState.principalGeneration;
  const current = () => routeToken === platformState.routeGeneration && requestGeneration === platformState.queryGeneration && principal === platformState.principalGeneration;
  const layout = (content) => pLayout(content, "首頁", { showHeader: false, pageClass: "platform-home-page" });
  const intro = (returning = false) => `<header class="home-intro"><p class="platform-eyebrow">語閱 StoryLingo · 免費小說閱讀</p><h1>${returning ? "接著讀你的故事" : "找一本故事，開始閱讀"}</h1>${returning ? "" : `<p>先享受故事，再隨心切換聽書；有學習內容的作品，也能邊讀邊學語言。</p>`}</header>`;
  layout(intro() + pState("loading", "正在載入作品", "即將為你整理可閱讀的故事。", "", "platform-loading"));
  try {
    const data = await pApi("/api/home");
    if (!current()) return;
    const recent = platformState.me ? pUniqueBooks(data.continueReading, new Set()).slice(0, 3) : [];
    const resume = recent[0];
    const progress = normalizeDetailProgress(resume?.readingProgress);
    const bookLink = (book) => `#/book/${encodeURIComponent(String(book.id ?? book.bid))}`;
    const resumeHref = progress ? `#/read/${encodeURIComponent(String(resume.id ?? resume.bid))}/${progress.seq}?mode=${progress.mode}` : resume ? bookLink(resume) : "";
    const resumeHtml = resume ? `<section class="home-resume" aria-labelledby="home-resume-title">${pCover(resume)}<div><p class="platform-eyebrow">最近閱讀</p><h2 id="home-resume-title">${pEsc(resume.title)}</h2><p>${progress ? `${pEsc(chapterDisplayLabel(resume, progress.seq))}〈${pEsc(resume.readingProgress.chapterTitle || "未命名章節")}〉 · ${pEsc(readerModeLabel(progress.mode))} · ${Math.round(progress.percent)}%` : "前往作品目錄，選擇下一個章節。"}</p><div class="home-resume-actions"><a class="platform-button platform-button-primary" href="${pEsc(resumeHref)}">${progress ? "繼續閱讀" : "返回作品"}</a><a href="#/shelf">我的書架與紀錄</a></div></div>${recent.length > 1 ? `<div class="home-recent"><span>也在閱讀</span>${recent.slice(1).map((book) => `<a href="${pEsc(bookLink(book))}">${pEsc(book.title)}</a>`).join("")}</div>` : ""}</section>` : "";
    const rankings = (data.rankings || []).map((item) => ({ id: item.bid, title: item.title, synopsis: item.synopsis, cover: item.cover_path, serial: item.serial, chars: item.chars, owner: item.owner, author: item.author, category: item.category, categoryName: item.category_name }));
    const feeds = {
      latest: { label: "最新更新", items: data.latest || [], href: "#/search?sort=updated" },
      popular: { label: "熱門作品", items: rankings, href: "#/rankings" },
      completed: { label: "完結推薦", items: data.completed || [], href: "#/search?serial=完結" },
    };
    const requested = queryParams(location.hash).get("collection");
    const selected = Object.hasOwn(feeds, requested) ? requested : Object.keys(feeds).find((key) => feeds[key].items.length) || "latest";
    const feed = feeds[selected];
    const books = pUniqueBooks(feed.items, new Set()).slice(0, 6);
    const hasBooks = Object.values(feeds).some((item) => item.items.length);
    const categories = (data.categories || []).slice(0, 6);
    const categoryHtml = categories.map((category) => `<a href="#/category/${encodeURIComponent(category.id)}">${pEsc(category.name)}</a>`).join("");
    const banners = (data.banners || []).slice(0, 5);
    layout(`${intro(!!resume)}${resumeHtml}<div class="home-content"><section class="home-discovery" aria-labelledby="home-discovery-title"><div class="home-section-heading"><h2 id="home-discovery-title">${resume ? "再找一本好故事" : "從這裡挑一本"}</h2><a href="#/search">探索全部作品 →</a></div>
      ${hasBooks ? `<nav class="home-collections" aria-label="選擇作品清單">${Object.entries(feeds).map(([key, item]) => `<a href="#/home?collection=${key}" ${key === selected ? 'aria-current="page"' : ""}>${item.label}</a>`).join("")}</nav>` : ""}
      ${books.length ? `<div class="platform-card-grid home-book-grid">${books.map(pHomeCard).join("")}</div><a class="home-more" href="${feed.href}">查看全部${feed.label} →</a>` : pState("empty", hasBooks ? `目前沒有${feed.label}的作品` : "故事正在準備中", hasBooks ? "試試其他清單，或搜尋你感興趣的作品。" : "目前還沒有公開作品。你可以先了解閱讀方式，稍後再回來找新故事。", hasBooks ? `<a href="#/home">看看其他作品</a>` : `<button class="platform-button" id="home-refresh" type="button">重新查看作品</button>`, "home-empty")}
      </section><aside class="home-find" aria-label="尋找其他故事"><h2>想找什麼故事？</h2><form class="platform-search-bar" id="platform-home-search"><label for="home-search-input">搜尋小說、作者或標籤</label><input id="home-search-input" name="q" type="search" placeholder="書名、作者、關鍵字" autocomplete="off"><button class="platform-button" type="submit">搜尋</button></form>
      <div class="home-genres"><h3>探索分類</h3><div class="home-category-links">${categoryHtml}</div><a href="#/search">全部分類 →</a></div>
      <div class="home-reading-guide"><h3>讓故事陪你讀，也陪你聽</h3><p>公開小說可直接閱讀，不必先登入。選擇有聲書即可聽書跟讀；雙語與單字工具會在支援的閱讀頁出現。</p><a href="#/audiobooks">瀏覽有聲書 →</a></div>
      ${banners.length ? `<div class="home-editorial"><h3>站內精選</h3>${banners.map((banner) => `<a href="${banner.linkType === "book" ? `#/book/${encodeURIComponent(banner.linkValue)}` : "#/search"}">${pEsc(banner.title)}</a>`).join("")}</div>` : ""}
      </aside></div>${platformState.me && ["author", "admin", "super_admin"].includes(platformState.me.role) ? `<p class="home-author-entry">準備更新自己的故事？ <a href="#/mine">前往我的作品 →</a></p>` : ""}`);
    document.querySelector("#home-refresh")?.addEventListener("click", () => renderHome(routeToken));
    const searchForm = document.querySelector("#platform-home-search");
    searchForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      const q = new FormData(searchForm).get("q");
      location.hash = `#/search?q=${encodeURIComponent(q)}`;
    });
  } catch (_) {
    if (!current()) return;
    layout(intro() + pState("error", "首頁目前無法載入", "請再試一次，或先前往搜尋。", `<button class="platform-button" id="home-retry" type="button">重新載入</button> <a class="platform-button" href="#/search">搜尋作品</a>`, "platform-error"));
    document.querySelector("#home-retry")?.addEventListener("click", () => renderHome(routeToken));
  }
}

function queryParams(hash) {
  const query = hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : "";
  return new URLSearchParams(query);
}

async function renderSearch(hash, categoryId = null, routeToken = platformState.routeGeneration) {
  pShow();
  const params = queryParams(hash);
  const q = params.get("q") || "";
  const page = Math.max(1, Number(params.get("page") || 1) || 1);
  const language = params.get("language") || "";
  const sort = ["relevance", "updated", "created", "chars"].includes(params.get("sort")) ? params.get("sort") : "relevance";
  const serial = params.get("serial") || "";
  const hasAudio = params.get("has_audio") === "1" || params.get("has_audio") === "true";
  let pageTitle = "搜尋小說";
  if (categoryId) {
    try {
      const cats = (await pApi("/api/genres")).items || [];
      const cat = cats.find((x) => String(x.id) === String(categoryId));
      if (cat) pageTitle = `分類：${cat.name}`;
    } catch (_) { /* 名稱查不到仍可搜尋 */ }
  }
  if (routeToken !== platformState.routeGeneration) return;
  pLayout(`${pFilterForm({
    id: "platform-search-form",
    label: "搜尋小說",
    controls: `<label class="field ui-field"><span>搜尋小說</span><input id="platform-search-input" class="field-input" name="q" value="${pEsc(q)}" type="search" placeholder="書名、作者、標籤" autofocus></label><label class="field ui-field"><span>語言</span><select class="field-input" name="language"><option value="">全部語言</option><option value="zh">中文閱讀</option><option value="vocab">外語學習</option><option value="bilingual">雙語閱讀</option><option value="en">英文閱讀</option><option value="other">其它</option></select></label><label class="field ui-field"><span>排序</span><select class="field-input" name="sort"><option value="relevance">相關度</option><option value="updated">最近更新</option><option value="created">最新上架</option><option value="chars">篇幅較長</option></select></label><label class="field ui-field"><span>連載狀態</span><select class="field-input" name="serial"><option value="">全部狀態</option><option value="連載">連載中</option><option value="完結">已完結</option></select></label><label class="search-audio-check"><input type="checkbox" name="has_audio" value="1"> 有聲內容</label>`,
  })}<div id="platform-search-result">${pState("loading", "搜尋中", "正在整理公開作品。", "", "platform-result-loading")}</div>`, pageTitle);
  const form = document.querySelector("#platform-search-form");
  if (form) {
    form.elements.language.value = language;
    form.elements.sort.value = sort;
    form.elements.serial.value = serial;
    form.elements.has_audio.checked = hasAudio;
    form.addEventListener("submit", (event) => { event.preventDefault(); const data = new FormData(form); const next = new URLSearchParams(); for (const [key, value] of data.entries()) if (value) next.set(key, value); next.set("page", "1"); location.hash = `#/${categoryId ? `category/${encodeURIComponent(categoryId)}` : "search"}?${next}`; });
  }
  const requestGeneration = ++platformState.queryGeneration;
  const current = () => routeToken === platformState.routeGeneration && requestGeneration === platformState.queryGeneration;
  try {
    const request = new URLSearchParams({ q, page: String(page), page_size: "20", sort });
    if (language) request.set("language", language);
    if (hasAudio) request.set("has_audio", "true");
    if (categoryId) request.set("category_id", categoryId);
    if (serial) request.set("serial", serial);
    const result = await pApi(`/api/search?${request}`);
    if (!current()) return;
    const meta = window.StoryLingoPagination.normalize(result);
    const routeForPage = (value) => {
      const next = new URLSearchParams(params);
      next.set("page", String(value));
      return `#/${categoryId ? `category/${encodeURIComponent(categoryId)}` : "search"}?${next}`;
    };
    const target = document.querySelector("#platform-search-result");
    target.innerHTML = `<div class="platform-result-head"><strong>${meta.total} 個結果</strong><span>${q ? `關鍵字：${pEsc(q)}` : "全部公開作品"}</span></div>${meta.items.length ? `<div class="platform-card-grid">${meta.items.map(pCard).join("")}</div>` : pState("empty", "找不到符合的作品", "請嘗試其他關鍵字或清除篩選。", `<a class="platform-button" href="#/search">清除篩選</a>`, "platform-empty")}${window.StoryLingoPagination.render(meta, { label: "搜尋結果分頁", hrefForPage: routeForPage })}`;
    bindPublicLinks(target);
  } catch (error) {
    if (!current()) return;
    const target = document.querySelector("#platform-search-result");
    if (target) {
      target.innerHTML = pState("error", "搜尋目前無法完成", "請稍後再試。", `<button class="platform-button" id="search-retry" type="button">重新載入</button>`, "platform-error");
      target.querySelector("#search-retry")?.addEventListener("click", () => {
        if (current()) renderSearch(hash, categoryId, routeToken);
      });
    }
  }
}

function audiobookQueryString(query) {
  return window.StoryLingoAudiobooks.toQuery(query);
}

function audiobookFilters(query, categories) {
  const languageLabels = { zh: "中文閱讀", vocab: "外語學習", bilingual: "雙語閱讀", en: "英文閱讀", other: "其它" };
  const sortLabels = { newest: "最新上架", updated: "最近更新", title: "依書名" };
  const categoryOptions = (categories || []).map((category) => `<option value="${pEsc(category.id)}"${String(category.id) === String(query.category_id) ? " selected" : ""}>${pEsc(category.name)}</option>`).join("");
  const languageOptions = Object.entries(languageLabels).map(([value, label]) => `<option value="${value}"${value === query.language ? " selected" : ""}>${label}</option>`).join("");
  const sortOptions = Object.entries(sortLabels).map(([value, label]) => `<option value="${value}"${value === query.sort ? " selected" : ""}>${label}</option>`).join("");
  return pFilterForm({
    id: "platform-audiobook-form",
    label: "搜尋有聲書",
    submitLabel: "搜尋",
    controls: `<label class="field ui-field"><span>搜尋有聲書</span><input id="platform-audiobook-search-input" class="field-input" name="q" value="${pEsc(query.q)}" type="search" placeholder="搜尋書名、作者或標籤"></label><label class="field ui-field"><span>作品分類</span><select class="field-input" name="category_id"><option value="">全部分類</option>${categoryOptions}</select></label><label class="field ui-field"><span>語言</span><select class="field-input" name="language"><option value="">全部語言</option>${languageOptions}</select></label><label class="field ui-field"><span>排序</span><select class="field-input" name="sort">${sortOptions}</select></label>`,
  });
}

async function renderAudiobooks(hash, routeToken = platformState.routeGeneration) {
  pShow();
  const query = window.StoryLingoAudiobooks.normalizeQuery(queryParams(hash));
  const requestGeneration = ++platformState.queryGeneration;
  const current = () => routeToken === platformState.routeGeneration && requestGeneration === platformState.queryGeneration;
  pLayout(`${audiobookFilters(query, [])}<div id="platform-audiobook-result">${pState("loading", "正在整理可聆聽作品", "請稍候。", "", "platform-result-loading")}</div>`, "有聲書");
  const bindFilters = () => {
    const form = document.querySelector("#platform-audiobook-form");
    form?.addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const next = {
        q: data.get("q") || "",
        category_id: data.get("category_id") || "",
        language: data.get("language") || "",
        sort: data.get("sort") || "newest",
        page: 1,
      };
      const nextQuery = audiobookQueryString(next);
      location.hash = nextQuery ? `#/audiobooks?${nextQuery}` : "#/audiobooks";
    });
  };
  bindFilters();
  try {
    const request = new URLSearchParams({ page: String(query.page), page_size: "20", sort: query.sort });
    if (query.q) request.set("q", query.q);
    if (query.category_id) request.set("category_id", query.category_id);
    if (query.language) request.set("language", query.language);
    const [result, genreData] = await Promise.all([pApi(`/api/audiobooks?${request}`), pApi("/api/genres")]);
    if (!current()) return;
    const categories = Array.isArray(genreData?.items) ? genreData.items : [];
    const form = document.querySelector("#platform-audiobook-form");
    if (form) {
      const replacement = document.createRange().createContextualFragment(audiobookFilters(query, categories));
      form.replaceWith(replacement.firstElementChild);
      bindFilters();
    }
    const meta = window.StoryLingoPagination.normalize(result);
    const hasFilters = Boolean(query.q || query.category_id || query.language);
    const routeForPage = (value) => {
      const next = { ...query, page: value };
      const nextQuery = audiobookQueryString(next);
      return nextQuery ? `#/audiobooks?${nextQuery}` : "#/audiobooks";
    };
    let body;
    if (meta.items.length) {
      body = `<div class="platform-card-grid audiobook-card-grid">${meta.items.map(pAudiobookCard).join("")}</div>`;
    } else if (meta.page > meta.total_pages && meta.total_pages > 0) {
      body = pState("empty", "這一頁目前沒有結果", "可以回到最後一頁繼續瀏覽。", `<a class="platform-button" href="${pEsc(routeForPage(meta.total_pages))}">回到最後一頁</a>`, "platform-empty");
    } else if (hasFilters) {
      body = pState("empty", "沒有符合條件的結果", "請調整篩選條件，或回到全部有聲書。", `<a class="platform-button" href="#/audiobooks">清除篩選</a>`, "platform-empty");
    } else {
      body = pState("empty", "目前還沒有可聆聽的公開作品", "有聲內容準備好後會在這裡出現。", `<a class="platform-button" href="#/search">探索公開作品</a>`, "platform-empty");
    }
    const target = document.querySelector("#platform-audiobook-result");
    target.innerHTML = `<div class="platform-result-head"><strong>${pEsc(meta.total)} 個結果</strong><span>${hasFilters ? "已套用篩選" : "公開有聲書"}</span></div>${body}${window.StoryLingoPagination.render(meta, { label: "有聲書分頁", hrefForPage: routeForPage })}`;
    bindPublicLinks(target);
  } catch (_) {
    if (!current()) return;
    const target = document.querySelector("#platform-audiobook-result");
    if (target) {
      target.innerHTML = pState("error", "有聲書目前無法載入", "請稍後再試。", `<button class="platform-button" id="audiobook-retry" type="button">重新載入</button>`, "platform-error");
      target.querySelector("#audiobook-retry")?.addEventListener("click", () => {
        if (current()) renderAudiobooks(hash, routeToken);
      });
    }
  }
}

async function renderRankings() {
  let requestId = 0;
  pShow();
  pLayout(`<div class="platform-tabs" id="platform-ranking-tabs" role="tablist" aria-label="排行榜分類"><button data-kind="hot" class="active" role="tab" aria-selected="true">人氣</button><button data-kind="new" role="tab" aria-selected="false">新書</button><button data-kind="completed" role="tab" aria-selected="false">完結</button><button data-kind="audio" role="tab" aria-selected="false">有聲</button></div><div id="platform-ranking-result">${pState("loading", "載入排行榜", "正在整理排行資料。", "", "platform-result-loading")}</div>`, "排行榜");
  const rankingPanel = document.querySelector("#platform-ranking-result");
  const rankingTabs = Array.from(document.querySelectorAll("#platform-ranking-tabs [role='tab']"));
  rankingTabs.forEach((button, index) => {
    button.id = `platform-ranking-tab-${button.dataset.kind}`;
    button.setAttribute("aria-controls", "platform-ranking-result");
    button.tabIndex = index === 0 ? 0 : -1;
  });
  rankingPanel?.setAttribute("role", "tabpanel");
  rankingPanel?.setAttribute("aria-labelledby", rankingTabs[0]?.id || "");
  rankingPanel?.setAttribute("tabindex", "0");
  async function load(kind) {
    const intent = ++requestId;
    const target = document.querySelector("#platform-ranking-result");
    target.innerHTML = pState("loading", "載入中", "正在更新排行榜。", "", "platform-result-loading");
    try {
      const data = await pApi(`/api/rankings?kind=${kind}&window=all&limit=50`);
      if (intent !== requestId || !target.isConnected) return;
      target.innerHTML = data.items?.length ? `<div class="platform-ranking-list">${data.items.map((item, index) => {
        const rankBook = { id: item.bid, title: item.title, cover: item.cover_path, owner: item.owner, author: item.author };
        const author = item.author;
        const authorHtml = author?.link && author.slug
          ? '<a class="platform-ranking-author platform-author-link" data-author-link href="#/author/' + encodeURIComponent(author.slug) + '">' + pEsc(author.displayName || "匿名作者") + "</a>"
          : '<span class="platform-ranking-author">' + pEsc(author?.displayName || item.owner || "匿名作者") + "</span>";
        return `<article class="platform-ranking-item"><a class="platform-ranking-book" href="#/book/${encodeURIComponent(item.bid)}" aria-label="第 ${index + 1} 名：${pEsc(item.title)}"><b aria-hidden="true">${String(index + 1).padStart(2, "0")}</b>${pCover(rankBook, "small")}<strong>${pEsc(item.title)}</strong></a><small>${authorHtml} · ${pEsc(item.category_name || "小說")}</small></article>`;
      }).join("")}</div>` : pState("empty", "目前還沒有排行資料", "先從搜尋或有聲書探索開始。", `<a class="platform-button" href="#/search">探索作品</a>`, "platform-empty");
      bindPublicLinks(target);
    } catch (_) { if (intent === requestId && target.isConnected) target.innerHTML = pState("error", "排行榜目前無法載入", "請稍後再試。", `<button class="platform-button" id="ranking-retry">重新載入</button>`, "platform-error"); target.querySelector("#ranking-retry")?.addEventListener("click", () => load(kind)); }
  }
  function activateRankingTab(button) {
    rankingTabs.forEach((item) => {
      const selected = item === button;
      item.classList.toggle("active", selected);
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
    });
    rankingPanel?.setAttribute("aria-labelledby", button.id);
    load(button.dataset.kind);
  }
  function moveRankingTabFocus(index, delta) {
    if (!rankingTabs.length) return;
    const nextIndex = (index + delta + rankingTabs.length) % rankingTabs.length;
    rankingTabs.forEach((item) => { item.tabIndex = -1; });
    rankingTabs[nextIndex].tabIndex = 0;
    rankingTabs[nextIndex].focus();
  }
  rankingTabs.forEach((button, index) => {
    button.addEventListener("click", () => activateRankingTab(button));
    button.addEventListener("keydown", (event) => {
      if (event.key === "ArrowRight") {
        event.preventDefault();
        moveRankingTabFocus(index, 1);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        moveRankingTabFocus(index, -1);
      } else if (event.key === "Home") {
        event.preventDefault();
        moveRankingTabFocus(index, -index);
      } else if (event.key === "End") {
        event.preventDefault();
        moveRankingTabFocus(index, rankingTabs.length - 1 - index);
      } else if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
        event.preventDefault();
        activateRankingTab(button);
      }
    });
  });
  load("hot");
}

async function renderDetail(bid) {
  const routeToken = platformState.routeGeneration;
  const principalToken = platformState.principalGeneration;
  const routeHash = location.hash;
  const current = () => routeToken === platformState.routeGeneration && principalToken === platformState.principalGeneration && routeHash === location.hash;
  pShow();
  pLayout(pState("loading", "載入作品資料", "正在準備閱讀與聆聽選項。", "", "platform-loading"), "作品詳情");
  try {
    const book = await pApi(`/api/books/${encodeURIComponent(bid)}`, { headers: { "X-StoryLingo-Public": "1" } });
    if (!current()) return;
    platformState.book = book;
    let recommendations = [];
    try { recommendations = (await pApi(`/api/books/${encodeURIComponent(bid)}/recommendations`)).items || []; } catch (_) {}
    let favorite = book.favorite === true;
    if (platformState.me && typeof book.favorite !== "boolean") {
      try { favorite = (await pApi("/api/me/library?kind=favorite")).items.some((item) => item.id === bid); } catch (_) {}
    }
    if (!current()) return;
    const chapters = (book.chapters || []).filter((chapter) => chapter.status !== "hidden");
    const firstChapter = chapters[0];
    const audiobook = book.audiobook && typeof book.audiobook === "object" ? book.audiobook : null;
    const hasCanonicalAudiobook = audiobook && Object.prototype.hasOwnProperty.call(audiobook, "playableChapterCount");
    const audioReady = hasCanonicalAudiobook
      ? Math.max(0, Number(audiobook.playableChapterCount) || 0)
      : chapters.filter((chapter) => chapter.audio === "ready").length;
    const canonicalFirstSeq = hasCanonicalAudiobook && audiobook.firstPlayableChapter != null
      ? Number(audiobook.firstPlayableChapter) : null;
    const firstPlayableSeq = Number.isInteger(canonicalFirstSeq) && chapters.some((chapter) => Number(chapter.seq) === canonicalFirstSeq)
      ? canonicalFirstSeq
      : (!hasCanonicalAudiobook ? chapters.find((chapter) => chapter.audio === "ready")?.seq : null);
    const playableSeqs = hasCanonicalAudiobook && Array.isArray(audiobook.playableChapterSeqs)
      ? new Set(audiobook.playableChapterSeqs.map((seq) => Number(seq))) : null;
    const audiobookLabel = audiobook?.availability === "partial" ? "部分章節可聆聽" : audioReady ? "可聆聽" : "尚無可聆聽章節";
    const firstHref = `#/read/${encodeURIComponent(bid)}/${firstChapter?.seq ?? 0}`;
    const firstReadingHref = `${firstHref}?mode=reading`;
    const firstListenHref = firstPlayableSeq != null ? `#/read/${encodeURIComponent(bid)}/${firstPlayableSeq}?mode=listen` : null;
    const readingProgress = await loadDetailProgress(bid, chapters);
    if (!current()) return;
    const progressHref = readingProgress
      ? publicReaderRoute(bid, readingProgress.seq, readingProgress.mode)
      : firstReadingHref;
    const progressSummary = readingProgress
      ? `上次讀到${chapterDisplayLabel(book, readingProgress.seq)}〈${readingProgress.chapter.title}〉 · ${readerModeLabel(readingProgress.mode)} · ${Math.round(readingProgress.percent)}%`
      : "進入後可在閱讀器切換純文字、聽書跟讀與學習工具。";
    const primaryLabel = readingProgress ? "繼續閱讀" : "開始閱讀";
    const secondaryAction = readingProgress
      ? `<a class="platform-button" href="${firstReadingHref}">從第一章開始</a>`
      : (firstListenHref ? `<a class="platform-button" href="${firstListenHref}">聽書跟讀</a>` : "");
    const chapterLink = (chapter) => {
      const playable = playableSeqs ? playableSeqs.has(Number(chapter.seq)) : chapter.audio === "ready";
      return `<a href="#/read/${encodeURIComponent(bid)}/${chapter.seq}"><span>${chapterDisplayLabel(book, chapter.seq)}</span><strong>${pEsc(chapter.title)}</strong><small>${playable ? "可聆聽" : "文字閱讀"}</small></a>`;
    };
    // Long works keep the first decision visible and progressively disclose the rest.
    // Every chapter remains a direct link once the reader expands the native details panel.
    const previewChapters = chapters.slice(0, 12);
    const remainingChapters = chapters.slice(12);
    const extraChapterMarkup = remainingChapters.length
      ? `<details class="platform-chapters-disclosure"><summary>查看其餘 ${remainingChapters.length} 章<span>展開完整目錄</span></summary><div class="platform-chapter-list platform-chapter-list-extra">${remainingChapters.map(chapterLink).join("")}</div></details>`
      : "";
    pLayout(`<section class="platform-detail-hero" aria-labelledby="platform-detail-title">${pCover({ ...book, cover: book.coverImage }, "large")}<div class="platform-detail-copy"><p class="platform-eyebrow">${pEsc(book.litCount || "NOVEL")}</p><h1 class="platform-detail-title" id="platform-detail-title">${pEsc(book.title)}</h1><p class="platform-author">${pEsc(book.owner || "匿名作者")}</p><p class="platform-detail-summary">${pEsc(book.synopsis || "作者尚未提供簡介")}</p><div class="platform-meta-row"><span>${pEsc(book.serial || "連載")}</span><span>${chapters.length} 章</span><span>${Number(book.totalChars || 0).toLocaleString()} 字</span>${audioReady ? `<span class="platform-audiobook-status">${pEsc(audiobookLabel)}</span>` : ""}</div><div class="reading-start-panel"><div class="reading-start-copy"><strong>${primaryLabel}</strong><span>${pEsc(progressSummary)}</span></div><div class="platform-actions reading-start-actions"><a class="platform-button platform-button-primary" href="${progressHref}">${primaryLabel}</a>${secondaryAction}</div></div><div class="platform-actions reading-social-actions"><button class="platform-button" id="platform-favorite">${favorite ? "已收藏" : "加入收藏"}</button><button class="platform-button" id="platform-follow">${book.follow ? "已追蹤" : "追蹤更新"}</button></div></div></section>${firstChapter ? `<section class="platform-section reading-mode-note" aria-labelledby="reading-mode-note-title"><div class="reading-mode-note-copy"><p class="platform-eyebrow">READING MODES</p><h2 id="reading-mode-note-title">進入閱讀後再選方式</h2><p>${pEsc(readingProgress ? "你可以沿用上次的方式繼續，也能在 Reader 內隨時切換。" : "先開始讀故事；進入 Reader 後可隨時切換純文字、聽書跟讀與語言學習工具。")}</p></div>${readingProgress ? `<span class="reading-progress-chip">${pEsc(progressSummary)}</span>` : ""}</section>` : ""}<section class="platform-section"><div class="platform-section-head"><div><p class="platform-eyebrow">TABLE OF CONTENTS</p><h2>章節目錄</h2></div><span>${chapters.length} 章 · ${audioReady} 章可聆聽</span></div><div class="platform-chapter-list">${previewChapters.map(chapterLink).join("")}</div>${extraChapterMarkup}</section>${recommendations.length ? pSection("你可能也喜歡", recommendations) : ""}<section class="platform-section"><div class="platform-section-head"><div><p class="platform-eyebrow">COMMUNITY</p><h2>讀者留言</h2></div></div><div id="platform-comments"><div class="platform-result-loading">載入留言...</div></div></section>`, book.title, { showHeader: false });
    if (!firstChapter) {
      document.querySelectorAll('.reading-start-panel, .reading-mode-note').forEach((node) => node.remove());
      document.querySelector('.platform-chapter-list').innerHTML = pState("empty", "尚無公開章節", "可以收藏作品或追蹤更新，等作者發布後再開始閱讀。", "", "platform-empty");
      document.querySelector('.platform-chapters-disclosure')?.remove();
    }
    const detailAuthorNode = document.querySelector(".platform-author");
    const detailAuthor = publicAuthor(book);
    if (detailAuthorNode && detailAuthor?.link && detailAuthor.slug) {
      detailAuthorNode.outerHTML = '<a class="platform-author platform-author-link" data-author-link href="#/author/'
        + encodeURIComponent(detailAuthor.slug) + '">' + pEsc(authorLabel(book)) + "</a>";
      bindPublicLinks(platformRoot);
    }
    if (detailAuthorNode && ["suspended", "tombstone"].includes(detailAuthor?.status)) {
      detailAuthorNode.insertAdjacentHTML("afterend", '<span class="platform-author-status">'
        + (detailAuthor.status === "suspended" ? "作者暫停更新" : "作者帳號已結束") + "</span>");
    }
    document.querySelector("#platform-favorite")?.addEventListener("click", async (event) => {
      if (!platformState.me) return loginPrompt();
      const btn = event.currentTarget; // async handler 中 currentTarget 會在 await 後失效，先捕獲
      if (btn.disabled) return;
      btn.disabled = true;
      try {
        await pApi(`/api/books/${encodeURIComponent(bid)}/favorite`, { method: favorite ? "DELETE" : "POST" });
        if (!current()) return;
        favorite = !favorite;
        btn.textContent = favorite ? "已收藏" : "加入收藏";
        NovelToast.toast(favorite ? "已加入收藏" : "已取消收藏", "success");
      } catch (error) { if (current()) NovelToast.toast(error.message, "error"); }
      finally { btn.disabled = false; }
    });
    document.querySelector("#platform-follow")?.addEventListener("click", async (event) => {
      if (!platformState.me) return loginPrompt();
      const btn = event.currentTarget;
      if (btn.disabled) return;
      btn.disabled = true;
      try {
        await pApi(`/api/books/${encodeURIComponent(bid)}/${book.follow ? "unfollow" : "follow"}`, { method: "POST" });
        if (!current()) return;
        book.follow = !book.follow;
        btn.textContent = book.follow ? "已追蹤" : "追蹤更新";
        NovelToast.toast(book.follow ? "已追蹤更新" : "已取消追蹤", "success");
      } catch (error) { if (current()) NovelToast.toast(error.message, "error"); }
      finally { btn.disabled = false; }
    });
    let comments = { items: [] };
    let commentsError = null;
    try { comments = await pApi(`/api/books/${encodeURIComponent(bid)}/comments`); }
    catch (error) { commentsError = error; }
    if (!current()) return;
    const commentsEl = document.querySelector("#platform-comments");
    const canModerate = platformState.me?.role === "admin";
    commentsEl.innerHTML = commentsError
      ? `<div class="platform-error"><h2>留言暫時無法載入</h2><p>作品內容仍可閱讀，請稍後再試。</p><button class="platform-button" id="platform-comments-retry" type="button">重試</button></div>`
      : `${platformState.me ? `<form id="platform-comment-form" class="platform-comment-form"><textarea name="body" maxlength="2000" placeholder="分享你對這部作品的想法"></textarea><button class="platform-button platform-button-primary">發表留言</button></form>` : `<p class="platform-note">登入後可以留言。</p>`}${comments.items?.length ? `<div class="platform-comments">${comments.items.map((comment) => `<article><header><strong>${pEsc(comment.displayName || "讀者")}</strong><time>${pEsc(comment.created_at)}</time>${canModerate ? `<button class="platform-comment-delete" data-comment-delete="${comment.id}" type="button">刪除</button>` : ""}</header><p>${pEsc(comment.body)}</p></article>`).join("")}</div>` : `<div class="platform-empty">還沒有留言，成為第一位分享心得的讀者。</div>`}`;
    document.querySelector("#platform-comments-retry")?.addEventListener("click", () => renderDetail(bid));
    document.querySelector("#platform-comment-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const button = form.querySelector("button");
      if (button.disabled || !current()) return;
      const body = String(new FormData(form).get("body") || "").trim();
      if (!body) { form.querySelector("textarea").focus(); return; }
      button.disabled = true;
      try {
        await pApi(`/api/books/${encodeURIComponent(bid)}/comments`, { method: "POST", body: { body } });
        if (current()) await renderDetail(bid);
      } catch (error) { if (current()) NovelToast.toast(error.message, "error"); }
      finally { button.disabled = false; }
    });
    document.querySelectorAll("[data-comment-delete]").forEach((button) => button.addEventListener("click", async () => {
      if (!window.confirm("確定刪除這則留言？")) return;
      try { await pApi(`/api/admin/comments/${button.dataset.commentDelete}`, { method: "DELETE" }); if (current()) await renderDetail(bid); }
      catch (error) { NovelToast.toast(error.message, "error"); }
    }));
  } catch (_) { if (current()) pLayout(pState("error", "無法開啟作品", "這部作品可能已下架，或目前暫時無法載入。", `<a class="platform-button" href="#/home">回到首頁</a> <a class="platform-button" href="#/search">搜尋作品</a>`, "platform-error"), "作品詳情"); }
}

async function saveReaderProgress(reader) {
  if (!platformState.me) {
    localStorage.setItem(`novel-reader:${reader.bid}`, JSON.stringify(reader));
    return;
  }
  try { await pApi("/api/me/progress", { method: "PUT", body: { bookId: reader.bid, chapterSeq: reader.seq, position: reader.position, percent: reader.percent } }); } catch (_) {}
}

let publicReaderChainIntent = null;

function publicReaderCurrent(controller) {
  return Boolean(controller && !controller.disposed
    && platformState.readerController === controller
    && controller.routeToken === platformState.routeGeneration
    && controller.principalGeneration === platformState.principalGeneration);
}

function publicReaderBind(controller, target, event, handler, options) {
  if (!target?.addEventListener) return;
  target.addEventListener(event, handler, options);
  controller.cleanups.push(() => target.removeEventListener(event, handler, options));
}

function publicReaderSetStatus(controller, message, kind = "info") {
  if (!publicReaderCurrent(controller)) return;
  controller.audioStatus = kind;
  const status = document.querySelector("#reader-audio-status");
  if (status) {
    status.textContent = message;
    status.dataset.state = kind;
  }
}

function publicReaderSetCapability(controller, message) {
  if (!publicReaderCurrent(controller)) return;
  const note = document.querySelector("#reader-capability-note");
  if (note) note.textContent = message || "";
}

function publicReaderSafeError(error) {
  const status = Number(error?.status || error?.statusCode || error?.response?.status);
  if (status === 401 || status === 403) return "此章節目前不可閱讀。";
  if (status === 404) return "找不到這個章節。";
  return "章節目前無法載入，請稍後再試。";
}

function publicReaderReleaseWakeLock(controller) {
  const lock = controller?.wakeLock;
  if (!lock) return;
  controller.wakeLock = null;
  try { Promise.resolve(lock.release?.()).catch(() => {}); } catch (_) {}
}

async function publicReaderRequestWakeLock(controller) {
  if (!publicReaderCurrent(controller) || controller.audio?.paused || document.hidden) return;
  if (!window.isSecureContext || !navigator.wakeLock?.request) {
    publicReaderSetCapability(controller, "螢幕常亮功能目前不可用，不影響播放。");
    return;
  }
  if (controller.wakeLock) return;
  try {
    const lock = await navigator.wakeLock.request("screen");
    if (!publicReaderCurrent(controller) || controller.audio?.paused) {
      try { await lock.release?.(); } catch (_) {}
      return;
    }
    controller.wakeLock = lock;
    publicReaderBind(controller, lock, "release", () => {
      if (publicReaderCurrent(controller)) {
        controller.wakeLock = null;
        publicReaderSetCapability(controller, "螢幕常亮已由瀏覽器釋放，播放仍會繼續。");
      }
    });
  } catch (_) {
    publicReaderSetCapability(controller, "螢幕常亮功能目前不可用，不影響播放。");
  }
}

function publicReaderClearMediaSession(controller) {
  if (!navigator.mediaSession || !publicReaderCurrent(controller) && !controller?.mediaSessionBound) return;
  const actions = ["play", "pause", "previoustrack", "nexttrack", "seekbackward", "seekforward"];
  actions.forEach((action) => {
    try { navigator.mediaSession.setActionHandler(action, null); } catch (_) {}
  });
  try { navigator.mediaSession.metadata = null; } catch (_) {}
  controller.mediaSessionBound = false;
}

function publicReaderBindMediaSession(controller, bookTitle, chapterTitle) {
  if (!navigator.mediaSession || typeof window.MediaMetadata !== "function") return;
  try {
    const metadata = window.NovelReader.safeMediaMetadata(bookTitle, chapterTitle);
    navigator.mediaSession.metadata = new window.MediaMetadata(metadata);
    controller.mediaSessionBound = true;
    const bindAction = (action, handler) => {
      try { navigator.mediaSession.setActionHandler(action, handler); } catch (_) {}
    };
    bindAction("play", () => controller.audio?.play?.().catch?.(() => {}));
    bindAction("pause", () => controller.audio?.pause?.());
    bindAction("previoustrack", () => {
      if (controller.navigation.previous != null) location.hash = publicReaderRoute(controller.bid, controller.navigation.previous, controller.mode);
    });
    bindAction("nexttrack", () => {
      if (controller.navigation.next != null) location.hash = publicReaderRoute(controller.bid, controller.navigation.next, controller.mode);
    });
    bindAction("seekbackward", (details) => {
      if (controller.audio) controller.audio.currentTime = Math.max(0, controller.audio.currentTime - Math.min(30, Number(details?.seekOffset) || 10));
    });
    bindAction("seekforward", (details) => {
      if (!controller.audio) return;
      const duration = Number.isFinite(controller.audio.duration) ? controller.audio.duration : Infinity;
      controller.audio.currentTime = Math.min(duration, controller.audio.currentTime + Math.min(30, Number(details?.seekOffset) || 10));
    });
  } catch (_) {
    publicReaderSetCapability(controller, "系統媒體控制目前不可用，不影響播放。");
  }
}

function publicReaderRoute(bid, seq, mode = "listen") {
  return "#/read/" + encodeURIComponent(String(bid)) + "/" + encodeURIComponent(String(seq)) + "?mode=" + encodeURIComponent(window.NovelReader.normalizeMode(mode, "listen"));
}

function publicReaderProgressStorage(bid) {
  return window.NovelReader.storageKey(bid);
}

async function savePublicReaderProgress(controller) {
  if (!publicReaderCurrent(controller)) return;
  const payload = window.NovelReader.progressPayload(controller);
  if (!payload.bookId) return;
  if (!platformState.me) {
    try {
      localStorage.setItem(publicReaderProgressStorage(controller.bid), JSON.stringify({
        ...payload, bid: controller.bid, seq: controller.seq, mode: controller.mode,
      }));
    } catch (_) {}
    return;
  }
  try {
    await pApi("/api/me/progress", { method: "PUT", body: payload });
  } catch (_) {
    if (publicReaderCurrent(controller)) publicReaderSetCapability(controller, "進度同步暫時失敗，仍可繼續閱讀。");
  }
}

function publicReaderScheduleProgress(controller) {
  if (!publicReaderCurrent(controller)) return;
  clearTimeout(controller.progressTimer);
  controller.progressTimer = setTimeout(() => savePublicReaderProgress(controller), 700);
}

function publicReaderRestorePayload(raw) {
  try { return window.NovelReader.normalizeProgress(raw); } catch (_) {
    return window.NovelReader.normalizeProgress(null);
  }
}

async function publicReaderLoadProgress(controller, bookId, chapterSeq) {
  let raw = null;
  if (platformState.me) {
    try {
      const result = await pApi("/api/me/progress");
      raw = (result.items || []).find((item) => String(item.book_id ?? item.bookId) === String(bookId)
        && Number(item.chapter_seq ?? item.chapterSeq ?? item.seq) === Number(chapterSeq));
    } catch (_) {}
  } else {
    try { raw = JSON.parse(localStorage.getItem(publicReaderProgressStorage(bookId)) || "null"); } catch (_) {}
  }
  if (!publicReaderCurrent(controller)) return null;
  return raw ? publicReaderRestorePayload(raw) : null;
}

function publicReaderApplyScrollProgress(controller, body, bar) {
  if (!publicReaderCurrent(controller) || !body) return;
  const scrollable = Math.max(1, body.scrollHeight - body.clientHeight);
  const position = window.NovelReader.clamp(body.scrollTop, 0, scrollable);
  const percent = window.NovelReader.clamp(position / scrollable * 100, 0, 100);
  controller.position = position;
  controller.percent = percent;
  if (bar) bar.style.width = percent + "%";
  publicReaderScheduleProgress(controller);
}

function publicReaderRenderStudy(controller, learningAvailable, learningMode, vocab, bilingual) {
  const heading = document.querySelector(".reader-sheet header");
  if (learningAvailable) {
    heading?.insertAdjacentHTML("afterbegin", '<div class="reader-heading-row"><span class="reader-learning-label">學習工具</span><button class="platform-button reader-mode-toggle" id="reader-mode-toggle" type="button" aria-expanded="' + String(learningMode) + '">' + (learningMode ? "關閉學習工具" : "開啟學習工具") + "</button></div>");
  }
  const studyHtml = (vocab.length ? '<section class="reader-study-section"><h3>本章單字</h3><div class="reader-vocab-grid">' + vocab.map((item) => '<button class="reader-vocab-card" type="button" data-vocab-en="' + pEsc(item.en) + '"><strong>' + pEsc(item.en) + "</strong><span>" + pEsc(item.zh || "") + "</span><small>" + pEsc(item.level || "") + " · 點擊播放發音</small></button>").join("") + "</div></section>" : "")
    + (bilingual.length ? '<section class="reader-study-section"><h3>雙語對照</h3><div class="reader-bilingual-list">' + bilingual.map((item) => "<article><p>" + pEsc(item.zh) + "</p><small>" + pEsc(item.en) + "</small></article>").join("") + "</div></section>" : "")
    + (!vocab.length && !bilingual.length ? '<div class="reader-study-empty">本章尚未有學習資料，可以先享受閱讀。</div>' : "");
  document.querySelector("#reader-body")?.insertAdjacentHTML("afterend", '<aside class="reader-study" id="reader-study" ' + (learningMode ? "" : "hidden") + ">" + studyHtml + "</aside>");
  const modeToggle = document.querySelector("#reader-mode-toggle");
  if (modeToggle) {
    modeToggle.addEventListener("click", () => {
      if (!publicReaderCurrent(controller)) return;
      const study = document.querySelector("#reader-study");
      const enabled = Boolean(study && study.hidden);
      if (study) study.hidden = !enabled;
      modeToggle.textContent = enabled ? "關閉學習工具" : "開啟學習工具";
      modeToggle.setAttribute("aria-expanded", String(enabled));
      try { localStorage.setItem("novel-reader-mode", enabled ? "learning" : "reading"); } catch (_) {}
    });
  }
  let vocabAudio = null;
  let vocabUrl = "";
  document.querySelectorAll("[data-vocab-en]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!publicReaderCurrent(controller)) return;
      const text = button.dataset.vocabEn || "";
      if (!text) return;
      button.disabled = true;
      try {
        const blob = await pApi("/api/preview-tts", { method: "POST", body: { text }, responseType: "blob" });
        if (vocabAudio) vocabAudio.pause();
        if (vocabUrl) URL.revokeObjectURL(vocabUrl);
        vocabUrl = URL.createObjectURL(blob);
        vocabAudio = new Audio(vocabUrl);
        await vocabAudio.play();
      } catch (_) { NovelToast.toast("發音暫時無法播放，請稍後再試。", "error"); }
      finally { button.disabled = false; }
    });
  });
  controller.cleanups.push(() => {
    if (vocabAudio) vocabAudio.pause();
    if (vocabUrl) URL.revokeObjectURL(vocabUrl);
  });
}

function publicReaderBindTranscript(controller, audio, segments, timingSegments, transcriptSegments) {
  if (!audio || !transcriptSegments.length) return;
  const starts = [];
  let total = 0;
  segments.forEach((_segment, index) => {
    starts[index] = total;
    total += Math.max(0, Number(timingSegments[index]?.dur) || 0);
  });
  const transcript = document.querySelector("#reader-body");
  let activeIndex = -1;
  const clearActive = () => transcript?.querySelectorAll(".reader-transcript-segment.active").forEach((element) => element.classList.remove("active"));
  const updateActive = () => {
    if (!publicReaderCurrent(controller)) return;
    let nextIndex = -1;
    let previousIndex = -1;
    for (const item of transcriptSegments) {
      const duration = Math.max(0, Number(timingSegments[item.index]?.dur) || 0);
      if (duration <= 0) continue;
      if (audio.currentTime < starts[item.index]) { nextIndex = previousIndex; break; }
      if (audio.currentTime < starts[item.index] + duration) { nextIndex = item.index; break; }
      previousIndex = item.index;
    }
    if (nextIndex < 0 && previousIndex >= 0) nextIndex = previousIndex;
    if (nextIndex === activeIndex) return;
    activeIndex = nextIndex;
    clearActive();
    if (nextIndex < 0) return;
    const element = transcript?.querySelector('[data-reader-index="' + nextIndex + '"]');
    element?.classList.add("active");
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    element?.scrollIntoView?.({ block: "center", behavior: reduced ? "auto" : "smooth" });
  };
  publicReaderBind(controller, audio, "timeupdate", updateActive);
  publicReaderBind(controller, audio, "ended", () => { activeIndex = -1; clearActive(); });
  transcript?.querySelectorAll("[data-reader-index]").forEach((element) => {
    const handler = () => {
      if (!publicReaderCurrent(controller)) return;
      const index = Number(element.dataset.readerIndex);
      audio.currentTime = starts[index] || 0;
      audio.play().catch(() => publicReaderSetStatus(controller, "請按播放繼續聆聽。", "info"));
    };
    publicReaderBind(controller, element, "click", handler);
  });
}

async function publicReaderAdvance(controller) {
  if (!publicReaderCurrent(controller) || controller.advancing) return;
  const next = controller.navigation.next;
  if (next == null) {
    publicReaderSetStatus(controller, "已到目前可聽範圍的最後一章。", "boundary");
    return;
  }
  controller.advancing = true;
  publicReaderSetStatus(controller, "正在確認下一個可播放章節…", "loading");
  try {
    const nextData = await pApi("/api/books/" + encodeURIComponent(controller.bid) + "/read/" + encodeURIComponent(next));
    if (!publicReaderCurrent(controller)) return;
    if (!window.NovelReader.isPlayableChapter(nextData.chapter)) {
      publicReaderSetStatus(controller, "下一章尚未生成可播放音訊，已停在目前章節。", "boundary");
      return;
    }
    publicReaderChainIntent = { bid: String(controller.bid), seq: Number(next) };
    location.hash = publicReaderRoute(controller.bid, next, "listen") + "&autoplay=1";
  } catch (_) {
    if (publicReaderCurrent(controller)) publicReaderSetStatus(controller, "下一章目前無法載入，請使用手動導覽。", "error");
  } finally {
    controller.advancing = false;
  }
}

function disposePublicReader() {
  const controller = platformState.readerController;
  if (controller) {
    // Route changes must persist the latest position before the controller is fenced.
    savePublicReaderProgress(controller);
    controller.disposed = true;
    clearTimeout(controller.progressTimer);
    controller.cleanups.splice(0).forEach((cleanup) => {
      try { cleanup(); } catch (_) {}
    });
    publicReaderReleaseWakeLock(controller);
    publicReaderClearMediaSession(controller);
    if (controller.audio) {
      try { controller.audio.pause(); } catch (_) {}
      controller.audio.removeAttribute?.("src");
    }
  }
  clearTimeout(platformState.progressTimer);
  platformState.progressTimer = null;
  platformState.readerController = null;
  platformState.reader = null;
}

function readerSegmentText(segment) {
  if (!segment || typeof segment !== "object") return "";
  return String(segment.text || "").trim();
}

function readerSpeakerLabel(segment) {
  const speaker = String(segment?.speaker || "").trim();
  const speakerId = String(segment?.speaker_id || "").trim();
  if (speakerId === "speaker:narrator" || ["narrator", "旁白"].includes(speaker.toLowerCase())) return "旁白";
  if (speakerId === "speaker:unresolved" || ["unknown", "speaker", "generic", "說話者", "語者", "角色", "某人", "有人", "未解析語者"].includes(speaker.toLowerCase())) {
    return "待辨識語者";
  }
  return speaker || "待辨識語者";
}

async function renderPublicReader(bid, seq, requestedMode = "", routeToken = platformState.routeGeneration, autoplayRequested = false) {
  pShow();
  const textMode = requestedMode === "reading";
  const listenMode = !textMode;
  const controller = {
    bid: String(bid),
    seq: Number(seq),
    mode: window.NovelReader.normalizeMode(requestedMode, textMode ? "reading" : "listen"),
    routeToken,
    principalGeneration: platformState.principalGeneration,
    disposed: false,
    cleanups: [],
    progressTimer: null,
    audio: null,
    audioPositionSeconds: 0,
    position: 0,
    percent: 0,
    navigation: { previous: null, next: null },
    autoNext: false,
    advancing: false,
    audioStatus: "loading",
  };
  platformState.readerController = controller;
  platformState.reader = controller;
  const chainIntent = Boolean(autoplayRequested && listenMode && publicReaderChainIntent
    && publicReaderChainIntent.bid === controller.bid
    && publicReaderChainIntent.seq === controller.seq);
  if (chainIntent) publicReaderChainIntent = null;
  pLayout('<div class="platform-result-loading">載入章節...</div>', textMode ? "文字閱讀" : "聽書跟讀");
  try {
    const data = await pApi("/api/books/" + encodeURIComponent(bid) + "/read/" + encodeURIComponent(seq));
    if (!publicReaderCurrent(controller)) return;
    const chapter = data.chapter;
    const learningTypeAvailable = supportsLearning(data.book);
    let learningData = { analysis: { segments: [] } };
    if (listenMode || learningTypeAvailable) {
      try { learningData = await pApi("/api/books/" + encodeURIComponent(bid) + "/chapters/" + encodeURIComponent(seq)); } catch (_) {}
    }
    if (!publicReaderCurrent(controller)) return;
    const segments = learningData.analysis?.segments || [];
    const learnedVocab = learningData.analysis?.learning?.vocab;
    const vocab = Array.isArray(learnedVocab)
      ? learnedVocab.filter((item) => item && item.en)
      : segments.filter((item) => item.type === "vocab" && item.vocab?.en).map((item) => item.vocab);
    const bilingual = segments.filter((item) => item.type === "bilingual" && item.en && item.zh);
    const learningAvailable = learningTypeAvailable && (vocab.length > 0 || bilingual.length > 0);
    const learningMode = learningAvailable && requestedMode !== "reading"
      && (requestedMode === "learning" || localStorage.getItem("novel-reader-mode") === "learning");
    const timingSegments = Array.isArray(learningData.timing?.segments) ? learningData.timing.segments : [];
    const analysisStale = learningData.analysis?.status === "stale";
    const audioReady = chapter.audio === "ready" && !analysisStale;
    const transcriptSegments = segments
      .map((segment, index) => ({ segment, index, text: readerSegmentText(segment) }))
      .filter(({ segment, text }) => !["vocab", "bilingual"].includes(segment.type) && text);
    const hasSyncedTranscript = listenMode && transcriptSegments.length > 0 && timingSegments.length === segments.length;
    const hasSourceTranscript = listenMode && analysisStale && transcriptSegments.length > 0;
    const paragraphs = String(chapter.text || "").split(/\n+/).filter(Boolean).map((line) => "<p>" + pEsc(line) + "</p>").join("");
    const transcriptHtml = (hasSyncedTranscript || hasSourceTranscript)
      ? transcriptSegments.map(({ segment, index, text }) => '<article class="reader-transcript-segment" data-reader-index="' + index + '"><span class="reader-segment-speaker">' + pEsc(readerSpeakerLabel(segment)) + '</span><div class="reader-segment-text">' + pEsc(text) + "</div></article>").join("")
      : paragraphs;
    controller.navigation = data.navigation || { previous: null, next: null };
    try { controller.autoNext = listenMode && window.NovelReader.isAutoNextEnabled(localStorage.getItem(window.NovelReader.autoNextKey(bid))); } catch (_) {}
    const audioUrl = location.origin + "/api/books/" + encodeURIComponent(bid) + "/audio/" + encodeURIComponent(seq);
    let speed = 1;
    try { speed = window.NovelReader.normalizeSpeed(localStorage.getItem("novel_play_rate"), 1); } catch (_) {}
    const audioHtml = audioReady && listenMode
      ? '<div class="reader-audio-box"><audio id="reader-audio" controls preload="metadata" src="' + pEsc(audioUrl) + '" aria-label="' + pEsc(chapter.title) + ' 音訊"></audio><label class="reader-speed-label" for="reader-speed">播放速度<select id="reader-speed"><option value="0.75">0.75×</option><option value="0.85">0.85×</option><option value="1">1×</option><option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="2">2×</option></select></label><button class="platform-button reader-audio-retry" id="reader-audio-retry" type="button" hidden>重試音訊</button></div>'
      : analysisStale
        ? '<span class="reader-audio-note">目前音訊與最新來源分段不一致，重新生成後才可同步播放。</span>'
        : chapter.audio === "ready"
        ? '<span class="reader-audio-note">目前是純文字閱讀；可從上方切換聽書跟讀。</span>'
        : '<span class="reader-audio-note">本章尚未生成音訊，現在可以直接閱讀</span>';
    const previousHref = controller.navigation.previous == null
      ? "#/book/" + encodeURIComponent(bid)
      : publicReaderRoute(bid, controller.navigation.previous, controller.mode);
    const nextHref = controller.navigation.next == null
      ? "#/book/" + encodeURIComponent(bid)
      : publicReaderRoute(bid, controller.navigation.next, controller.mode);
    const controls = '<button class="platform-button reader-controls-toggle" id="reader-controls-toggle" type="button" aria-expanded="true" aria-controls="reader-controls">隱藏閱讀工具</button>'
      + '<div class="reader-controls" id="reader-controls" aria-label="閱讀工具">'
      + '<label class="reader-check"><input id="reader-auto-next" type="checkbox" ' + (controller.autoNext ? "checked" : "") + (listenMode && audioReady ? "" : "disabled") + '>播完自動下一章</label>'
      + '<button class="platform-button" id="reader-bookmark" type="button">加入書籤</button>'
      + '<button class="platform-button" id="reader-fullscreen" type="button">全螢幕閱讀</button>'
      + "</div>";
    const modeSwitch = '<nav class="reader-mode-switcher" aria-label="閱讀方式"><span>閱讀方式</span><a class="' + (textMode ? 'active' : '') + '" data-reader-mode="reading" aria-current="' + (textMode ? 'page' : 'false') + '" href="' + pEsc(publicReaderRoute(bid, seq, "reading")) + '">純文字閱讀</a>' + (audioReady ? '<a class="' + (listenMode ? 'active' : '') + '" data-reader-mode="listen" aria-current="' + (listenMode ? 'page' : 'false') + '" href="' + pEsc(publicReaderRoute(bid, seq, "listen")) + '">聽書跟讀</a>' : '') + '</nav>';
    pLayout('<div class="reader-top"><a href="#/book/' + encodeURIComponent(bid) + '">← 返回作品目錄</a><span>' + pEsc(data.book.title) + "</span><span>" + pEsc(chapterDisplayLabel(data.book, seq)) + '</span></div><article class="reader-sheet" id="reader-sheet"><header><p class="platform-eyebrow">' + pEsc(data.book.title) + "</p><h2>" + pEsc(chapter.title) + '</h2>' + modeSwitch + '<div id="reader-progress" class="reader-progress"><span></span></div><p class="reader-capability-note" id="reader-capability-note" role="status"></p>' + controls + '</header><div class="reader-body' + ((hasSyncedTranscript || hasSourceTranscript) ? " reader-transcript" : "") + '" id="reader-body">' + transcriptHtml + '</div><p class="reader-audio-status" id="reader-audio-status" aria-live="polite"></p><footer class="reader-footer"><nav class="reader-navigation" aria-label="章節導覽"><a class="platform-button" data-reader-navigation href="' + pEsc(previousHref) + '">' + (controller.navigation.previous == null ? '返回目錄' : '上一章') + '</a><a class="platform-button platform-button-primary" data-reader-navigation href="' + pEsc(nextHref) + '">' + (controller.navigation.next == null ? '返回作品' : '下一章') + '</a></nav><div class="reader-footer-media">' + audioHtml + '</div></footer><p class="reader-boundary" id="reader-boundary" role="status"></p></article>', chapter.title, { showHeader: false });
    if (!publicReaderCurrent(controller)) return;
    const audio = document.querySelector("#reader-audio");
    const body = document.querySelector("#reader-body");
    const progressBar = document.querySelector("#reader-progress span");
    const autoNext = document.querySelector("#reader-auto-next");
    const retry = document.querySelector("#reader-audio-retry");
    controller.audio = audio;
    if (audio) {
      audio.playbackRate = speed;
      publicReaderSetStatus(controller, "音訊可以播放，請按播放開始。", "ready");
      publicReaderBindMediaSession(controller, data.book.title, chapter.title);
      publicReaderBindTranscript(controller, audio, segments, timingSegments, transcriptSegments);
    } else {
      publicReaderSetStatus(controller, analysisStale
        ? "目前顯示來源句子分段；音訊同步需重新生成。"
        : chapter.audio === "ready" ? "目前為純文字閱讀。" : "本章尚未生成音訊，現在可以直接閱讀。", "boundary");
    }
    const controlsToggle = document.querySelector("#reader-controls-toggle");
    const controlsPanel = document.querySelector("#reader-controls");
    controlsToggle?.addEventListener("click", () => {
      if (!publicReaderCurrent(controller) || !controlsPanel) return;
      controlsPanel.hidden = !controlsPanel.hidden;
      const expanded = !controlsPanel.hidden;
      controlsToggle.setAttribute("aria-expanded", String(expanded));
      controlsToggle.textContent = expanded ? "隱藏閱讀工具" : "顯示閱讀工具";
    });
    if (autoNext) {
      autoNext.addEventListener("change", () => {
        if (!publicReaderCurrent(controller)) return;
        controller.autoNext = Boolean(autoNext.checked);
        try { localStorage.setItem(window.NovelReader.autoNextKey(bid), controller.autoNext ? "1" : "0"); } catch (_) {}
      });
    }
    document.querySelector("#reader-bookmark")?.addEventListener("click", async (event) => {
      if (!publicReaderCurrent(controller)) return;
      if (!platformState.me) {
        NovelToast.toast("請先登入，才能建立書籤。", "info");
        const login = document.querySelector("#btn-login");
        if (login) setTimeout(() => login.click(), 0);
        return;
      }
      const button = event.currentTarget;
      if (button.disabled) return;
      button.disabled = true;
      try {
        await pApi("/api/me/bookmarks", { method: "POST", body: { bookId: controller.bid, chapterSeq: controller.seq, position: controller.position, note: "" } });
        if (publicReaderCurrent(controller)) {
          button.textContent = "已加入書籤";
          publicReaderSetCapability(controller, "書籤已保存。");
        }
      } catch (_) {
        if (publicReaderCurrent(controller)) NovelToast.toast("書籤暫時無法保存，請稍後再試。", "error");
      } finally { button.disabled = false; }
    });
    const sheet = document.querySelector("#reader-sheet");
    const fullscreen = document.querySelector("#reader-fullscreen");
    const updateFullscreenLabel = () => {
      if (fullscreen) fullscreen.textContent = document.fullscreenElement === sheet ? "退出全螢幕" : "全螢幕閱讀";
    };
    publicReaderBind(controller, document, "fullscreenchange", updateFullscreenLabel);
    fullscreen?.addEventListener("click", async () => {
      if (!publicReaderCurrent(controller) || !sheet) return;
      try {
        if (document.fullscreenElement) await document.exitFullscreen?.();
        else if (sheet.requestFullscreen) await sheet.requestFullscreen();
        else publicReaderSetCapability(controller, "此瀏覽器不支援全螢幕，仍可正常閱讀。");
      } catch (_) { publicReaderSetCapability(controller, "全螢幕目前無法使用，仍可正常閱讀。"); }
      updateFullscreenLabel();
    });
    if (audio) {
      publicReaderBind(controller, audio, "loadstart", () => publicReaderSetStatus(controller, "正在載入音訊…", "loading"));
      publicReaderBind(controller, audio, "canplay", () => {
        if (!audio.paused) return;
        publicReaderSetStatus(controller, "音訊可以播放，請按播放開始。", "ready");
      });
      publicReaderBind(controller, audio, "loadedmetadata", () => {
        const saved = controller.restore;
        if (!saved) return;
        const duration = Number.isFinite(audio.duration) ? Math.max(0, audio.duration) : Number.MAX_SAFE_INTEGER;
        controller.audioPositionSeconds = window.NovelReader.clamp(saved.audioPositionSeconds, 0, duration);
        if (controller.audioPositionSeconds > 0) {
          try { audio.currentTime = controller.audioPositionSeconds; } catch (_) {}
        }
      });
      publicReaderBind(controller, audio, "error", () => {
        publicReaderSetStatus(controller, "音訊暫時無法載入，請重試；正文仍可閱讀。", "error");
        const retryButton = document.querySelector("#reader-audio-retry");
        if (retryButton) retryButton.hidden = false;
      });
      publicReaderBind(controller, audio, "play", () => {
        controller.playStarted = true;
        publicReaderSetStatus(controller, "正在播放。", "playing");
        publicReaderRequestWakeLock(controller);
        publicReaderScheduleProgress(controller);
      });
      publicReaderBind(controller, audio, "pause", () => {
        controller.audioPositionSeconds = window.NovelReader.clamp(audio.currentTime, 0, Number.isFinite(audio.duration) ? audio.duration : Number.MAX_SAFE_INTEGER);
        publicReaderReleaseWakeLock(controller);
        if (!audio.ended) publicReaderSetStatus(controller, "已暫停。", "paused");
        publicReaderScheduleProgress(controller);
      });
      publicReaderBind(controller, audio, "timeupdate", () => {
        controller.audioPositionSeconds = window.NovelReader.clamp(audio.currentTime, 0, Number.isFinite(audio.duration) ? audio.duration : Number.MAX_SAFE_INTEGER);
        publicReaderScheduleProgress(controller);
      });
      publicReaderBind(controller, audio, "ended", () => {
        controller.audioPositionSeconds = Number.isFinite(audio.duration) ? Math.max(0, audio.duration) : controller.audioPositionSeconds;
        publicReaderScheduleProgress(controller);
        if (window.NovelReader.shouldAutoAdvance({ enabled: controller.autoNext, ended: true, manualPause: false, next: controller.navigation.next })) {
          publicReaderAdvance(controller);
        } else if (controller.navigation.next == null) {
          publicReaderSetStatus(controller, "已到目前可聽範圍的最後一章。", "boundary");
        } else {
          publicReaderSetStatus(controller, "本章播放完畢，可手動前往下一章。", "ready");
        }
      });
      if (retry) {
        retry.addEventListener("click", () => {
          if (!publicReaderCurrent(controller)) return;
          retry.hidden = true;
          publicReaderSetStatus(controller, "正在重新載入音訊…", "loading");
          try { audio.load(); } catch (_) {}
        });
      }
    }
    publicReaderBind(controller, document, "visibilitychange", () => {
      if (!publicReaderCurrent(controller)) return;
      if (document.hidden) publicReaderScheduleProgress(controller);
      else if (audio && !audio.paused) publicReaderRequestWakeLock(controller);
    });
    publicReaderBind(controller, window, "pagehide", () => savePublicReaderProgress(controller));
    publicReaderRenderStudy(controller, learningAvailable, learningMode, vocab, bilingual);
    const restored = await publicReaderLoadProgress(controller, bid, seq);
    if (!publicReaderCurrent(controller)) return;
    controller.restore = restored;
    if (restored && body) {
      const scrollable = Math.max(0, body.scrollHeight - body.clientHeight);
      controller.position = window.NovelReader.clamp(restored.position, 0, scrollable);
      body.scrollTop = controller.position;
      publicReaderApplyScrollProgress(controller, body, progressBar);
      if (audio && audio.readyState >= 1) {
        const duration = Number.isFinite(audio.duration) ? Math.max(0, audio.duration) : Number.MAX_SAFE_INTEGER;
        controller.audioPositionSeconds = window.NovelReader.clamp(restored.audioPositionSeconds, 0, duration);
        if (controller.audioPositionSeconds > 0) {
          try { audio.currentTime = controller.audioPositionSeconds; } catch (_) {}
        }
      }
    }
    publicReaderBind(controller, body, "scroll", () => publicReaderApplyScrollProgress(controller, body, progressBar), { passive: true });
    const viewResult = pApi("/api/books/" + encodeURIComponent(bid) + "/view", { method: "POST", body: { eventType: "read", chapterSeq: Number(seq) } }).catch(() => {});
    if (chainIntent && audio && controller.autoNext) {
      Promise.resolve(audio.play?.()).catch(() => publicReaderSetStatus(controller, "已切換下一章，請按播放繼續。", "info"));
    }
    await viewResult;
  } catch (error) {
    if (!publicReaderCurrent(controller)) return;
    pLayout('<div class="platform-error"><h2>無法載入章節</h2><p>' + pEsc(publicReaderSafeError(error)) + '</p><a class="platform-button" href="#/home">回到首頁</a></div>', textMode ? "文字閱讀" : "聽書跟讀");
  }
}

async function renderReader(bid, seq, requestedMode = "") {
  pShow();
  const textMode = requestedMode === "reading";
  const listenMode = !textMode;
  pLayout(`<div class="platform-result-loading">載入章節...</div>`, textMode ? "文字閱讀" : "聽書跟讀");
  try {
    const data = await pApi(`/api/books/${encodeURIComponent(bid)}/read/${encodeURIComponent(seq)}`);
    const chapter = data.chapter;
    const learningTypeAvailable = supportsLearning(data.book);
    let learningData = { analysis: { segments: [] } };
    // 聽書跟讀需要同一份 analysis/timing；純文字模式只有學習型作品才需載入學習資料。
    if (listenMode || learningTypeAvailable) {
      try { learningData = await pApi(`/api/books/${encodeURIComponent(bid)}/chapters/${encodeURIComponent(seq)}`); } catch (_) {}
    }
    const segments = learningData.analysis?.segments || [];
    const learnedVocab = learningData.analysis?.learning?.vocab;
    const vocab = Array.isArray(learnedVocab)
      ? learnedVocab.filter((item) => item && item.en)
      : segments.filter((item) => item.type === "vocab" && item.vocab?.en).map((item) => item.vocab);
    const bilingual = segments.filter((item) => item.type === "bilingual" && item.en && item.zh);
    const learningAvailable = learningTypeAvailable && (vocab.length > 0 || bilingual.length > 0);
    const learningMode = learningAvailable && requestedMode !== "reading" && (requestedMode === "learning" || localStorage.getItem("novel-reader-mode") === "learning");
    const timingSegments = Array.isArray(learningData.timing?.segments) ? learningData.timing.segments : [];
    const analysisStale = learningData.analysis?.status === "stale";
    const audioReady = chapter.audio === "ready" && !analysisStale;
    // 只有 backend-owned canonical narration/dialogue text 才進入同步字幕；學習卡片
    // 的 utterance/翻譯資料不能冒充正文，沒有可同步 segment 時回退成原文段落。
    const transcriptSegments = segments
      .map((segment, index) => ({ segment, index, text: readerSegmentText(segment) }))
      .filter(({ segment, text }) => !["vocab", "bilingual"].includes(segment.type) && text);
    const hasSyncedTranscript = listenMode && transcriptSegments.length > 0 && timingSegments.length === segments.length;
    const hasSourceTranscript = listenMode && analysisStale && transcriptSegments.length > 0;
    const paragraphs = String(chapter.text || "").split(/\n+/).filter(Boolean).map((line) => `<p>${pEsc(line)}</p>`).join("");
    const transcriptHtml = (hasSyncedTranscript || hasSourceTranscript)
      ? transcriptSegments.map(({ segment, index, text }) => `<article class="reader-transcript-segment" data-reader-index="${index}"><span class="reader-segment-speaker">${pEsc(readerSpeakerLabel(segment))}</span><div class="reader-segment-text">${pEsc(text)}</div></article>`).join("")
      : paragraphs;
    platformState.reader = { bid, seq: Number(seq), position: 0, percent: 0 };
    const audioUrl = `${location.origin}/api/books/${encodeURIComponent(bid)}/audio/${encodeURIComponent(seq)}`;
    const savedSpeed = Number(localStorage.getItem("novel_play_rate"));
    const speed = [0.75, 0.85, 1, 1.25, 1.5, 2].includes(savedSpeed) ? savedSpeed : 1;
    const audioHtml = audioReady && listenMode
      ? `<div class="reader-audio-box"><audio id="reader-audio" controls preload="metadata" src="${pEsc(audioUrl)}" aria-label="${pEsc(chapter.title)} 音訊"></audio><label class="reader-speed-label" for="reader-speed">播放速度<select id="reader-speed"><option value="0.75">0.75×</option><option value="0.85">0.85×</option><option value="1">1×</option><option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="2">2×</option></select></label></div>`
      : analysisStale
        ? `<span class="reader-audio-note">目前音訊與最新來源分段不一致，重新生成後才可同步播放。</span>`
        : chapter.audio === "ready"
        ? `<span class="reader-audio-note">目前是純文字閱讀；切換「聽書跟讀」可播放並同步正文</span>`
        : `<span class="reader-audio-note">本章尚未生成音訊，現在可以直接閱讀</span>`;
    pLayout(`<div class="reader-top"><a href="#/book/${encodeURIComponent(bid)}">← 返回作品</a><span>${pEsc(data.book.title)}</span><span>${chapterDisplayLabel(data.book, seq)}</span></div><article class="reader-sheet"><header><p class="platform-eyebrow">${pEsc(data.book.title)}</p><h2>${pEsc(chapter.title)}</h2><div id="reader-progress" class="reader-progress"><span></span></div></header><div class="reader-body${(hasSyncedTranscript || hasSourceTranscript) ? " reader-transcript" : ""}" id="reader-body">${transcriptHtml}</div><footer class="reader-footer"><a class="platform-button" href="${data.navigation.previous == null ? `#/book/${encodeURIComponent(bid)}` : `#/read/${encodeURIComponent(bid)}/${data.navigation.previous}`}">${data.navigation.previous == null ? "返回目錄" : "上一章"}</a>${audioHtml}<a class="platform-button platform-button-primary" href="${data.navigation.next == null ? `#/book/${encodeURIComponent(bid)}` : `#/read/${encodeURIComponent(bid)}/${data.navigation.next}`}">${data.navigation.next == null ? "返回作品" : "下一章"}</a></footer></article>`, chapter.title);
    const audio = document.querySelector("#reader-audio");
    const speedSelect = document.querySelector("#reader-speed");
    if (speedSelect) {
      speedSelect.value = String(speed);
      speedSelect.addEventListener("change", (event) => {
        const nextSpeed = Number(event.currentTarget.value);
        if (!audio || !Number.isFinite(nextSpeed)) return;
        audio.playbackRate = nextSpeed;
        localStorage.setItem("novel_play_rate", String(nextSpeed));
      });
    }
    if (audio) audio.playbackRate = speed;
    const heading = document.querySelector(".reader-sheet header");
    if (learningAvailable) {
      heading?.insertAdjacentHTML("afterbegin", `<div class="reader-heading-row"><span></span><button class="platform-button reader-mode-toggle" id="reader-mode-toggle" type="button">${learningMode ? "純閱讀" : "閱讀＋學習"}</button></div>`);
    }
    const studyHtml = `${vocab.length ? `<section class="reader-study-section"><h3>本章單字</h3><div class="reader-vocab-grid">${vocab.map((item) => `<button class="reader-vocab-card" type="button" data-vocab-en="${pEsc(item.en)}"><strong>${pEsc(item.en)}</strong><span>${pEsc(item.zh || "")}</span><small>${pEsc(item.level || "")} · 點擊播放發音</small></button>`).join("")}</div></section>` : ""}${bilingual.length ? `<section class="reader-study-section"><h3>雙語對照</h3><div class="reader-bilingual-list">${bilingual.map((item) => `<article><p>${pEsc(item.zh)}</p><small>${pEsc(item.en)}</small></article>`).join("")}</div></section>` : ""}${!vocab.length && !bilingual.length ? `<div class="reader-study-empty">本章尚未有學習資料，可以先享受閱讀。</div>` : ""}`;
    document.querySelector("#reader-body")?.insertAdjacentHTML("afterend", `<aside class="reader-study" id="reader-study" ${learningMode ? "" : "hidden"}>${studyHtml}</aside>`);
    document.querySelector("#reader-mode-toggle")?.addEventListener("click", (event) => {
      const next = document.querySelector("#reader-study");
      const enabled = next.hidden;
      next.hidden = !enabled;
      localStorage.setItem("novel-reader-mode", enabled ? "learning" : "reading");
      event.currentTarget.textContent = enabled ? "純閱讀" : "閱讀＋學習";
    });
    let vocabAudio = null;
    let vocabUrl = "";
    document.querySelectorAll("[data-vocab-en]").forEach((button) => button.addEventListener("click", async () => {
      const text = button.dataset.vocabEn || "";
      if (!text) return;
      button.disabled = true;
      try {
        const blob = await pApi("/api/preview-tts", { method: "POST", body: { text }, responseType: "blob" });
        if (vocabAudio) vocabAudio.pause();
        if (vocabUrl) URL.revokeObjectURL(vocabUrl);
        vocabUrl = URL.createObjectURL(blob);
        vocabAudio = new Audio(vocabUrl);
        await vocabAudio.play();
      } catch (error) { NovelToast.toast(error.message || "發音暫時無法播放", "error"); }
      finally { button.disabled = false; }
    }));
    if (hasSyncedTranscript && audio) {
      const starts = [];
      let total = 0;
      segments.forEach((_segment, index) => {
        starts[index] = total;
        total += Math.max(0, Number(timingSegments[index]?.dur) || 0);
      });
      const transcript = document.querySelector("#reader-body");
      let activeIndex = -1;
      const clearActive = () => transcript.querySelectorAll(".reader-transcript-segment.active").forEach((element) => element.classList.remove("active"));
      const updateActive = () => {
        let nextIndex = -1;
        let previousIndex = -1;
        for (const { index } of transcriptSegments) {
          const duration = Math.max(0, Number(timingSegments[index]?.dur) || 0);
          if (duration <= 0) continue;
          if (audio.currentTime < starts[index]) {
            nextIndex = previousIndex;
            break;
          }
          if (audio.currentTime < starts[index] + duration) {
            nextIndex = index;
            break;
          }
          previousIndex = index;
        }
        if (nextIndex < 0 && previousIndex >= 0) nextIndex = previousIndex;
        if (nextIndex === activeIndex) return;
        activeIndex = nextIndex;
        clearActive();
        if (nextIndex < 0) return;
        const element = transcript.querySelector(`[data-reader-index="${nextIndex}"]`);
        element?.classList.add("active");
        element?.scrollIntoView({ block: "center", behavior: "smooth" });
      };
      audio.addEventListener("timeupdate", updateActive);
      audio.addEventListener("ended", () => { activeIndex = -1; clearActive(); });
      transcript.querySelectorAll("[data-reader-index]").forEach((element) => element.addEventListener("click", () => {
        const index = Number(element.dataset.readerIndex);
        audio.currentTime = starts[index] || 0;
        audio.play().catch(() => {});
      }));
    }
    const body = document.querySelector("#reader-body");
    const progressBar = document.querySelector("#reader-progress span");
    const restore = platformState.me ? (await pApi("/api/me/progress")).items?.find((item) => item.book_id === data.book.id) : JSON.parse(localStorage.getItem(`novel-reader:${bid}`) || "null");
    if (restore && Number(restore.chapter_seq ?? restore.seq) === Number(seq)) body.scrollTop = 0;
    const update = () => {
      const scrollable = Math.max(1, body.scrollHeight - body.clientHeight);
      const percent = Math.min(100, Math.max(0, body.scrollTop / scrollable * 100));
      platformState.reader.position = body.scrollTop;
      platformState.reader.percent = percent;
      progressBar.style.width = `${percent}%`;
      clearTimeout(platformState.progressTimer);
      platformState.progressTimer = setTimeout(() => saveReaderProgress(platformState.reader), 1000);
    };
    body.addEventListener("scroll", update, { passive: true });
    await pApi(`/api/books/${encodeURIComponent(bid)}/view`, { method: "POST", body: { eventType: "read", chapterSeq: Number(seq) } }).catch(() => {});
  } catch (error) { pLayout(`<div class="platform-error"><h2>無法載入章節</h2><p>${pEsc(error.message)}</p><a class="platform-button" href="#/home">回到首頁</a></div>`, textMode ? "文字閱讀" : "聽書跟讀"); }
}

async function renderShelf(query = "", routeToken = platformState.routeGeneration) {
  pShow();
  if (!platformState.me) { pLayout(pState("empty", "登入後管理你的書架", "收藏、追蹤與閱讀進度會在不同裝置同步。", `<button class="platform-button platform-button-primary" id="platform-login-button" type="button">登入或註冊</button>`, "platform-empty platform-login-empty"), "我的書架"); document.querySelector("#platform-login-button")?.addEventListener("click", loginPrompt); return; }
  const params = new URLSearchParams(query || "");
  const favoritePage = Math.max(1, Number(params.get("favorite_page") || 1) || 1);
  const historyPage = Math.max(1, Number(params.get("history_page") || 1) || 1);
  const principalGeneration = platformState.principalGeneration;
  const requestGeneration = ++platformState.queryGeneration;
  const current = () => routeToken === platformState.routeGeneration && requestGeneration === platformState.queryGeneration && principalGeneration === platformState.principalGeneration;
  const pageQuery = (base, page) => page > 1 ? `${base}${base.includes("?") ? "&" : "?"}page=${page}&page_size=20` : base;
  pLayout(pState("loading", "正在載入書架", "正在整理收藏與閱讀紀錄。", "", "platform-result-loading"), "我的書架");
  try {
    const [favorites, history] = await Promise.all([
      pApi(pageQuery("/api/me/library?kind=favorite", favoritePage, "favorite_page")),
      pApi(pageQuery("/api/me/history", historyPage, "history_page")),
    ]);
    if (!current()) return;
    let application = null;
    let applicationLoadError = null;
    if (platformState.me.role === "reader") {
      try { application = (await pApi("/api/authors/application")).application || null; }
      catch (_) { applicationLoadError = true; }
    }
    if (!current()) return;
    const authorApply = platformState.me.role === "reader" ? `<section class="platform-section platform-apply"><div class="platform-section-head"><div><p class="platform-eyebrow">CREATE</p><h2>成為作者</h2></div></div>${applicationLoadError ? `<div class="platform-error"><h3>申請狀態暫時無法載入</h3><p>請稍後再試。</p></div>` : application?.status === "pending" ? '<p class="platform-note">作者申請審核中，通過後即可建立小說與章節。</p>' : `<p class="platform-note">${application?.status === "rejected" ? "申請已退回，修正資料後可以重新提交。" : "分享你擁有權利的作品，通過審核後即可建立小說與章節。"}</p><form id="platform-author-form"><input name="penName" required minlength="1" maxlength="80" placeholder="筆名"><textarea name="bio" maxlength="1000" placeholder="作者簡介"></textarea><label><input type="checkbox" name="rightsConfirmed" required> 我確認提交內容為原創、已取得授權或屬於公共版權。</label><button class="platform-button platform-button-primary">${application?.status === "rejected" ? "重新提交申請" : "提交作者申請"}</button></form>`}</section>` : "";
    const favoriteLink = (value) => { const next = new URLSearchParams(params); next.set("favorite_page", String(value)); return `#/shelf?${next}`; };
    const historyLink = (value) => { const next = new URLSearchParams(params); next.set("history_page", String(value)); return `#/shelf?${next}`; };
    pLayout(`${pPagedSection("收藏作品", favorites, favoriteLink, "收藏作品分頁")}${pPagedSection("最近閱讀", history, historyLink, "閱讀紀錄分頁")}${authorApply}`, "我的書架");
    document.querySelector("#platform-author-form")?.addEventListener("submit", async (event) => { event.preventDefault(); const form = event.currentTarget; const submit = form.querySelector("button[type=submit]"); if (submit?.disabled) return; const values = new FormData(form); if (submit) { submit.disabled = true; submit.textContent = "送出中…"; } try { await pApi("/api/authors/apply", { method: "POST", body: { penName: values.get("penName"), bio: values.get("bio"), rightsConfirmed: values.get("rightsConfirmed") === "on" } }); form.outerHTML = `<div class="platform-note">申請已送出，請等待管理員審核。</div>`; } catch (error) { if (submit) { submit.disabled = false; submit.textContent = "提交作者申請"; } NovelToast.toast(error.message, "error"); } });
  } catch (_) {
    if (!current()) return;
    pLayout(pState("error", "書架目前無法載入", "請稍後再試。", `<button class="platform-button" id="shelf-retry" type="button">重新載入</button>`, "platform-error"), "我的書架");
    document.querySelector("#shelf-retry")?.addEventListener("click", () => {
      if (current()) renderShelf(query, routeToken);
    });
  }
}

async function renderNotifications(query = "") {
  pShow();
  if (!platformState.me) { pLayout(`<div class="platform-empty platform-login-empty"><h2>登入後查看通知</h2><button class="platform-button platform-button-primary" id="platform-login-button">登入或註冊</button></div>`, "通知"); document.querySelector("#platform-login-button")?.addEventListener("click", loginPrompt); return; }
  const params = new URLSearchParams(query || "");
  const page = Math.max(1, Number(params.get("page") || 1));
  const filter = ["all", "unread", "read"].includes(params.get("filter")) ? params.get("filter") : "all";
  const category = params.get("category") || "";
  let result;
  try {
    result = await (window.StoryLingoNotifications?.listCenter
      ? window.StoryLingoNotifications.listCenter({ page, filter, category })
      : pApi(`/api/notifications?page=${page}&page_size=20&filter=${encodeURIComponent(filter)}&category=${encodeURIComponent(category)}`));
  } catch (e) {
    pLayout(`<div class="platform-error"><h2>通知目前無法載入</h2><p>請稍後再試。</p></div>`, "通知"); return;
  }
  const items = result?.items || [];
  const totalPages = result?.total_pages ?? result?.totalPages ?? 0;
  const unreadCount = Number(result?.unread || 0);
  const filterLink = (value) => `#/notifications?filter=${value}${category ? `&category=${encodeURIComponent(category)}` : ""}`;
  const itemMarkup = items.map((n) => {
    const read = n.readAt || n.read_at;
    const route = n.targetRoute || n.link || "";
    return `<li class="${read ? "read" : "unread"}" data-notification-row="${pEsc(n.id)}"><div class="platform-notice-main"><span class="platform-notice-type">${pEsc(n.eventType || n.kind || "系統")}</span><strong>${pEsc(n.title || "")}</strong>${n.body ? `<p>${pEsc(n.body)}</p>` : ""}<small>${pEsc(n.createdAt || n.created_at || "")}</small></div><div class="platform-notice-actions">${route ? `<a class="platform-button platform-button-small" data-notification-target="${pEsc(route)}" href="${pEsc(route)}">開啟</a>` : `<span class="platform-note">此內容目前無法存取</span>`}${read ? `<span class="platform-note">已讀</span>` : `<button class="platform-button platform-button-small" data-notification-read="${pEsc(n.id)}" type="button">標為已讀</button>`}</div></li>`;
  }).join("");
  const pageLink = (value) => `#/notifications?page=${value}&filter=${filter}${category ? `&category=${encodeURIComponent(category)}` : ""}`;
  pLayout(`<div class="platform-section notification-center"><div class="platform-section-head"><div><p class="platform-eyebrow">INBOX</p><h2>通知</h2></div>${unreadCount ? '<button class="platform-button platform-button-small" id="notifications-read-all" type="button">全部標為已讀</button>' : ""}</div><div class="notification-filters" role="tablist" aria-label="通知篩選"><a class="${filter === "all" ? "active" : ""}" href="${filterLink("all")}">全部</a><a class="${filter === "unread" ? "active" : ""}" href="${filterLink("unread")}">未讀</a><a class="${filter === "read" ? "active" : ""}" href="${filterLink("read")}">已讀</a></div>${items.length ? `<ul class="platform-notice-list">${itemMarkup}</ul>` : `<div class="platform-empty">目前沒有符合條件的通知</div>`}${totalPages > 1 ? `<nav class="notification-pagination" aria-label="通知分頁"><span>第 ${page} / ${totalPages} 頁</span>${page > 1 ? `<a class="platform-button platform-button-small" href="${pageLink(page - 1)}">上一頁</a>` : ""}${page < totalPages ? `<a class="platform-button platform-button-small" href="${pageLink(page + 1)}">下一頁</a>` : ""}</nav>` : ""}</div>`, "通知");
  document.querySelector("#notifications-read-all")?.addEventListener("click", async (event) => {
    const button = event.currentTarget; button.disabled = true;
    try { await window.StoryLingoNotifications.markAllRead(); await renderNotifications(query); }
    catch (_) { button.disabled = false; NovelToast.toast("目前無法更新通知狀態", "error"); }
  });
  document.querySelectorAll("[data-notification-read]").forEach((button) => button.addEventListener("click", async () => {
    button.disabled = true;
      try { await window.StoryLingoNotifications.markRead(button.dataset.notificationRead, true); button.closest("li")?.classList.replace("unread", "read"); button.replaceWith(Object.assign(document.createElement("span"), { className: "platform-note", textContent: "已讀" })); }
    catch (_) { button.disabled = false; NovelToast.toast("目前無法更新通知狀態", "error"); }
  }));
  document.querySelectorAll("[data-notification-target]").forEach((link) => link.addEventListener("click", async (event) => {
    const row = link.closest("li"); const unread = row?.classList.contains("unread");
    if (!unread) return;
    event.preventDefault();
    const id = row?.dataset.notificationRow;
    const navigation = window.StoryLingoNotifications?.captureNavigation?.() || { hash: location.hash };
    try {
      await window.StoryLingoNotifications.markRead(id, true);
      row.classList.replace("unread", "read");
      const route = link.getAttribute("href");
      if (window.StoryLingoNotifications?.navigateToTarget) {
        window.StoryLingoNotifications.navigateToTarget(route, navigation);
      } else if (typeof route === "string" && route.startsWith("#/") && route.length <= 500
        && !route.includes("//") && !route.includes("\\")
        && !/[\u0000-\u001f]/.test(route) && location.hash === navigation.hash) {
        location.hash = route.slice(1);
      }
    } catch (_) { NovelToast.toast("此通知目前無法開啟", "error"); }
  }));
}

function renderPolicy(kind) {
  const policies = {
    terms: ["服務條款", "TERMS OF SERVICE", `<h3>1. 服務內容</h3><p>語閱 StoryLingo 提供公開小說瀏覽、閱讀、閱讀進度同步、收藏、留言、通知，以及依作品狀態提供的有聲朗讀與語言學習功能。部分功能可能因維護、容量或外部服務狀態而暫時無法使用。</p><h3>2. 帳號安全</h3><p>請使用不冒用他人的帳號名稱，並妥善保管登入資訊。不得冒用他人、繞過權限、探測或干擾服務。若發現帳號遭未授權使用，請儘速透過頁尾公布的問題回報管道通知平台。</p><h3>3. 作品與留言</h3><p>上傳作品、章節、封面、作者資料或留言時，你應確認自己擁有必要權利或已取得授權，不得提交侵權、違法、惡意程式、個資、仇恨、騷擾或詐騙內容。作者仍對其提交內容負責；平台可依審核結果、使用規範或安全需要限制公開。</p><h3>4. 審核、發布與所有權</h3><p>作品發布、下架、有聲書申請與所有權轉移依平台流程處理。送出申請不代表一定核准；平台會以目前可驗證的作品、帳號與權限狀態作決定，並保留必要的流程紀錄。</p><h3>5. AI 與語音服務</h3><p>部分分析或語音由管理者設定的服務處理，結果可能延遲、失敗或需要重試。語音可用性不代表作品內容、翻譯或朗讀結果的絕對正確；不得把平台的生成結果當作專業、法律或醫療建議。</p><h3>6. 服務調整與問題回報</h3><p>平台可能為修補安全問題、改善功能或因外部服務狀態調整服務。對條款或功能有疑問，請使用網站頁尾列出的問題回報方式聯絡；實際營運資訊更新時，平台會同步修訂相關頁面。</p>`],
    privacy: ["隱私權政策", "PRIVACY POLICY", `<h3>1. 收集的資料類型</h3><p>平台可能保存帳號名稱、電子郵件、密碼雜湊、帳號狀態、登入與驗證狀態；若使用 OAuth，也會保存供帳號連結所需的提供者識別資料。你提交的公開個人資料、作者資料、作品、章節、封面與留言也會依功能保存。</p><p>當你使用服務時，平台還可能保存收藏、書架、追蹤、閱讀進度、閱讀歷史、書籤、通知、內容申請、審核與所有權流程紀錄，以及分析／語音生成所需的工作狀態與結果識別資料。</p><h3>2. 安全與技術紀錄</h3><p>為了登入驗證、濫用防護與安全調查，部分驗證請求會保存請求 IP；公開閱讀事件會使用由請求來源資訊產生的雜湊識別值協助統計，並不把這些資訊作為公開個人資料。密碼只保存雜湊結果，驗證與重設流程的 token 只保存受保護的摘要，不保存可直接使用的明文 token。</p><h3>3. 使用目的</h3><p>資料用於提供登入、帳號與作者身份、作品管理、內容審核、所有權流程、閱讀與聆聽進度、通知、服務統計、錯誤排查與安全防護。平台不會把私人流程或安全資料放入公開作品與作者頁面的顯示資料。</p><h3>4. 外部服務與生成處理</h3><p>若作品啟用遠端 AI 或 TTS，為完成分析或語音產生所需的文字與必要設定可能傳送給管理者選定的服務。帳號驗證、密碼重設或 email change 的必要通知，也可能透過管理者選定的 transactional mail provider 寄送；目前實際 provider、所在地、跨境傳輸、保存期限與正式營運聯絡資訊，應以服務正式上線時的設定與適用法規補充確認。</p><h3>5. 保存與刪除</h3><p>資料會依提供服務、流程追蹤、法律要求與安全防護需要保存；本基本頁面不臆定未經確認的固定天數。你可以透過頁尾問題回報管道提出查詢、更正、停止利用或刪除請求，平台會依適用法規與實際資料狀況回覆。</p><h3>6. 公開資訊與變更</h3><p>公開作者資料、作品與留言可能對其他訪客顯示；帳號安全資料、私人通知與審核細節不應透過公開頁面顯示。平台更新實際資料流程時，會同步修訂本政策版本。</p>`],
    community: ["社群與內容規範", "COMMUNITY GUIDELINES", `<h3>作者上傳責任</h3><p>只可提交自己創作、已取得授權或屬於公共領域的內容；不得上傳侵權、惡意程式、個資、仇恨、騷擾或違法內容。</p><h3>讀者留言</h3><p>請尊重作者與其他讀者，不得洗版、冒充、散播個資、廣告詐騙或規避審核。</p><h3>檢舉與處理</h3><p>平台會保留必要的審核紀錄，並可暫停公開、要求補件、刪除內容或限制帳號。正式上線前應補上聯絡信箱、侵權通知/下架流程與處理時限。</p>`]
  };
  const [title, eyebrow, body] = policies[kind] || policies.terms;
  pShow();
  pLayout(`<article class="policy-page"><a class="platform-button" href="#/home">← 返回平台</a><p class="platform-eyebrow">${eyebrow}</p><h2>${title}</h2><p class="policy-version">版本：2026-09-04</p><div class="policy-content">${body}</div></article>`, title);
}

async function renderAuthor(slug, query = "", routeToken = platformState.routeGeneration) {
  pShow();
  pLayout(pState("loading", "載入作者資料", "正在整理作者的公開作品。", "", "platform-loading"), "作者");
  const params = new URLSearchParams(query || "");
  const page = Math.max(1, Number(params.get("page") || 1) || 1);
  const requestGeneration = ++platformState.queryGeneration;
  const current = () => routeToken === platformState.routeGeneration && requestGeneration === platformState.queryGeneration;
  try {
    const path = "/api/authors/" + encodeURIComponent(slug) + (page > 1 ? `?page=${page}&page_size=20` : "");
    const data = await pApi(path);
    if (!current()) return;
    const author = data.author || {};
    const avatar = author.avatar
      ? '<img class="author-profile-avatar" src="' + pEsc(author.avatar) + '" alt="' + pEsc(author.displayName || "作者") + '">'
      : '<div class="author-profile-avatar author-profile-avatar-fallback">' + pEsc((author.displayName || "作").slice(0, 1)) + "</div>";
     const status = author.status === "tombstone" ? '<span class="chip">作者帳號已結束</span>' : author.status === "suspended" ? '<span class="chip">作者暫停更新</span>' : "";
    const meta = window.StoryLingoPagination.normalize({ ...data, items: data.items || data.works || [] });
    const link = (value) => { const next = new URLSearchParams(params); next.set("page", String(value)); return `#/author/${encodeURIComponent(slug)}?${next}`; };
    pLayout('<section class="author-profile-hero"><div>' + avatar + '</div><div><p class="platform-eyebrow">AUTHOR PROFILE</p><h2>' + pEsc(author.displayName || "匿名作者") + '</h2>' + status + '<p class="author-profile-bio">' + pEsc(author.bio || "作者尚未提供簡介") + '</p></div></section>'
      + pPagedSection("公開作品", meta, link, "作者作品分頁"), "作者頁");
  } catch (_) {
    if (!current()) return;
    pLayout(pState("error", "無法開啟作者頁", "作者頁可能已結束，或目前暫時無法載入。", '<a class="platform-button" href="#/home">回到首頁</a>', "platform-error"), "作者");
  }
}

async function platformRoute() {
  disposePublicReader();
  const routeToken = ++platformState.routeGeneration;
  await loadMe();
  if (routeToken !== platformState.routeGeneration) return;
  const raw = (location.hash || "#/home").replace(/^#\/?/, "");
  const [path, query = ""] = raw.split("?");
  const parts = path.split("/");
  if (parts[0] === "policy") return renderPolicy(parts[1]);
  if (parts[0] === "privacy") return renderPolicy("privacy");
  if (!["home", "search", "category", "rankings", "audiobooks", "book", "read", "shelf", "notifications", "author"].includes(parts[0])) { pHide(); return; }
  if (parts[0] === "home") return renderHome(routeToken);
  if (parts[0] === "search") return renderSearch(raw, null, routeToken);
  if (parts[0] === "category") return renderSearch(raw, parts[1], routeToken);
  if (parts[0] === "rankings") return renderRankings();
  if (parts[0] === "audiobooks") return renderAudiobooks(raw, routeToken);
  if (parts[0] === "book" && parts[1]) return renderDetail(parts[1]);
  if (parts[0] === "read" && parts[1] && parts[2] != null) {
    const params = new URLSearchParams(query);
    return renderPublicReader(parts[1], parts[2], params.get("mode") || "", routeToken, params.get("autoplay") === "1");
  }
  if (parts[0] === "shelf") return renderShelf(query, routeToken);
  if (parts[0] === "notifications") return renderNotifications(query);
  if (parts[0] === "author") return renderAuthor(parts[1] || "__missing__", query, routeToken);
}

document.addEventListener("storylingo:auth-changed", () => {
  // Invalidate private collection responses immediately.  The next route
  // load obtains the canonical principal again before rendering any rows.
  platformState.authRequestGeneration += 1;
  platformState.principalGeneration += 1;
  platformState.principalKey = "";
  platformState.me = null;
  platformRoute();
});

window.addEventListener("hashchange", platformRoute);
window.addEventListener("DOMContentLoaded", platformRoute);
