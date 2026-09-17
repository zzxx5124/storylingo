import { test, expect } from "@playwright/test";

const respond = (route, data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function setup(page) {
  const state = { text: "原始章節內容。", reads: 0, writes: 0, fail: false, slow: false, conflict: false };
  const book = { id: "audit-book", title: "雨夜書店", ownerId: 7, category: "zh", status: "draft", settings: {},
    chapters: [{ seq: 0, title: "第一章", chars: 10, status: "pending", audio: "none" }, { seq: 1, title: "第二章", chars: 10, status: "pending", audio: "none" }] };
  await page.route("**/api/auth/me", (r) => respond(r, { authed: true, user: { id: 7, role: "author", username: "audit-author" } }));
  await page.route("**/api/voices", (r) => respond(r, { voices: [] }));
  await page.route("**/api/categories", (r) => respond(r, { categories: [] }));
  await page.route("**/api/books?*", (r) => respond(r, []));
  await page.route("**/api/books/audit-book", (r) => respond(r, book));
  await page.route("**/api/books/audit-book/chapters", async (r) => { state.writes++; await delay(350); return respond(r, book); });
  await page.route(/\/api\/books\/audit-book\/chapters\/[01]$/, async (r) => {
    const seq = Number(r.request().url().split("/").pop());
    if (r.request().method() === "PUT") {
      state.writes++;
      await delay(350);
      if (state.conflict) return respond(r, { detail: "章節已被修改" }, 409);
      state.text = r.request().postDataJSON().text;
      return respond(r, book);
    }
    state.reads++;
    const text = seq === 1 ? "第二章原文" : state.text;
    if (state.slow && seq === 0) await delay(600);
    if (state.fail) return respond(r, { detail: "讀取失敗" }, 500);
    return respond(r, { chapter: { seq, title: seq === 0 ? "第一章" : "第二章", text, chapterKey: `key-${seq}`, textHash: "hash" } });
  });
  await page.route("**/api/books/audit-book/chapters/0/revisions", (r) => respond(r, { revisions: [{ id: 1, chars: 6, created_at: "2026-09-01" }] }));
  await page.route("**/api/books/audit-book/chapters/1/revisions", (r) => respond(r, { revisions: [] }));
  await page.route("**/api/books/audit-book/chapters/0/revisions/1/restore", (r) => { state.text = "還原後的原文"; return respond(r, book); });
  await page.goto("/#/detail/audit-book");
  await expect(page.locator("[data-edit-ch='0']")).toBeVisible();
  return state;
}

async function setupMetadataEditor(page) {
  const state = {
    failLoad: false,
    conflict: false,
    writes: 0,
    book: {
      id: "metadata-book", bid: "metadata-book", title: "作品原名", synopsis: "原始簡介", tags: ["原始"],
      category: "zh", categoryId: null, serial: "連載", hasPrologue: true, languageTypeEditable: true,
      status: "draft", ownerId: 7, chapterCount: 1, audioReadyCount: 0, analyzedChapterCount: 0,
      created: "2026-09-01", updated: "v1", metadataHash: "hash-1",
    },
  };
  await page.route("**/api/auth/me", (r) => respond(r, { authed: true, user: { id: 7, role: "author", username: "metadata-author" } }));
  await page.route("**/api/voices", (r) => respond(r, { voices: [] }));
  await page.route("**/api/categories", (r) => respond(r, { categories: [] }));
  await page.route("**/api/books?*", (r) => respond(r, [state.book]));
  await page.route("**/api/requests*", (r) => respond(r, { items: [], total: 0, page: 1, page_size: 100, total_pages: 1 }));
  await page.route("**/api/books/metadata-book", async (r) => {
    if (r.request().method() === "PUT") {
      state.writes += 1;
      await delay(250);
      if (state.conflict) return respond(r, { detail: "作品資料已在其他分頁更新", code: "book_metadata_conflict" }, 409);
      const payload = r.request().postDataJSON() || {};
      state.book = { ...state.book, ...payload, updated: `v${state.writes + 1}`, metadataHash: `hash-${state.writes + 1}` };
      return respond(r, state.book);
    }
    if (state.failLoad) return respond(r, { detail: "作品載入失敗" }, 500);
    return respond(r, state.book);
  });
  await page.goto("/#/mine");
  await expect(page.locator('[data-medit="metadata-book"]')).toBeVisible();
  return state;
}

async function setupAuthorMineCard(page) {
  const book = {
    id: "mine-visual-book", bid: "mine-visual-book", title: "長夜行旅",
    synopsis: "一部漫長的旅程。", status: "draft", serial: "連載", ownerId: 7,
    chapterCount: 18, audioReadyCount: 5, analyzedChapterCount: 12,
    created: "2026-09-01", updated: "2026-09-05", coverImage: null, rejectReason: "",
    workflow: { nextAction: "generate" },
  };
  await page.route("**/api/auth/me", (r) => respond(r, { authed: true, user: { id: 7, role: "author", username: "mine-author" } }));
  await page.route("**/api/voices", (r) => respond(r, { voices: [] }));
  await page.route("**/api/categories", (r) => respond(r, { categories: [] }));
  await page.route(/\/api\/books(?:\?.*)?$/, (r) => {
    const url = new URL(r.request().url());
    return respond(r, url.searchParams.get("mine") === "1"
      ? { items: [book], page: 1, page_size: 20, total: 1, total_pages: 1, has_next: false, has_prev: false }
      : []);
  });
  await page.route("**/api/requests*", (r) => respond(r, { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 }));
  await page.goto("/#/mine");
  await expect(page.locator(".mine-card")).toBeVisible();
}

async function setupPublicLongDetail(page) {
  const chapters = Array.from({ length: 20 }, (_, seq) => ({
    seq, title: `第 ${seq + 1} 章`, status: "published", audio: seq === 0 ? "ready" : "none",
  }));
  const book = {
    id: "public-long", bid: "public-long", title: "長篇目錄測試", owner: "作者",
    synopsis: "用於驗證長篇目錄漸進揭露。", serial: "連載", totalChars: 20000,
    chapters, audiobook: { availability: "partial", playableChapterCount: 1, firstPlayableChapter: 0, playableChapterSeqs: [0] },
  };
  await page.route("**/api/auth/me", (r) => respond(r, { authed: false }));
  await page.route("**/api/books/public-long", (r) => respond(r, book));
  await page.route("**/api/books/public-long/recommendations", (r) => respond(r, { items: [] }));
  await page.route("**/api/books/public-long/comments", (r) => respond(r, { items: [] }));
  await page.goto("/#/book/public-long");
  await expect(page.locator("#platform-detail-title")).toHaveText("長篇目錄測試");
}

async function setupReadingFlow(page, { authed = false, progress = null } = {}) {
  const book = {
    id: "reading-flow-book", bid: "reading-flow-book", title: "閱讀流程測試書", owner: "作者",
    synopsis: "用於驗證開始、繼續與閱讀模式切換。", serial: "連載", totalChars: 1200,
    category: "bilingual", status: "approved",
    chapters: [
      { seq: 0, title: "第一章", status: "published", audio: "ready" },
      { seq: 1, title: "第二章", status: "published", audio: "ready" },
    ],
    audiobook: { availability: "available", playableChapterCount: 2, firstPlayableChapter: 0, playableChapterSeqs: [0, 1] },
  };
  await page.route("**/api/auth/me", (r) => respond(r, authed
    ? { authed: true, user: { id: 9, role: "reader", username: "flow-reader" } }
    : { authed: false }));
  await page.route("**/api/books/reading-flow-book", (r) => respond(r, book));
  await page.route("**/api/books/reading-flow-book/recommendations", (r) => respond(r, { items: [] }));
  await page.route("**/api/books/reading-flow-book/comments", (r) => respond(r, { items: [] }));
  await page.route("**/api/me/progress", (r) => {
    if (r.request().method() === "PUT") return respond(r, { ok: true });
    return respond(r, { items: progress ? [{ book_id: "reading-flow-book", ...progress }] : [] });
  });
  await page.route(/\/api\/books\/reading-flow-book\/read\/[01]$/, (r) => {
    const seq = Number(r.request().url().split("/").pop());
    return respond(r, {
      book: { id: "reading-flow-book", title: book.title, category: "bilingual" },
      chapter: { seq, title: book.chapters[seq].title, text: `第 ${seq + 1} 章正文。`, chars: 10, audio: "ready" },
      navigation: { previous: seq > 0 ? seq - 1 : null, next: seq < 1 ? seq + 1 : null, total: 2 },
    });
  });
  await page.route(/\/api\/books\/reading-flow-book\/chapters\/[01]$/, (r) => respond(r, {
    analysis: { segments: [{ type: "bilingual", zh: "第一句。", en: "The first sentence." }] }, timing: null,
  }));
  await page.route(/\/api\/books\/reading-flow-book\/audio\/[01]$/, (r) => r.fulfill({ status: 200, contentType: "audio/mpeg", body: Buffer.from("ID3" + "\x00".repeat(32)) }));
  await page.route("**/api/books/reading-flow-book/view", (r) => respond(r, { ok: true }));
}

test("原文載入中及載入失敗不可儲存，可重試", async ({ page }) => {
  const state = await setup(page);
  state.slow = true; state.fail = true;
  await page.locator("[data-edit-ch='0']").click();
  await expect(page.locator("#new-ch-submit")).toBeDisabled();
  await expect(page.locator("#chapter-editor-status")).toContainText("載入失敗");
  await expect(page.locator("#new-ch-submit")).toBeDisabled();
  state.fail = false;
  await page.locator("#chapter-editor-retry").click();
  await expect(page.locator("#new-ch-text")).toHaveValue("原始章節內容。");
  await expect(page.locator("#new-ch-submit")).toBeEnabled();
  expect(state.writes).toBe(0);
});

test("關閉後切換章節，舊回應不可覆寫新的編輯器", async ({ page }) => {
  const state = await setup(page); state.slow = true;
  await page.locator("[data-edit-ch='0']").click();
  await page.locator("#new-ch-cancel").click();
  await page.locator("[data-edit-ch='1']").click();
  await expect(page.locator("#new-ch-text")).toHaveValue("第二章原文");
  await delay(700);
  await expect(page.locator("#new-ch-text")).toHaveValue("第二章原文");
});

test("新增章節防重複提交且完整保留正文空白", async ({ page }) => {
  const state = await setup(page);
  await page.locator("#btn-add-chapter").click();
  await page.locator("#new-ch-text").fill("  新章節正文。\n\n");
  await page.locator("#new-ch-submit").dblclick();
  await expect(page.locator("#chapter-modal")).toBeHidden();
  expect(state.writes).toBe(1);
  await page.locator("[data-edit-ch='0']").click();
  await expect(page.locator("#new-ch-text")).toBeEnabled();
  await page.locator("#new-ch-text").fill("  原文空白。\n\n");
  await page.locator("#new-ch-submit").click();
  await expect(page.locator("#chapter-modal")).toBeHidden();
  expect(state.text).toBe("  原文空白。\n\n");
});

test("重新整理後找回草稿，衝突保留文字並可載入目前版本", async ({ page }) => {
  const state = await setup(page);
  await page.locator("[data-edit-ch='0']").click();
  await expect(page.locator("#new-ch-text")).toBeEnabled();
  await page.locator("#new-ch-text").fill("尚未儲存的珍貴文字");
  page.on("dialog", (dialog) => dialog.accept());
  await page.reload();
  await page.locator("[data-edit-ch='0']").click();
  await expect(page.locator("#new-ch-text")).toHaveValue("尚未儲存的珍貴文字");
  state.conflict = true;
  await page.locator("#new-ch-submit").click();
  await expect(page.locator("#chapter-editor-status")).toContainText("儲存失敗");
  await expect(page.locator("#new-ch-text")).toHaveValue("尚未儲存的珍貴文字");
  await page.locator("#chapter-draft-discard").click();
  await expect(page.locator("#new-ch-text")).toHaveValue("原始章節內容。");
});

test("版本還原重新讀取正文，桌面手機操作列均可達", async ({ page }, testInfo) => {
  await setup(page);
  await page.locator("[data-edit-ch='0']").click();
  await expect(page.locator("#new-ch-text")).toBeEnabled();
  await page.locator("#chapter-revisions summary").click();
  page.on("dialog", (dialog) => dialog.accept());
  await page.locator("[data-rev-restore='1']").click();
  await expect(page.locator("#new-ch-text")).toHaveValue("還原後的原文");
  await expect(page.locator("#new-ch-submit")).toBeEnabled();
  const box = await page.locator("#new-ch-submit").boundingBox();
  expect(box.y + box.height).toBeLessThanOrEqual(page.viewportSize().height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("chapter-editor.png") });
});

test("舊儲存請求完成不能清除稍後建立的新草稿", async ({ page }) => {
  await setup(page);
  let finishSave;
  const saving = new Promise((resolve) => { finishSave = resolve; });
  await page.route("**/api/books/audit-book/chapters/0", async (r) => {
    if (r.request().method() !== "PUT") return r.fallback();
    await saving;
    return respond(r, {});
  });
  await page.locator("[data-edit-ch='0']").click();
  await expect(page.locator("#new-ch-text")).toBeEnabled();
  await page.locator("#new-ch-text").fill("舊儲存的內容");
  await page.locator("#new-ch-submit").click();
  await expect(page.locator("#new-ch-submit")).toBeDisabled();
  await page.evaluate(() => { location.hash = "#/mine"; });
  await expect(page.locator("#chapter-modal")).toBeHidden();
  await page.evaluate(() => { location.hash = "#/detail/audit-book"; });
  await page.locator("[data-edit-ch='0']").click();
  await expect(page.locator("#new-ch-text")).toBeEnabled();
  await page.locator("#new-ch-text").fill("之後輸入的新草稿");
  finishSave();
  await delay(300);
  page.on("dialog", (dialog) => dialog.accept());
  await page.reload();
  await page.locator("[data-edit-ch='0']").click();
  await expect(page.locator("#new-ch-text")).toHaveValue("之後輸入的新草稿");
});

test("作品慢回應不可拉回已離開的頁面", async ({ page }) => {
  await setup(page);
  await page.route("**/api/books/slow-book", async (r) => { await delay(600); return respond(r, { id: "slow-book", title: "過期作品", ownerId: 7, chapters: [] }); });
  await page.evaluate(() => { location.hash = "#/detail/slow-book"; });
  await delay(100);
  await page.evaluate(() => { location.hash = "#/mine"; });
  await expect(page.locator("#view-mine")).toBeVisible();
  await delay(700);
  await expect(page.locator("#view-mine")).toBeVisible();
  await expect(page.locator("#view-chapters")).toBeHidden();
});

test("排行榜快速切換以最後一次選擇為準，失敗可原地重試", async ({ page }) => {
  await setup(page);
  let failed = true;
  await page.route("**/api/rankings?*", async (r) => {
    const kind = new URL(r.request().url()).searchParams.get("kind");
    if (kind === "hot") await delay(600);
    if (kind === "new" && failed) return respond(r, {}, 500);
    return respond(r, { items: [{ bid: kind, title: kind === "hot" ? "舊人氣排行" : "最新排行" }] });
  });
  await page.goto("/#/rankings");
  await page.locator("[data-kind='new']").click();
  await expect(page.locator("#ranking-retry")).toBeVisible();
  failed = false;
  await page.locator("#ranking-retry").click();
  await expect(page.locator("#platform-ranking-result")).toContainText("最新排行");
  await delay(700);
  await expect(page.locator("#platform-ranking-result")).not.toContainText("舊人氣排行");
});

test("公開作品慢回應不會重導使用者", async ({ page }) => {
  await setup(page);
  await page.route("**/api/books/slow-public", async (r) => { await delay(600); return respond(r, { id: "slow-public", title: "過期公開作品", chapters: [] }); });
  await page.goto("/#/book/slow-public");
  if (await page.locator("#nav-toggle").isVisible()) await page.locator("#nav-toggle").click();
  await page.locator("#main-nav a[href='#/search']").click();
  await delay(700);
  await expect(page.locator("#platform-root")).not.toContainText("過期公開作品");
});

test("空目錄不提供無效閱讀連結，收藏與留言只送出一次", async ({ page }) => {
  await setup(page);
  await page.route("**/api/books/public-book", (r) => respond(r, { id: "public-book", title: "尚待更新的作品", favorite: true, chapters: [] }));
  await page.route("**/api/books/public-book/recommendations", (r) => respond(r, { items: [] }));
  let favorites = 0;
  let comments = 0;
  await page.route("**/api/books/public-book/favorite", async (r) => { favorites++; await delay(400); return respond(r, { ok: true }); });
  await page.route("**/api/books/public-book/comments", async (r) => {
    if (r.request().method() === "POST") { comments++; await delay(400); }
    return respond(r, { items: [] });
  });
  await page.goto("/#/book/public-book");
  await expect(page.getByText("尚無公開章節", { exact: true })).toBeVisible();
  await expect(page.locator("#platform-root a[href*='/read/']")).toHaveCount(0);
  await expect(page.locator("#platform-favorite")).toHaveText("已收藏");
  await page.locator("#platform-favorite").dblclick();
  await expect(page.locator("#platform-favorite")).toHaveText("加入收藏");
  expect(favorites).toBe(1);
  await page.locator("#platform-comment-form textarea").fill("期待後續更新");
  await page.locator("#platform-comment-form button").dblclick();
  await expect(page.locator("#platform-comment-form textarea")).toHaveValue("");
  expect(comments).toBe(1);
});

test("首末章導覽正確說明目的地", async ({ page }) => {
  await setup(page);
  await page.route("**/api/books/audit-book/read/0", (r) => respond(r, {
    book: { id: "audit-book", title: "單章作品", category: "zh" },
    chapter: { seq: 0, title: "唯一章節", text: "故事到此結束。", audio: "none" },
    navigation: { previous: null, next: null, total: 1 },
  }));
  await page.goto("/#/read/audit-book/0?mode=reading");
  await expect(page.locator(".reader-footer").getByRole("link", { name: "返回目錄" })).toHaveAttribute("href", "#/book/audit-book");
  await expect(page.locator(".reader-footer").getByRole("link", { name: "返回作品" })).toHaveAttribute("href", "#/book/audit-book");
  await expect(page.locator(".reader-footer").getByRole("link", { name: "下一章" })).toHaveCount(0);
});

test("作品 metadata 載入失敗不可儲存，可重試", async ({ page }) => {
  const state = await setupMetadataEditor(page);
  state.failLoad = true;
  await page.locator('[data-medit="metadata-book"]').click();
  await expect(page.locator("#be-submit")).toBeDisabled();
  await expect(page.locator("#book-editor-status")).toContainText("載入失敗");
  state.failLoad = false;
  await page.locator("#book-editor-retry").click();
  await expect(page.locator("#be-submit")).toBeEnabled();
  expect(state.writes).toBe(0);
});

test("作品 metadata 防止重複提交，重新整理後找回草稿", async ({ page }) => {
  const state = await setupMetadataEditor(page);
  await page.locator('[data-medit="metadata-book"]').click();
  await page.locator("#be-title").fill("尚未儲存的新書名");
  await page.locator("#be-submit").dblclick();
  await expect(page.locator("#book-edit-modal")).toBeHidden();
  expect(state.writes).toBe(1);

  await page.locator('[data-medit="metadata-book"]').click();
  await page.locator("#be-title").fill("重新整理仍要保留");
  page.on("dialog", (dialog) => dialog.accept());
  await page.reload();
  await page.locator('[data-medit="metadata-book"]').click();
  await expect(page.locator("#be-title")).toHaveValue("重新整理仍要保留");
});

test("作品 metadata 衝突保留輸入，可明確載入目前版本", async ({ page }) => {
  const state = await setupMetadataEditor(page);
  await page.locator('[data-medit="metadata-book"]').click();
  await page.locator("#be-title").fill("我的修改");
  state.conflict = true;
  await page.locator("#be-submit").click();
  await expect(page.locator("#book-editor-status")).toContainText("其他分頁更新");
  await expect(page.locator("#be-title")).toHaveValue("我的修改");
  state.conflict = false;
  page.on("dialog", (dialog) => dialog.accept());
  await page.locator("#book-editor-retry").click();
  await expect(page.locator("#be-title")).toHaveValue("作品原名");
});

test("作品 metadata 慢儲存完成後不會拉回已離開的頁面", async ({ page }) => {
  const state = await setupMetadataEditor(page);
  await page.locator('[data-medit="metadata-book"]').click();
  await page.locator("#be-title").fill("離開前的修改");
  await page.locator("#be-submit").click();
  await expect(page.locator("#be-submit")).toBeDisabled();
  await page.evaluate(() => { location.hash = "#/home"; });
  await expect(page.locator("#book-edit-modal")).toBeHidden();
  await delay(400);
  await expect(page.locator("#platform-root")).toBeVisible();
  expect(state.writes).toBe(1);
});

test("作者工作台作品卡在桌面與手機保持資訊層級，低頻操作可展開", async ({ page }) => {
  await setupAuthorMineCard(page);
  await expect(page.locator(".mine-next-step")).toContainText("下一步");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const infoBox = await page.locator(".mine-card .book-info").boundingBox();
  expect(infoBox?.width || 0).toBeGreaterThan(100);
  await expect(page.locator(".mine-more-menu [data-mstats]")).toBeHidden();
  await page.locator(".mine-more-actions summary").click();
  await expect(page.locator(".mine-more-menu [data-mstats]")).toBeVisible();
  await page.reload();
  await expect(page.locator(".mine-next-step")).toContainText("下一步");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("長篇作品先顯示前段章節，完整目錄可展開且重新整理仍可直接閱讀", async ({ page }) => {
  await setupPublicLongDetail(page);
  await expect(page.locator(".platform-chapter-list:not(.platform-chapter-list-extra) > a")).toHaveCount(12);
  await expect(page.locator(".platform-chapters-disclosure summary")).toContainText("查看其餘 8 章");
  await expect(page.locator(".platform-chapter-list-extra")).toBeHidden();
  await page.locator(".platform-chapters-disclosure summary").click();
  await expect(page.locator(".platform-chapter-list-extra > a")).toHaveCount(8);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.reload();
  await expect(page.locator(".platform-chapter-list:not(.platform-chapter-list-extra) > a")).toHaveCount(12);
  await expect(page.locator(".platform-chapter-list-extra")).toBeHidden();
});

test("首次進入詳情以開始閱讀為主，Reader 內可切換純文字與聽書", async ({ page }) => {
  await setupReadingFlow(page);
  await page.goto("/#/book/reading-flow-book");
  await expect(page.locator(".reading-start-actions a", { hasText: "開始閱讀" })).toHaveAttribute("href", "#/read/reading-flow-book/0?mode=reading");
  await expect(page.locator(".reading-start-actions a", { hasText: "聽書跟讀" })).toHaveAttribute("href", "#/read/reading-flow-book/0?mode=listen");
  await expect(page.getByRole("link", { name: "閱讀＋學習" })).toHaveCount(0);
  await expect(page.locator(".reading-choice-card")).toHaveCount(0);

  await page.locator(".reading-start-actions a", { hasText: "開始閱讀" }).click();
  await expect(page.locator(".reader-mode-switcher [data-reader-mode='reading']")).toHaveClass(/active/);
  await expect(page.locator(".reader-mode-switcher [data-reader-mode='listen']")).toBeVisible();
  await expect(page.locator(".reader-top a")).toHaveText("← 返回作品目錄");
  await page.locator(".reader-mode-switcher [data-reader-mode='listen']").click();
  await expect(page).toHaveURL(/read\/reading-flow-book\/0\?mode=listen$/);
  await expect(page.locator("#reader-audio")).toBeVisible();
  await page.locator(".reader-mode-switcher [data-reader-mode='reading']").click();
  await expect(page).toHaveURL(/read\/reading-flow-book\/0\?mode=reading$/);
  await page.reload();
  await expect(page.locator(".reader-mode-switcher [data-reader-mode='reading']")).toHaveClass(/active/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("已有登入閱讀進度時優先繼續閱讀，仍可從第一章開始", async ({ page }) => {
  await setupReadingFlow(page, { authed: true, progress: { chapter_seq: 1, percent: 42, position: 120, last_mode: "listen" } });
  await page.goto("/#/book/reading-flow-book");
  await expect(page.locator(".reading-start-actions a", { hasText: "繼續閱讀" })).toHaveAttribute("href", "#/read/reading-flow-book/1?mode=listen");
  await expect(page.locator(".reading-start-copy")).toContainText("第二章");
  await expect(page.locator(".reading-start-actions a", { hasText: "從第一章開始" })).toHaveAttribute("href", "#/read/reading-flow-book/0?mode=reading");
  await expect(page.locator(".reading-start-actions a", { hasText: "聽書跟讀" })).toHaveCount(0);
  await page.locator(".reading-start-actions a", { hasText: "繼續閱讀" }).click();
  await expect(page.locator(".reader-sheet h2")).toHaveText("第二章");
  await expect(page.locator(".reader-mode-switcher [data-reader-mode='listen']")).toHaveClass(/active/);
  await page.locator(".reader-top a").click();
  await expect(page.locator(".reading-start-actions a", { hasText: "繼續閱讀" })).toBeVisible();
  await page.reload();
  await expect(page.locator(".reading-start-actions a", { hasText: "繼續閱讀" })).toBeVisible();
});
