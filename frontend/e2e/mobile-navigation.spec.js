import { test, expect } from "@playwright/test";

test.use({ viewport: { width: 390, height: 844 } });

const json = (route, data, status = 200) => route.fulfill({
  status,
  contentType: "application/json",
  body: JSON.stringify(data),
});

const home = {
  banners: [], categories: [], continueReading: [], rankings: [],
  latest: [{ id: "mobile-book", bid: "mobile-book", title: "手機故事", synopsis: "", category: "zh", serial: "連載", owner: "作者" }],
  completed: [],
};

async function shell(page, user = null) {
  await page.route("**/api/auth/me", (route) => json(route, { authed: Boolean(user), user }));
  await page.route("**/api/voices", (route) => json(route, { voices: [] }));
  await page.route("**/api/categories", (route) => json(route, { categories: [] }));
  await page.route("**/api/announcements/active", (route) => json(route, { items: [] }));
  await page.route(/\/api\/notifications(?:\/unread-count)?(?:\?.*)?$/, (route) => json(route, { items: [], count: 0 }));
  await page.route("**/api/home", (route) => json(route, home));
}

test.describe("Mobile canonical navigation", () => {
  test("guest bottom navigation reaches canonical routes and survives reload", async ({ page }) => {
    await shell(page);
    await page.goto("/#/home");
    const nav = page.locator("#mobile-primary-nav");
    await expect(nav).toBeVisible();
    await expect(nav.locator("a:visible")).toHaveCount(3);
    await expect(nav.locator('a[href="#/mine"]')).toBeHidden();
    const hrefs = await nav.locator("a:visible").evaluateAll((links) => links.map((link) => link.getAttribute("href")));
    expect(hrefs).toEqual(["#/home", "#/search", "#/shelf"]);
    await expect(nav.locator('[data-public-nav="home"]')).toHaveAttribute("aria-current", "page");
    await nav.locator('[data-public-nav="search"]').click();
    await expect(page).toHaveURL(/#\/search$/);
    await expect(nav.locator('[data-public-nav="search"]')).toHaveAttribute("aria-current", "page");
    await page.reload();
    await expect(page).toHaveURL(/#\/search$/);
    await expect(page.locator('#mobile-primary-nav [data-public-nav="search"]')).toHaveAttribute("aria-current", "page");
  });

  test("author enters workspace directly, opens detail, and returns to workspace", async ({ page }) => {
    await shell(page, { id: 7, username: "手機作者", role: "author" });
    await page.route("**/api/books?*", (route) => json(route, {
      items: [{ id: "author-book", bid: "author-book", title: "作者作品", synopsis: "", status: "draft", ownerId: 7, chapters: [] }],
      page: 1, page_size: 20, total: 1, total_pages: 1, has_next: false,
    }));
    await page.route("**/api/books/author-book", (route) => json(route, {
      id: "author-book", bid: "author-book", title: "作者作品", synopsis: "", status: "draft", ownerId: 7, chapters: [], settings: {}, voices: {},
    }));
    await page.goto("/#/mine");
    await expect(page.locator("#mobile-nav-mine")).toHaveAttribute("href", "#/mine");
    await expect(page.locator("#mobile-nav-mine")).toBeVisible();
    await page.locator('.mine-card a[href="#/detail/author-book"]').click();
    await expect(page).toHaveURL(/#\/detail\/author-book$/);
    await expect(page.locator("#mobile-parent")).toHaveAttribute("href", "#/mine");
    await page.locator("#mobile-parent").click();
    await expect(page).toHaveURL(/#\/mine$/);
  });

  test("reader direct URL is immersive and its return control goes to the book", async ({ page }) => {
    await shell(page);
    await page.route("**/api/books/mobile-book/read/0", (route) => json(route, {
      book: { id: "mobile-book", title: "手機故事", category: "zh" },
      chapter: { seq: 0, title: "第一章", text: "手機正文。", chars: 6, audio: "none" },
      navigation: { previous: null, next: null, total: 1 },
    }));
    await page.route("**/api/books/mobile-book/chapters/0", (route) => json(route, { analysis: { segments: [] }, timing: null }));
    await page.route("**/api/books/mobile-book/view", (route) => json(route, { ok: true }));
    await page.goto("/#/read/mobile-book/0");
    await expect(page.locator("#topbar")).toBeHidden();
    await expect(page.locator("#mobile-primary-nav")).toBeHidden();
    await expect(page.locator("#mobile-location")).toBeHidden();
    await expect(page.locator("#mobile-parent")).toBeHidden();
    await expect(page.locator(".reader-top a")).toHaveAttribute("href", "#/book/mobile-book");
    await page.locator(".reader-footer [data-reader-navigation]").first().click();
    await expect(page).toHaveURL(/#\/book\/mobile-book$/);
  });

  test("guest Reader bookmark opens login and topbar login remains usable after returning", async ({ page }) => {
    await shell(page);
    await page.route("**/api/books/mobile-book/read/0", (route) => json(route, {
      book: { id: "mobile-book", title: "手機故事", category: "zh" },
      chapter: { seq: 0, title: "第一章", text: "手機正文。", chars: 6, audio: "none" },
      navigation: { previous: null, next: null, total: 1 },
    }));
    await page.route("**/api/books/mobile-book/chapters/0", (route) => json(route, { analysis: { segments: [] }, timing: null }));
    await page.route("**/api/books/mobile-book/view", (route) => json(route, { ok: true }));
    await page.route("**/api/books/mobile-book", (route) => json(route, {
      id: "mobile-book", bid: "mobile-book", title: "手機故事", synopsis: "", status: "approved", chapters: [],
    }));
    await page.goto("/#/read/mobile-book/0");
    await page.locator("#reader-bookmark").click();
    await expect(page.locator("#login-modal")).toBeVisible();
    await expect(page.locator("#login-username")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator("#login-modal")).toBeHidden();
    await page.locator(".reader-top a").click();
    await expect(page).toHaveURL(/#\/book\/mobile-book$/);
    await expect(page.locator("#topbar")).toBeVisible();
    await page.locator("#btn-login").click();
    await expect(page.locator("#login-modal")).toBeVisible();
  });

  test("Reader 403 returns safely to home and restores the global mobile shell", async ({ page }) => {
    await shell(page);
    await page.route("**/api/books/blocked-book/read/0", (route) => json(route, { detail: "forbidden" }, 403));
    await page.goto("/#/read/blocked-book/0");
    await expect(page.locator(".platform-error")).toContainText("無法載入章節");
    await expect(page.locator(".platform-error a")).toHaveAttribute("href", "#/home");
    await page.locator(".platform-error a").click();
    await expect(page).toHaveURL(/#\/home$/);
    await expect(page.locator("#topbar")).toBeVisible();
    await expect(page.locator("#nav-toggle")).toBeVisible();
    await expect(page.locator("#mobile-primary-nav")).toBeVisible();
  });

  test("Escape and outside click close mobile nav while preserving focus order", async ({ page }) => {
    await shell(page);
    await page.goto("/#/home");
    const toggle = page.locator("#nav-toggle");
    await toggle.click();
    await expect(page.locator("#main-nav")).toBeVisible();
    await expect(page.locator("#main-nav a").first()).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await toggle.click();
    await page.locator(".home-intro h1").click();
    await expect(toggle).toHaveAttribute("aria-label", "開啟導覽選單");
    await expect(page.locator("#main-nav")).toBeHidden();
  });

  test("logout invalidates mobile role entries and restores guest login", async ({ page }) => {
    let loggedIn = true;
    await page.route("**/api/auth/me", (route) => json(route, loggedIn
      ? { authed: true, user: { id: 7, username: "作者", role: "author" } }
      : { authed: false }));
    await page.route("**/api/voices", (route) => json(route, { voices: [] }));
    await page.route("**/api/categories", (route) => json(route, { categories: [] }));
    await page.route("**/api/announcements/active", (route) => json(route, { items: [] }));
    await page.route("**/api/home", (route) => json(route, home));
    await page.route("**/api/auth/logout", (route) => { loggedIn = false; return json(route, { ok: true }); });
    await page.goto("/#/home");
    await page.locator("#nav-toggle").click();
    await expect(page.locator("#mobile-account")).toBeVisible();
    await expect(page.locator("#mobile-nav-mine")).toBeVisible();
    await expect(page.locator("#btn-login")).toContainText("登出");
    await page.locator("#btn-login").click();
    await expect(page.locator("#mobile-nav-mine")).toBeHidden();
    await expect(page.locator("#btn-login")).toHaveText("登入");
  });

  test("author workspace crosses desktop and narrow breakpoints without overflow", async ({ page }) => {
    await shell(page, { id: 7, username: "作者", role: "author" });
    await page.goto("/#/home");
    await page.locator("#nav-toggle").click();
    await expect(page.locator("#mobile-workspace")).toBeVisible();
    await expect(page.locator("#mobile-nav-mine")).toBeVisible();

    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(page.locator("#nav-workspace-group")).toBeVisible();
    await expect(page.locator("#mobile-workspace")).toBeHidden();
    await expect(page.locator("#mobile-primary-nav")).toBeHidden();
    await expect(page.locator("#mobile-account")).toBeHidden();
    await expect(page.locator("#btn-login")).toBeVisible();

    await page.setViewportSize({ width: 320, height: 700 });
    await page.locator("#nav-toggle").click();
    await expect(page.locator("#nav-workspace-group")).toBeHidden();
    await expect(page.locator("#mobile-workspace")).toBeVisible();
    await expect(page.locator("#mobile-nav-mine")).toBeVisible();
    await expect(page.locator("#mobile-primary-nav")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
});
