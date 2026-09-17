import { test, expect } from "@playwright/test";

test.describe("公開小說平台", () => {
  test("訪客可以瀏覽首頁並搜尋", async ({ page }) => {
    await page.goto("/#/home");
    await expect(page.locator("#view-platform")).toBeVisible();
    await expect(page.getByRole("heading", { name: "探索分類" })).toBeVisible();
    await page.goto("/#/search?q=不存在的作品");
    await expect(page.locator(".platform-result-head, .platform-empty").first()).toBeVisible();
  });

  test("搜尋 query 刷新後仍停留在搜尋頁，隱私別名顯示政策頁", async ({ page }) => {
    await page.goto("/#/search?q=不存在的作品");
    await page.reload();
    await expect(page.getByRole("heading", { name: "搜尋小說" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "我的書架" })).toHaveCount(0);
    await page.goto("/#/privacy");
    await expect(page.locator(".policy-page").getByRole("heading", { name: "隱私權政策" })).toBeVisible();
  });

  test("訪客可從頁尾進入服務條款與隱私權政策，窄版仍可閱讀", async ({ page }) => {
    await page.goto("/#/home");
    await page.locator('.site-footer a[href="#/policy"]').click();
    await expect(page.locator(".policy-page").getByRole("heading", { name: "服務條款" })).toBeVisible();
    await expect(page.locator(".policy-content h3")).toHaveCount(6);
    await page.locator('.site-footer a[href="#/policy/privacy"]').click();
    await expect(page.locator(".policy-page").getByRole("heading", { name: "隱私權政策" })).toBeVisible();
    await expect(page.locator(".policy-content h3")).toHaveCount(6);
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/#/policy");
    await expect(page.locator(".policy-page")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)).toBe(false);
  });

  test("訪客可進入排行榜與手機版底部流程", async ({ page }) => {
    await page.goto("/#/rankings");
    await expect(page.getByRole("heading", { name: "排行榜" })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/#/home");
    await expect(page.locator(".home-intro")).toBeVisible();
  });

  test("通知頁可渲染（登入提示或通知清單）", async ({ page }) => {
    await page.goto("/#/notifications");
    await expect(page.locator(".platform-page")).toBeVisible();
    // 訪客看到「登入後查看通知」；已登入則看到通知清單或空狀態
    await expect(page.locator(".platform-login-empty, .platform-notice-list, .platform-empty").first()).toBeVisible();
  });

  test("訪客點收藏／追蹤會明確提示登入", async ({ page }) => {
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    await page.route("**/api/auth/me", (route) => fulfill(route, { authed: false }));
    await page.route("**/api/books/demo/recommendations", (route) => fulfill(route, { items: [] }));
    await page.route("**/api/books/demo/comments", (route) => fulfill(route, { items: [] }));
    await page.route("**/api/books/demo", (route) => fulfill(route, {
      id: "demo", bid: "demo", title: "收藏測試書", synopsis: "", serial: "連載",
      owner: "作者", totalChars: 10, status: "approved", chapters: [],
    }));
    await page.goto("/#/book/demo");
    await page.locator("#platform-favorite").click();
    await expect(page.locator(".toast-info").last()).toContainText("請先登入");
    await expect(page.locator("#login-modal")).toBeVisible();
    await page.locator("#btn-login-cancel").click();
    await page.locator("#platform-follow").click();
    await expect(page.locator(".toast-info").last()).toContainText("請先登入");
    await expect(page.locator("#login-modal")).toBeVisible();
  });

  test("作品詳情以單一作品標題作為頁面主標題", async ({ page }) => {
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    await page.route("**/api/auth/me", (route) => fulfill(route, { authed: false }));
    await page.route("**/api/books/detail-heading/recommendations", (route) => fulfill(route, { items: [] }));
    await page.route("**/api/books/detail-heading/comments", (route) => fulfill(route, { items: [] }));
    await page.route("**/api/books/detail-heading", (route) => fulfill(route, {
      id: "detail-heading", bid: "detail-heading", title: "只出現一次的作品名", synopsis: "作品簡介", serial: "連載",
      owner: "作者", totalChars: 20, status: "approved", chapters: [],
    }));
    await page.goto("/#/book/detail-heading");
    await expect(page.locator(".platform-detail-title")).toHaveCount(1);
    await expect(page.locator(".platform-page > .foundation-page-header")).toHaveCount(0);
    await expect(page.locator(".platform-page h1")).toHaveCount(1);
  });

  test("閱讀器可切換學習模式並顯示單字與雙語資料", async ({ page }) => {
    await page.route("**/api/auth/me", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ authed: false }) }));
    await page.route("**/api/books/demo/read/0", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      book: { id: "demo", title: "測試作品", category: "vocab", categories: ["vocab"] },
      chapter: { seq: 0, title: "序章", text: "這是一段測試正文。", chars: 10, audio: "ready" },
      navigation: { previous: null, next: null, total: 1 },
    }) }));
    await page.route("**/api/books/demo/chapters/0", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      analysis: { segments: [
        { type: "vocab", vocab: { en: "book", zh: "書", level: "A1" } },
        { type: "bilingual", zh: "這是一本書。", en: "This is a book." },
      ] }, timing: null,
    }) }));
    await page.route("**/api/books/demo/view", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) }));
    await page.goto("/#/read/demo/0");
    await expect(page.locator("#reader-body")).toContainText("測試正文");
    await expect(page.locator("#reader-study")).toBeHidden();
    await page.locator("#reader-mode-toggle").click();
    await expect(page.locator("#reader-study")).toBeVisible();
    await expect(page.locator(".reader-vocab-card")).toContainText("book");
    await expect(page.locator(".reader-bilingual-list")).toContainText("This is a book.");
    await expect(page.locator("audio")).toBeVisible();
  });

  test("閱讀器沒有學習資料時維持純閱讀", async ({ page }) => {
    await page.route("**/api/auth/me", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ authed: false }) }));
    await page.route("**/api/books/demo/read/0", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      book: { id: "demo", title: "尚無學習資料", category: "vocab", categories: ["vocab"] },
      chapter: { seq: 0, title: "序章", text: "這是一段純閱讀正文。", chars: 10, audio: "none" },
      navigation: { previous: null, next: null, total: 1 },
    }) }));
    await page.route("**/api/books/demo/chapters/0", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ analysis: { segments: [] }, timing: null }) }));
    await page.route("**/api/books/demo/view", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) }));
    await page.goto("/#/read/demo/0");
    await expect(page.locator("#reader-body")).toContainText("純閱讀正文");
    await expect(page.locator("#reader-mode-toggle")).toHaveCount(0);
    await expect(page.locator("#reader-study")).toBeHidden();
  });

  test("作品詳情只保留一個開始決策，其他閱讀方式在 Reader 可切換", async ({ page }) => {
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    await page.route("**/api/auth/me", (route) => fulfill(route, { authed: false }));
    await page.route("**/api/books/demo/recommendations", (route) => fulfill(route, { items: [] }));
    await page.route("**/api/books/demo/comments", (route) => fulfill(route, { items: [] }));
    await page.route("**/api/books/demo", (route) => fulfill(route, {
      id: "demo", bid: "demo", title: "模式測試書", synopsis: "", serial: "連載",
      owner: "作者", totalChars: 10, status: "approved", category: "zh", categories: ["zh"],
      chapters: [{ id: 1, seq: 0, title: "序章", status: "analyzed", audio: "ready", publishStatus: "published" }],
    }));
    await page.goto("/#/book/demo");
    await expect(page.locator(".reading-start-panel a", { hasText: "開始閱讀" })).toHaveAttribute("href", "#/read/demo/0?mode=reading");
    await expect(page.locator(".reading-start-panel a", { hasText: "聽書跟讀" })).toHaveAttribute("href", "#/read/demo/0?mode=listen");
    await expect(page.locator(".reading-choice-card")).toHaveCount(0);
  });

  test("聽書跟讀使用 canonical segments 同步正文且不顯示聲線卡片", async ({ page }) => {
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    await page.route("**/api/auth/me", (route) => fulfill(route, { authed: false }));
    await page.route("**/api/books/demo/read/0", (route) => fulfill(route, {
      book: { id: "demo", title: "同步測試書", category: "zh" },
      chapter: { seq: 0, title: "序章", text: "第一句。\n第二句。", chars: 6, audio: "ready" },
      navigation: { previous: null, next: null, total: 1 },
    }));
    await page.route("**/api/books/demo/chapters/0", (route) => fulfill(route, {
      analysis: { segments: [
        { type: "narration", text: "第一句。", speaker: "旁白" },
        { type: "dialogue", text: "第二句。", speaker: "甲" },
      ] },
      timing: { segments: [{ dur: 1 }, { dur: 2 }] },
    }));
    await page.route("**/api/books/demo/audio/0", (route) => route.fulfill({ status: 200, contentType: "audio/mpeg", body: Buffer.from("ID3" + "\x00".repeat(256)) }));
    await page.route("**/api/books/demo/view", (route) => fulfill(route, { ok: true }));
    await page.goto("/#/read/demo/0?mode=listen");
    await expect(page.locator(".reader-transcript-segment")).toHaveCount(2);
    await expect(page.locator(".reader-transcript-segment").first()).toContainText("第一句。");
    await expect(page.locator("#reader-audio")).toBeVisible();
    await expect(page.locator("#reader-speed")).toBeVisible();
    await expect(page.locator("#reader-study")).toHaveCount(1);
    await page.evaluate(() => {
      const audio = document.querySelector("#reader-audio");
      Object.defineProperty(audio, "currentTime", { configurable: true, value: 1.2 });
      audio.dispatchEvent(new Event("timeupdate"));
    });
    await expect(page.locator('.reader-transcript-segment[data-reader-index="1"]')).toHaveClass(/active/);
    await expect(page.locator("#reader-body")).not.toContainText("角色聲線");
  });

  test("舊分段版本顯示目前來源句子且不假裝可同步舊音訊", async ({ page }) => {
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    await page.route("**/api/auth/me", (route) => fulfill(route, { authed: false }));
    await page.route("**/api/books/demo/read/0", (route) => fulfill(route, {
      book: { id: "demo", title: "英文來源測試書", category: "en" },
      chapter: { seq: 0, title: "Chapter 1", text: "Alice looked at the sky. Bob smiled.", chars: 37, audio: "ready" },
      navigation: { previous: null, next: null, total: 1 },
    }));
    await page.route("**/api/books/demo/chapters/0", (route) => fulfill(route, {
      analysis: {
        status: "stale",
        staleReason: "segmentation_version_outdated",
        segmentationVersion: "paragraph-quote-sentence-v2",
        segments: [
          { type: "narration", text: "Alice looked at the sky.", speaker_id: "speaker:narrator" },
          { type: "narration", text: "Bob smiled.", speaker_id: "speaker:narrator" },
        ],
      },
      timing: null,
    }));
    await page.route("**/api/books/demo/view", (route) => fulfill(route, { ok: true }));
    await page.goto("/#/read/demo/0?mode=listen");
    await expect(page.locator(".reader-transcript-segment")).toHaveCount(2);
    await expect(page.locator(".reader-transcript-segment").first()).toContainText("Alice looked at the sky.");
    await expect(page.locator(".reader-transcript-segment").nth(1)).toContainText("Bob smiled.");
    await expect(page.locator("#reader-audio")).toHaveCount(0);
    await expect(page.locator(".reader-audio-note")).toContainText("重新生成");
  });

  test("app 登入 modal 具無障礙屬性且 ESC 可關閉", async ({ page }) => {
    await page.goto("/#/bookshelf");
    // 等 app init 完成綁定（#btn-login handler 在 loadBooks 前綁定），避免點擊落在未綁定視窗
    await page.waitForFunction(() => document.querySelector("#book-list")?.children.length > 0);
    await page.locator("#btn-login").click();
    await expect(page.locator("#login-modal")).toBeVisible();
    await expect(page.locator("#login-modal")).toHaveAttribute("role", "dialog");
    await expect(page.locator("#login-modal")).toHaveAttribute("aria-modal", "true");
    await page.keyboard.press("Escape");
    await expect(page.locator("#login-modal")).toBeHidden();
    await expect(page.locator("#btn-login")).toBeFocused();
  });

  test("註冊 popup 可從登入切換、密碼不符即時提示且可返回登入", async ({ page }) => {
    await page.goto("/#/bookshelf");
    await page.waitForFunction(() => document.querySelector("#book-list")?.children.length > 0);
    await page.locator("#btn-login").click();
    await page.locator("#btn-register-open").click();
    await expect(page.locator("#register-modal")).toBeVisible();
    await expect(page.locator("#login-modal")).toBeHidden();
    await expect(page.locator("#register-modal")).toHaveAttribute("role", "dialog");
    await expect(page.locator("#register-modal")).toHaveAttribute("aria-modal", "true");
    await page.locator("#register-username").fill("新讀者");
    await page.locator("#register-password").fill("secret123");
    await page.locator("#register-confirm").fill("different");
    await page.locator("#btn-register-submit").click();
    await expect(page.locator(".toast-error")).toContainText("兩次密碼不一致");
    await page.locator("#btn-register-cancel").click();
    await expect(page.locator("#login-modal")).toBeVisible();
    await expect(page.locator("#register-modal")).toBeHidden();
  });

  test("詳情頁未生成音檔時不顯示「聽書跟讀」卡片", async ({ page }) => {
    await page.route("**/api/auth/me", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ authed: false }) }));
    await page.route("**/api/books/demo/recommendations", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) }));
    await page.route("**/api/books/demo/comments", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) }));
    await page.route("**/api/books/demo", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      id: "demo", bid: "demo", title: "無音檔作品", synopsis: "測試簡介", serial: "連載",
      owner: "作者", totalChars: 1000, litCount: "小說", status: "approved", category: "vocab", categories: ["vocab"],
      chapters: [{ id: 1, seq: 0, title: "序章", status: "analyzed", audio: "none", publishStatus: "published" }],
    }) }));
    await page.goto("/#/book/demo");
    await expect(page.getByRole("heading", { name: "進入閱讀後再選方式" })).toBeVisible();
    await expect(page.locator(".reading-start-panel a", { hasText: "聽書跟讀" })).toHaveCount(0);
  });

  test("沒有學習資料時不顯示「閱讀＋學習」入口", async ({ page }) => {
    await page.route("**/api/auth/me", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ authed: false }) }));
    await page.route("**/api/books/demo/recommendations", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) }));
    await page.route("**/api/books/demo/comments", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) }));
    await page.route("**/api/books/demo", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      id: "demo", bid: "demo", title: "尚無學習資料", synopsis: "尚未分析", serial: "連載",
      owner: "作者", totalChars: 1000, litCount: "小說", status: "approved", category: "vocab", categories: ["vocab"],
      chapters: [{ id: 1, seq: 0, title: "序章", status: "pending", audio: "none", publishStatus: "published" }],
    }) }));
    await page.goto("/#/book/demo");
    await expect(page.getByRole("heading", { name: "進入閱讀後再選方式" })).toBeVisible();
    await expect(page.getByRole("link", { name: "閱讀＋學習" })).toHaveCount(0);
  });

  test("詳情頁已有音檔時顯示「聽書跟讀」卡片", async ({ page }) => {
    await page.route("**/api/auth/me", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ authed: false }) }));
    await page.route("**/api/books/demo/recommendations", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) }));
    await page.route("**/api/books/demo/comments", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) }));
    await page.route("**/api/books/demo", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      id: "demo", bid: "demo", title: "有音檔作品", synopsis: "測試簡介", serial: "連載",
      owner: "作者", totalChars: 1000, litCount: "小說", status: "approved", category: "vocab", categories: ["vocab"],
      chapters: [{ id: 1, seq: 0, title: "序章", status: "analyzed", audio: "ready", publishStatus: "published" }],
    }) }));
    await page.goto("/#/book/demo");
    await expect(page.locator(".reading-start-panel a", { hasText: "聽書跟讀" })).toHaveAttribute("href", "#/read/demo/0?mode=listen");
    await expect(page.locator(".reading-choice-card")).toHaveCount(0);
  });

  test("純中文作品不顯示「閱讀＋學習」入口", async ({ page }) => {
    await page.route("**/api/auth/me", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ authed: false }) }));
    await page.route("**/api/books/demo/recommendations", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) }));
    await page.route("**/api/books/demo/comments", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) }));
    await page.route("**/api/books/demo", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      id: "demo", bid: "demo", title: "純中文作品", synopsis: "純中文內容", serial: "連載",
      owner: "作者", totalChars: 1000, litCount: "小說", status: "approved", category: "zh", categories: ["zh"],
      chapters: [{ id: 1, seq: 0, title: "序章", status: "analyzed", audio: "none", publishStatus: "published" }],
    }) }));
    await page.goto("/#/book/demo");
    await expect(page.getByRole("heading", { name: "進入閱讀後再選方式" })).toBeVisible();
    await expect(page.getByRole("link", { name: "閱讀＋學習" })).toHaveCount(0);
  });

  test("學習入口只依 canonical 語言型別，不受 categories 污染", async ({ page }) => {
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    await page.route("**/api/auth/me", (route) => fulfill(route, { authed: false }));
    await page.route("**/api/books/demo/recommendations", (route) => fulfill(route, { items: [] }));
    await page.route("**/api/books/demo/comments", (route) => fulfill(route, { items: [] }));
    let current = { category: "zh", categories: ["vocab"] };
    await page.route("**/api/books/demo", (route) => fulfill(route, {
      id: "demo", bid: "demo", title: "語言型別測試", synopsis: "", serial: "連載",
      owner: "作者", totalChars: 10, status: "approved", ...current,
      chapters: [{ id: 1, seq: 0, title: "序章", status: "analyzed", audio: "none", publishStatus: "published" }],
    }));
    await page.goto("/#/book/demo");
    await expect(page.getByRole("link", { name: "閱讀＋學習" })).toHaveCount(0);

    current = { category: "vocab", categories: ["zh"] };
    await page.reload();
    await expect(page.getByRole("heading", { name: "進入閱讀後再選方式" })).toBeVisible();

    current = { category: "bilingual", categories: ["zh"] };
    await page.reload();
    await expect(page.getByRole("heading", { name: "進入閱讀後再選方式" })).toBeVisible();

    current = { category: "other", categories: ["vocab", "bilingual"] };
    await page.reload();
    await expect(page.getByRole("link", { name: "閱讀＋學習" })).toHaveCount(0);
  });

  test("PWA manifest 包含可用圖示與 scope", async ({ page }) => {
    const response = await page.request.get("/manifest.webmanifest");
    expect(response.ok()).toBeTruthy();
    const manifest = await response.json();
    expect(manifest.scope).toBe("/");
    expect(manifest.icons.length).toBeGreaterThan(0);
    expect((await page.request.get(manifest.icons[0].src)).ok()).toBeTruthy();
  });
});
