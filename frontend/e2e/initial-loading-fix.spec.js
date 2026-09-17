import { test, expect } from "@playwright/test";

const json = (data, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(data),
});

async function mockSharedBootstrapApis(page) {
  await page.route("**/api/voices", (route) => route.fulfill(json({ voices: [] })));
}

async function holdAuth(page, response) {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route("**/api/auth/me", async (route) => {
    await gate;
    await route.fulfill(json(response));
  });
  return release;
}

async function expectBootstrapGate(page) {
  await expect(page.locator("#app-bootstrap")).toBeVisible();
  await expect(page.locator("#view-bookshelf")).toBeHidden();
  await expect(page.locator("#view-platform")).toBeHidden();
  await expect(page.locator("#view-mine")).toBeHidden();
  await expect(page.locator("#view-admin")).toBeHidden();
  await expect(page.locator("#app-bootstrap")).toHaveAttribute("aria-busy", "true");
  await expect(page.locator("#view-bookshelf")).toHaveAttribute("hidden", "");
  await expect(page.getByRole("heading", { name: "我的書架" })).toHaveCount(0);
}

async function finishHome(page, release, homeData = {}) {
  await page.route("**/api/home", (route) => route.fulfill(json({
    banners: [], categories: [], continueReading: [], rankings: [], latest: [], completed: [], ...homeData,
  })));
  release();
  await expect(page.locator(".platform-page")).toBeVisible();
  await expect(page.locator("#view-platform")).toBeVisible();
  await expect(page.locator("#app-bootstrap")).toBeHidden();
  await expect(page.locator("html")).toHaveAttribute("data-storylingo-bootstrap", "VIEW_VISIBLE");
}

test.describe("initial-loading-fix", () => {
  test("訪客首頁在 auth bootstrap 延遲時不顯示錯誤書架", async ({ page }) => {
    await mockSharedBootstrapApis(page);
    const release = await holdAuth(page, { authed: false });
    await page.goto("/#/home");
    await expectBootstrapGate(page);
    await finishHome(page, release);
    await expect(page.getByRole("heading", { name: "探索分類" })).toBeVisible();
  });

  for (const [label, user] of [
    ["登入讀者", { id: 2, username: "reader", role: "reader" }],
    ["登入作者", { id: 3, username: "author", role: "author" }],
  ]) {
    test(`${label}首頁在 auth bootstrap 延遲時不顯示受保護 view`, async ({ page }) => {
      await mockSharedBootstrapApis(page);
      const release = await holdAuth(page, { authed: true, user });
      await page.goto("/#/home");
      await expectBootstrapGate(page);
      await finishHome(page, release);
      await expect(page.getByRole("heading", { name: "探索分類" })).toBeVisible();
    });
  }

  test("公開作品 direct route 在 bootstrap 完成前不顯示書架", async ({ page }) => {
    await mockSharedBootstrapApis(page);
    const release = await holdAuth(page, { authed: false });
    await page.route("**/api/books/demo", (route) => route.fulfill(json({
      id: "demo", bid: "demo", title: "公開測試書", synopsis: "測試", serial: "連載",
      owner: "作者", chapters: [], status: "approved",
    })));
    await page.route("**/api/books/demo/recommendations", (route) => route.fulfill(json({ items: [] })));
    await page.route("**/api/books/demo/comments", (route) => route.fulfill(json({ items: [] })));
    await page.goto("/#/book/demo");
    await expectBootstrapGate(page);
    release();
    await expect(page.locator(".platform-detail-hero")).toBeVisible();
    await expect(page.locator("#app-bootstrap")).toBeHidden();
  });

  test("reader direct route 與 browser refresh 不會回到書架內容", async ({ page }) => {
    await mockSharedBootstrapApis(page);
    await page.route("**/api/auth/me", (route) => route.fulfill(json({ authed: false })));
    const readResponse = json({
      book: { id: "demo", title: "閱讀測試書", category: "zh" },
      chapter: { seq: 0, title: "第一章", text: "閱讀正文", chars: 4, audio: "none" },
      navigation: { previous: null, next: null, total: 1 },
    });
    await page.route("**/api/books/demo/read/0", (route) => route.fulfill(readResponse));
    await page.route("**/api/books/demo/chapters/0", (route) => route.fulfill(json({ analysis: { segments: [] }, timing: null })));
    await page.route("**/api/books/demo/view", (route) => route.fulfill(json({ ok: true })));
    await page.goto("/#/read/demo/0");
    await expect(page.locator(".reader-sheet")).toBeVisible();
    await expect(page.getByRole("heading", { name: "我的書架" })).toHaveCount(0);
    await page.reload();
    await expect(page.locator(".reader-sheet")).toBeVisible();
    await expect(page.getByRole("heading", { name: "我的書架" })).toHaveCount(0);
  });

  test("direct bookshelf route resolves to the platform shell without a stale app view", async ({ page }) => {
    await mockSharedBootstrapApis(page);
    const release = await holdAuth(page, { authed: false });
    await page.goto("/#/shelf");
    await expectBootstrapGate(page);
    release();
    await expect(page.locator(".platform-page")).toBeVisible();
    await expect(page.getByRole("heading", { name: "登入後管理你的書架" })).toBeVisible();
    await expect(page.locator("#view-bookshelf")).toBeHidden();
  });

  test("direct author route resolves to mine without exposing bookshelf", async ({ page }) => {
    await mockSharedBootstrapApis(page);
    const release = await holdAuth(page, { authed: true, user: { id: 3, username: "author", role: "author" } });
    await page.route("**/api/books*", (route) => route.fulfill(json([])));
    await page.goto("/#/mine");
    await expectBootstrapGate(page);
    release();
    await expect(page.locator("#view-mine")).toBeVisible();
    await expect(page.locator("#app-bootstrap")).toBeHidden();
    await expect(page.locator("#view-bookshelf")).toBeHidden();
  });

  test("direct admin route resolves to admin without exposing bookshelf", async ({ page }) => {
    await mockSharedBootstrapApis(page);
    const release = await holdAuth(page, { authed: true, user: { id: 1, username: "admin", role: "admin" } });
    await page.route("**/api/admin/**", (route) => route.fulfill(json({ items: [], categories: [] })));
    await page.goto("/#/admin");
    await expectBootstrapGate(page);
    release();
    await expect(page.locator("#view-admin")).toBeVisible();
    await expect(page.locator("#app-bootstrap")).toBeHidden();
    await expect(page.locator("#view-bookshelf")).toBeHidden();
  });

  test("unknown route follows the documented safe bookshelf default", async ({ page }) => {
    await mockSharedBootstrapApis(page);
    const release = await holdAuth(page, { authed: false });
    await page.route("**/api/books*", (route) => route.fulfill(json([])));
    await page.goto("/#/unsupported");
    await expectBootstrapGate(page);
    release();
    await expect(page.locator("#view-bookshelf")).toBeVisible();
    await expect(page.locator("#app-bootstrap")).toBeHidden();
    await expect(page.getByRole("heading", { name: "我的書架" })).toBeVisible();
    await expect(page.locator("#view-platform")).toBeHidden();
  });

  test("failed auth bootstrap 仍以 guest-safe 首頁完成 route resolution", async ({ page }) => {
    await mockSharedBootstrapApis(page);
    await page.route("**/api/auth/me", (route) => route.fulfill(json({ detail: "session unavailable" }, 503)));
    await page.route("**/api/home", (route) => route.fulfill(json({ banners: [], categories: [], rankings: [], latest: [], completed: [] })));
    await page.goto("/#/home");
    await expect(page.locator(".platform-page")).toBeVisible();
    await expect(page.locator("#app-bootstrap")).toBeHidden();
    await expect(page.getByRole("heading", { name: "我的書架" })).toHaveCount(0);
  });

  test("登出後回到 guest-safe 首頁且不殘留管理 view", async ({ page }) => {
    await mockSharedBootstrapApis(page);
    let authed = true;
    await page.route("**/api/auth/me", (route) => route.fulfill(json({
      authed,
      user: authed ? { id: 3, username: "author", role: "author" } : undefined,
    })));
    await page.route("**/api/home", (route) => route.fulfill(json({
      banners: [], categories: [], rankings: [], latest: [], completed: [],
    })));
    await page.route("**/api/auth/logout", (route) => {
      authed = false;
      route.fulfill(json({ ok: true }));
    });
    await page.goto("/#/home");
    await expect(page.locator(".platform-page")).toBeVisible();
    await expect(page.locator("#btn-login")).toHaveText("登出");
    if (await page.locator("#nav-toggle").isVisible()) await page.locator("#nav-toggle").click();
    await page.locator("#btn-login").click();
    await expect(page.locator("#btn-login")).toHaveText("登入");
    await expect(page).toHaveURL(/#\/home$/);
    await expect(page.locator("#view-platform")).toBeVisible();
    await expect(page.locator("#view-bookshelf")).toBeHidden();
    await expect(page.getByRole("heading", { name: "我的書架" })).toHaveCount(0);
  });

  test("back/forward navigation keeps route-specific visible view", async ({ page }) => {
    await mockSharedBootstrapApis(page);
    await page.route("**/api/auth/me", (route) => route.fulfill(json({ authed: false })));
    await page.route("**/api/home", (route) => route.fulfill(json({ banners: [], categories: [], rankings: [], latest: [], completed: [] })));
    await page.route("**/api/search**", (route) => route.fulfill(json({ total: 0, page: 1, items: [] })));
    await page.goto("/#/home");
    await expect(page.locator(".platform-page")).toBeVisible();
    await page.goto("/#/search?q=history");
    await expect(page.getByRole("heading", { name: "搜尋小說" })).toBeVisible();
    await page.goBack();
    await expect(page.getByRole("heading", { name: "找一本故事，開始閱讀" })).toBeVisible();
    await page.goForward();
    await expect(page.getByRole("heading", { name: "搜尋小說" })).toBeVisible();
  });

  // NOT APPLICABLE: login is a modal flow in the current app; no direct login hash route exists.
});
