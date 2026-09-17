import { test, expect } from "@playwright/test";

const author = { displayName: "分頁作者", slug: "pagination-author", publicId: "ap-pagination", status: "active", link: "/authors/pagination-author" };

function fulfill(route, data) {
  return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
}

function card(id, title) {
  return { id, title, synopsis: "公開分頁內容", category: "zh", categories: ["zh"], serial: "連載", status: "approved", owner: author.displayName, author, audioReadyCount: 0 };
}

async function common(page, user = { id: 7, username: "分頁使用者", role: "reader" }) {
  await page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user }));
  await page.route(/\/api\/books(?:\?.*)?$/, (route) => fulfill(route, []));
  await page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
  await page.route("**/api/requests**", (route) => fulfill(route, { items: [] }));
}

test.describe("Platform 分頁 contract 與 route state", () => {
  for (const scenario of [
    { hash: "#/search?q=retry&language=en&sort=updated&serial=%E9%80%A3%E8%BC%89&has_audio=1&page=2", categoryId: null },
    { hash: "#/category/77?q=retry&language=zh&sort=chars&page=2", categoryId: "77" },
  ]) {
    test(`搜尋錯誤重試保留目前 route 與 query：${scenario.hash}`, async ({ page }) => {
      await common(page);
      const requests = [];
      await page.route("**/api/genres", (route) => fulfill(route, { items: [{ id: 77, name: "測試分類" }] }));
      await page.route(/\/api\/search(?:\?.*)?$/, (route) => {
        requests.push(new URL(route.request().url()).search);
        if (requests.length === 1) return route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
        return new Promise((resolve) => setTimeout(() => resolve(fulfill(route, { items: [card("retry-result", "重試成功")], page: 2, page_size: 20, total: 21, total_pages: 2, has_next: false, has_prev: true })), 150));
      });
      await page.goto(`/${scenario.hash}`);
      const initialUrl = page.url();
      await expect(page.locator("#search-retry")).toBeVisible();
      await page.locator("#search-retry").click();
      await expect(page.locator(".platform-result-loading")).toBeVisible();
      await expect(page.locator("#platform-search-result")).toContainText("重試成功");
      expect(requests).toHaveLength(2);
      expect(requests[1]).toBe(requests[0]);
      await expect(page).toHaveURL(initialUrl);
      if (scenario.categoryId) await expect(page.getByRole("heading", { name: "分類：測試分類" })).toBeVisible();
      await page.reload();
      await expect(page.locator("#platform-search-result")).toContainText("重試成功");
      await expect(page).toHaveURL(initialUrl);
    });
  }

  test("搜尋重試後離開頁面，晚回應不能覆蓋首頁", async ({ page }) => {
    await common(page);
    let calls = 0;
    let release;
    const pending = new Promise((resolve) => { release = resolve; });
    await page.route("**/api/home", (route) => fulfill(route, { latest: [], rankings: [], completed: [], categories: [], banners: [] }));
    await page.route(/\/api\/search(?:\?.*)?$/, async (route) => {
      if (++calls === 1) return route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
      await pending;
      return fulfill(route, { items: [card("late-retry", "過期重試結果")], page: 1, page_size: 20, total: 1, total_pages: 1 });
    });
    await page.goto("/#/search?q=late");
    await page.locator("#search-retry").click();
    await expect.poll(() => calls).toBe(2);
    await page.evaluate(() => { location.hash = "#/home"; });
    await expect(page.locator(".home-intro")).toBeVisible();
    release();
    await page.waitForTimeout(80);
    await expect(page.locator(".home-intro")).toBeVisible();
    await expect(page.locator("#platform-search-result")).toHaveCount(0);
    await expect(page).toHaveURL(/#\/home$/);
  });

  test("書架錯誤重試保留兩組分頁並在重整後維持 route", async ({ page }) => {
    await common(page, { id: 7, username: "書架作者", role: "author" });
    const favoriteRequests = [];
    const historyRequests = [];
    await page.route("**/api/authors/application", (route) => fulfill(route, { application: null }));
    await page.route("**/api/me/library**", (route) => {
      favoriteRequests.push(new URL(route.request().url()).search);
      if (favoriteRequests.length === 1) return route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
      return new Promise((resolve) => setTimeout(() => resolve(fulfill(route, { items: [card("retry-favorite", "重試後收藏")], page: 2, page_size: 20, total: 21, total_pages: 2, has_next: false, has_prev: true })), 150));
    });
    await page.route("**/api/me/history**", (route) => {
      historyRequests.push(new URL(route.request().url()).search);
      return fulfill(route, { items: [card("retry-history", "重試後紀錄")], page: 3, page_size: 20, total: 41, total_pages: 3, has_next: false, has_prev: true });
    });
    const route = "/#/shelf?favorite_page=2&history_page=3";
    await page.goto(route);
    const initialUrl = page.url();
    await expect(page.locator("#shelf-retry")).toBeVisible();
    await page.locator("#shelf-retry").click();
    await expect(page.locator(".platform-result-loading")).toBeVisible();
    await expect(page.locator(".platform-page")).toContainText("重試後收藏");
    expect(favoriteRequests).toEqual(["?kind=favorite&page=2&page_size=20", "?kind=favorite&page=2&page_size=20"]);
    expect(historyRequests).toEqual(["?page=3&page_size=20", "?page=3&page_size=20"]);
    await expect(page).toHaveURL(initialUrl);
    await page.reload();
    await expect(page.locator(".platform-page")).toContainText("重試後紀錄");
    await expect(page).toHaveURL(initialUrl);
  });

  test("書架重試後離開頁面，晚失敗不能覆蓋首頁", async ({ page }) => {
    await common(page);
    let favoriteCalls = 0;
    let release;
    const pending = new Promise((resolve) => { release = resolve; });
    await page.route("**/api/home", (route) => fulfill(route, { latest: [], rankings: [], completed: [], categories: [], banners: [] }));
    await page.route("**/api/me/library**", async (route) => {
      if (++favoriteCalls === 1) return route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
      await pending;
      return route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
    });
    await page.route("**/api/me/history**", (route) => fulfill(route, { items: [], page: 1, total: 0, total_pages: 0 }));
    await page.goto("/#/shelf");
    await page.locator("#shelf-retry").click();
    await expect.poll(() => favoriteCalls).toBe(2);
    await page.evaluate(() => { location.hash = "#/home"; });
    await expect(page.locator(".home-intro")).toBeVisible();
    release();
    await page.waitForTimeout(80);
    await expect(page.locator(".home-intro")).toBeVisible();
    await expect(page.locator("#shelf-retry")).toHaveCount(0);
    await expect(page).toHaveURL(/#\/home$/);
  });

  test("公開搜尋以 server page 狀態前進，且窄視窗不產生橫向溢出", async ({ page }) => {
    await common(page, { id: 7, username: "公開讀者", role: "reader" });
    await page.route(/\/api\/search(?:\?.*)?$/, (route) => {
      const url = new URL(route.request().url());
      const current = Number(url.searchParams.get("page") || 1);
      return fulfill(route, current === 1
        ? { items: [card("search-1", "搜尋第一頁")], page: 1, page_size: 1, total: 2, total_pages: 2, has_next: true, has_prev: false }
        : { items: [card("search-2", "搜尋第二頁")], page: 2, page_size: 1, total: 2, total_pages: 2, has_next: false, has_prev: true });
    });
    await page.goto("/#/search?q=搜尋");
    await expect(page.locator("#platform-search-result")).toContainText("搜尋第一頁");
    await page.getByRole("link", { name: "下一頁" }).click();
    await expect(page).toHaveURL(/#\/search\?q=%E6%90%9C%E5%B0%8B&page=2/);
    await expect(page.locator("#platform-search-result")).toContainText("搜尋第二頁");
    await page.reload();
    await expect(page.locator("#platform-search-result")).toContainText("搜尋第二頁");
    await page.goBack();
    await expect(page.locator("#platform-search-result")).toContainText("搜尋第一頁");
    await page.goForward();
    await expect(page.locator("#platform-search-result")).toContainText("搜尋第二頁");
    const fits = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
    expect(fits).toBe(true);
  });

  test("作者我的創作使用 bounded page，保留作品管理 metadata", async ({ page }) => {
    await common(page, { id: 8, username: "作者分頁", role: "author" });
    let deleted = false;
    await page.route(/\/api\/books\?mine=1(?:&.*)?$/, (route) => {
      const current = Number(new URL(route.request().url()).searchParams.get("page") || 1);
      if (deleted && current === 2) {
        return fulfill(route, { items: [], page: 2, page_size: 1, total: 1, total_pages: 1, has_next: false, has_prev: true });
      }
      const item = current === 1 ? { ...card("mine-1", "我的第一頁"), status: "draft", ownerId: 8, chapterCount: 2, audioReadyCount: 1, analyzedChapterCount: 2, cover: null, created: "2026-01-01", rejectReason: "" }
        : { ...card("mine-2", "我的第二頁"), status: "draft", ownerId: 8, chapterCount: 1, audioReadyCount: 0, analyzedChapterCount: 0, cover: null, created: "2026-01-02", rejectReason: "" };
      return fulfill(route, { items: [item], page: current, page_size: 1, total: 2, total_pages: 2, has_next: current === 1, has_prev: current > 1 });
    });
    await page.route("**/api/books/mine-2", async (route) => { deleted = true; return fulfill(route, { ok: true }); });
    await page.goto("/#/mine");
    await expect(page.locator("#mine-count")).toHaveText("2 本");
    await expect(page.locator("#mine-list")).toContainText("我的第一頁");
    await page.locator("#mine-list").getByRole("link", { name: "下一頁" }).click();
    await expect(page).toHaveURL(/#\/mine\?page=2/);
    await expect(page.locator("#mine-list")).toContainText("我的第二頁");
    await page.locator("#mine-list .mine-more-actions summary").click();
    page.once("dialog", (dialog) => dialog.accept());
    await page.locator("#mine-list").getByRole("button", { name: "刪除" }).click();
    await expect(page).toHaveURL(/#\/mine\?page=1/);
    await expect(page.locator("#mine-list")).toContainText("我的第一頁");
  });

  test("作者公開作品頁支援 page query 且維持 sanitized profile", async ({ page }) => {
    await common(page);
    await page.route(/\/api\/authors\/pagination-author(?:\?.*)?$/, (route) => {
      const current = Number(new URL(route.request().url()).searchParams.get("page") || 1);
      return fulfill(route, {
        author,
        items: [card(current === 1 ? "author-1" : "author-2", current === 1 ? "作者作品第一頁" : "作者作品第二頁")],
        works: [card(current === 1 ? "author-1" : "author-2", current === 1 ? "作者作品第一頁" : "作者作品第二頁")],
        page: current, page_size: 1, total: 2, total_pages: 2, has_next: current === 1, has_prev: current > 1,
      });
    });
    await page.goto("/#/author/pagination-author");
    await expect(page.locator(".author-profile-hero h2")).toHaveText(author.displayName);
    await expect(page.locator(".platform-page")).toContainText("作者作品第一頁");
    await page.getByRole("link", { name: "下一頁" }).click();
    await expect(page).toHaveURL(/#\/author\/pagination-author\?page=2/);
    await expect(page.locator(".platform-page")).toContainText("作者作品第二頁");
  });

  test("私人書架各 collection 分別分頁，且舊的慢搜尋回應不能覆寫新頁", async ({ page }) => {
    await common(page);
    await page.route("**/api/authors/application", (route) => fulfill(route, { application: null }));
    await page.route(/\/api\/me\/library(?:\?.*)?$/, (route) => {
      const url = new URL(route.request().url());
      const current = Number(url.searchParams.get("page") || 1);
      return fulfill(route, current === 1
        ? { items: [card("favorite-1", "收藏第一頁")], page: 1, page_size: 1, total: 2, total_pages: 2, has_next: true, has_prev: false }
        : { items: [card("favorite-2", "收藏第二頁")], page: 2, page_size: 1, total: 2, total_pages: 2, has_next: false, has_prev: true });
    });
    await page.route(/\/api\/me\/history(?:\?.*)?$/, (route) => fulfill(route, { items: [], page: 1, page_size: 20, total: 0, total_pages: 0, has_next: false, has_prev: false }));
    await page.goto("/#/shelf");
    await expect(page.locator(".platform-page")).toContainText("收藏第一頁");
    await page.getByRole("link", { name: "下一頁" }).first().click();
    await expect(page).toHaveURL(/#\/shelf\?favorite_page=2/);
    await expect(page.locator(".platform-page")).toContainText("收藏第二頁");

    let releaseSlow;
    const slow = new Promise((resolve) => { releaseSlow = resolve; });
    await page.route(/\/api\/search(?:\?.*)?$/, async (route) => {
      const current = Number(new URL(route.request().url()).searchParams.get("page") || 1);
      if (current === 1) await slow;
      return fulfill(route, { items: [card(`race-${current}`, current === 1 ? "慢的舊頁" : "新的目前頁")], page: current, page_size: 1, total: 2, total_pages: 2, has_next: current === 1, has_prev: current > 1 });
    });
    await page.goto("/#/search?q=race&page=1");
    await page.goto("/#/search?q=race&page=2");
    await expect(page.locator("#platform-search-result")).toContainText("新的目前頁");
    releaseSlow();
    await page.waitForTimeout(100);
    await expect(page.locator("#platform-search-result")).not.toContainText("慢的舊頁");
  });
});
