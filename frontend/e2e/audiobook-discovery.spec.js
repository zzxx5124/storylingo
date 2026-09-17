import { test, expect } from "@playwright/test";

const fulfill = (route, data) => route.fulfill({
  status: 200, contentType: "application/json", body: JSON.stringify(data),
});

const audiobook = (overrides = {}) => ({
  id: "book-1", title: "可聆聽作品", synopsis: "公開作品簡介", category: "vocab",
  categoryName: "語言學習", languageType: "vocab", serial: "連載", author: {
    displayName: "公開作者", link: "/authors/public", slug: "public", status: "active",
  }, availability: "available", playableChapterCount: 2, publicChapterCount: 3,
  detailRoute: "#/book/book-1", listenRoute: "#/read/book-1/2?mode=listen", ...overrides,
});

test.describe("公開有聲書探索", () => {
  for (const hash of ["#/audiobooks", "#/audiobooks?q=story&category_id=7&language=en&sort=updated&page=2"]) {
    test(`錯誤重試重新請求相同查詢並保留 refresh 狀態：${hash}`, async ({ page }) => {
      const queries = [];
      await page.route("**/api/audiobooks?*", async (route) => {
        queries.push(new URL(route.request().url()).search);
        if (queries.length === 1) return route.fulfill({ status: 503, body: "{}", contentType: "application/json" });
        await new Promise((resolve) => setTimeout(resolve, 150));
        return fulfill(route, { items: [audiobook()], page: hash.includes("page=2") ? 2 : 1, page_size: 20, total: 21, total_pages: 2 });
      });
      await page.goto(`/${hash}`);
      const initialUrl = page.url();
      await page.locator("#audiobook-retry").click();
      await expect(page.locator(".platform-result-loading")).toBeVisible();
      await expect(page.locator(".audiobook-card")).toBeVisible();
      expect(queries).toHaveLength(2);
      expect(queries[1]).toBe(queries[0]);
      if (hash.includes("page=2")) {
        expect(Object.fromEntries(new URLSearchParams(queries[1]))).toMatchObject({ q: "story", category_id: "7", language: "en", sort: "updated", page: "2" });
      }
      await expect(page).toHaveURL(initialUrl);
      await page.reload();
      await expect(page.locator(".audiobook-card")).toBeVisible();
      expect(queries[2]).toBe(queries[0]);
      await expect(page).toHaveURL(initialUrl);
    });
  }

  test("重試後離開頁面，晚回應不能覆蓋首頁", async ({ page }) => {
    let calls = 0;
    let release;
    const pending = new Promise((resolve) => { release = resolve; });
    await page.route("**/api/home", (route) => fulfill(route, { latest: [], rankings: [], completed: [], categories: [], banners: [] }));
    await page.route("**/api/audiobooks?*", async (route) => {
      if (++calls === 1) return route.fulfill({ status: 503, body: "{}", contentType: "application/json" });
      await pending;
      return fulfill(route, { items: [audiobook({ title: "過期重試結果" })], page: 1, total: 1, total_pages: 1 });
    });
    await page.goto("/#/audiobooks");
    await page.locator("#audiobook-retry").click();
    await expect.poll(() => calls).toBe(2);
    await page.evaluate(() => { location.hash = "#/home"; });
    await expect(page.locator(".home-intro")).toBeVisible();
    const response = page.waitForResponse((res) => res.url().includes("/api/audiobooks?") && res.status() === 200);
    release();
    await response;
    await expect(page.locator(".home-intro")).toBeVisible();
    await expect(page.locator(".audiobook-card")).toHaveCount(0);
    await expect(page).toHaveURL(/#\/home$/);
  });

  test.beforeEach(async ({ page }) => {
    await page.route("**/api/auth/me", (route) => fulfill(route, { authed: false }));
    await page.route("**/api/genres", (route) => fulfill(route, { items: [{ id: 7, name: "語言學習" }] }));
  });

  test("可瀏覽有聲書卡片並從第一個可播放章節開始聆聽", async ({ page }) => {
    await page.route("**/api/audiobooks**", (route) => fulfill(route, { items: [audiobook({ availability: "partial" })], page: 1, page_size: 20, total: 1, total_pages: 1, has_next: false, has_prev: false }));
    await page.goto("/#/audiobooks");
    await expect(page.getByRole("heading", { name: "有聲書" })).toBeVisible();
    await expect(page.locator(".audiobook-card")).toContainText("部分");
    await expect(page.locator(".audiobook-card .platform-author-link")).toHaveAttribute("href", "#/author/public");
    await expect(page.locator(".platform-card-listen")).toHaveAttribute("href", "#/read/book-1/2?mode=listen");
    await page.route("**/api/books/book-1/recommendations", (route) => fulfill(route, { items: [] }));
    await page.route("**/api/books/book-1/comments", (route) => fulfill(route, { items: [] }));
    await page.route("**/api/books/book-1", (route) => fulfill(route, {
      id: "book-1", title: "可聆聽作品", synopsis: "公開作品簡介", owner: "公開作者", chapters: [
        { seq: 0, title: "序章", status: "pending", audio: "none" },
        { seq: 1, title: "第一章", status: "pending", audio: "ready" },
        { seq: 2, title: "第二章", status: "pending", audio: "ready" },
      ], audiobook: { availability: "partial", playableChapterCount: 1, publicChapterCount: 3, firstPlayableChapter: 2, playableChapterSeqs: [2] },
    }));
    await page.goto("/#/book/book-1");
    await expect(page.locator(".reading-start-panel a", { hasText: "聽書跟讀" })).toHaveAttribute("href", "#/read/book-1/2?mode=listen");
  });

  test("篩選、重新載入與手機版不會遺失狀態或產生橫向溢出", async ({ page }) => {
    await page.route("**/api/audiobooks**", (route) => {
      const url = new URL(route.request().url());
      const filtered = url.searchParams.get("language") === "en";
      return fulfill(route, { items: filtered ? [audiobook({ id: "book-en", title: "英文有聲書", languageType: "en" })] : [audiobook()], page: 1, page_size: 20, total: 1, total_pages: 1, has_next: false, has_prev: false });
    });
    await page.goto("/#/audiobooks");
    await page.locator("#platform-audiobook-form select[name=language]").selectOption("en");
    await page.locator("#platform-audiobook-form").getByRole("button", { name: "搜尋" }).click();
    await expect(page).toHaveURL(/audiobooks\?.*language=en/);
    await expect(page.locator(".audiobook-card h3")).toContainText("英文有聲書");
    await page.reload();
    await expect(page.locator("select[name=language]")).toHaveValue("en");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(async () => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await expect(page.locator(".platform-card-listen")).toBeVisible();
  });

  test("較慢的舊查詢不能覆蓋最新查詢", async ({ page }) => {
    await page.route("**/api/audiobooks**", async (route) => {
      const url = new URL(route.request().url());
      const slow = url.searchParams.get("q") === "慢";
      if (slow) await new Promise((resolve) => setTimeout(resolve, 250));
      return fulfill(route, { items: [audiobook({ id: slow ? "slow" : "fast", title: slow ? "慢結果" : "最新結果" })], page: 1, page_size: 20, total: 1, total_pages: 1, has_next: false, has_prev: false });
    });
    await page.goto("/#/audiobooks?q=%E6%85%A2");
    await page.goto("/#/audiobooks?q=%E5%BF%AB");
    await expect(page.locator(".audiobook-card h3")).toHaveText("最新結果");
    await expect(page.locator(".audiobook-card h3")).not.toHaveText("慢結果");
  });

  test("空結果、錯誤與鍵盤/ARIA 狀態都有安全呈現", async ({ page }) => {
    await page.route("**/api/audiobooks**", async (route) => {
      const url = new URL(route.request().url());
      const q = url.searchParams.get("q");
      if (q === "錯誤") return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "provider detail must not leak" }) });
      return fulfill(route, q === "無結果"
        ? { items: [], page: 1, page_size: 20, total: 0, total_pages: 0, has_next: false, has_prev: false }
        : { items: [audiobook()], page: 1, page_size: 20, total: 1, total_pages: 1, has_next: false, has_prev: false });
    });
    await page.goto("/#/audiobooks");
    const search = page.getByRole("searchbox", { name: "搜尋有聲書" });
    await expect(search).toBeVisible();
    await search.focus();
    await expect(search).toBeFocused();
    await expect(page.locator(".audiobook-card")).toHaveAttribute("aria-label", "開啟有聲書 可聆聽作品");
    await expect(page.locator(".platform-audiobook-availability")).toHaveAttribute("aria-label", "有聲書狀態：可聆聽");

    await search.fill("無結果");
    await page.getByRole("button", { name: "搜尋" }).click();
    await expect(page.locator("#platform-audiobook-result")).toContainText("沒有符合條件的結果");
    await expect(page.getByRole("link", { name: "清除篩選" })).toBeVisible();

    await search.fill("錯誤");
    await page.getByRole("button", { name: "搜尋" }).click();
    await expect(page.locator("#platform-audiobook-result")).toContainText("有聲書目前無法載入");
    await expect(page.locator("#platform-audiobook-result")).not.toContainText("provider detail");
  });
});
