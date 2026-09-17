import { test, expect } from "@playwright/test";

const BOOK_ID = "book/特別";
const AUTHOR_SLUG = "作者/小林";
const LONG_TITLE = "這是一個很長很長很長很長很長很長很長很長很長很長的排行榜作品標題";
const LONG_AUTHOR = "這是一個很長很長很長很長很長很長很長很長的作者名稱";

function json(route, data, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(data),
  });
}

async function mockRanking(context) {
  await context.route("**/api/auth/me", (route) => json(route, { authed: false }));
  await context.route("**/api/categories", (route) => json(route, { items: [], categories: [] }));
  await context.route("**/api/voices", (route) => json(route, { voices: [] }));
  await context.route("**/api/announcements/active", (route) => json(route, { items: [] }));
  await context.route(/\/api\/notifications(?:\/unread-count)?(?:\?.*)?$/, (route) => json(route, { items: [], count: 0 }));
  await context.route("**/api/rankings?*", (route) => json(route, {
    kind: "hot",
    window: "all",
    items: [
      {
        rank: 1,
        bid: BOOK_ID,
        title: LONG_TITLE,
        synopsis: "測試作品簡介",
        cover_path: null,
        serial: "連載",
        category_name: "奇幻",
        author: { displayName: LONG_AUTHOR, slug: AUTHOR_SLUG, link: "/authors/" + AUTHOR_SLUG },
        owner: LONG_AUTHOR,
      },
      {
        rank: 2,
        bid: "anonymous-book",
        title: "匿名作品",
        synopsis: "匿名作品簡介",
        cover_path: null,
        serial: "完結",
        category_name: "小說",
        author: { displayName: "匿名作者", legacy: true, link: null },
        owner: "匿名作者",
      },
    ],
  }));
  await context.route("**/api/books/**", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/recommendations") || pathname.endsWith("/comments")) return json(route, { items: [] });
    return json(route, {
      id: BOOK_ID,
      bid: BOOK_ID,
      title: LONG_TITLE,
      synopsis: "測試作品簡介",
      serial: "連載",
      owner: LONG_AUTHOR,
      author: { displayName: LONG_AUTHOR, slug: AUTHOR_SLUG, link: "/authors/" + AUTHOR_SLUG },
      status: "approved",
      chapters: [],
    });
  });
  await context.route("**/api/authors/**", (route) => json(route, {
    author: { displayName: LONG_AUTHOR, slug: AUTHOR_SLUG, link: "/authors/" + AUTHOR_SLUG },
    works: [],
    items: [],
    page: 1,
    page_size: 20,
    total: 0,
    total_pages: 1,
    has_next: false,
    has_prev: false,
  }));
}

test.describe("排行榜作品與作者連結", () => {
  test("作品可用滑鼠與 Enter 開啟，作者可用 Tab focus 後 Enter 開啟作者頁", async ({ page, context }) => {
    await mockRanking(context);
    await page.goto("/#/rankings");
    const first = page.locator("article.platform-ranking-item").first();
    const bookLink = first.locator("a.platform-ranking-book");
    const authorLink = first.locator("a[data-author-link]");
    const bookHref = await bookLink.getAttribute("href");
    const authorHref = await authorLink.getAttribute("href");
    const bookUrl = new URL(bookHref, page.url()).href;
    const authorUrl = new URL(authorHref, page.url()).href;
    await expect(bookLink).toHaveAttribute("href", "#/book/" + encodeURIComponent(BOOK_ID));
    await expect(authorLink).toHaveAttribute("href", "#/author/" + encodeURIComponent(AUTHOR_SLUG));

    await bookLink.click();
    await expect(page).toHaveURL(bookUrl);
    await page.goto("/#/rankings");
    await bookLink.focus();
    await bookLink.press("Enter");
    await expect(page).toHaveURL(bookUrl);
    await page.goto("/#/rankings");
    await bookLink.focus();
    await page.keyboard.press("Tab");
    await expect(authorLink).toBeFocused();
    await authorLink.press("Enter");
    await expect(page).toHaveURL(authorUrl);
  });

  test("排行榜 tabs 支援 roving focus、手動啟用與 tabpanel 關聯", async ({ page, context }) => {
    await mockRanking(context);
    const rankingRequests = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname === "/api/rankings") rankingRequests.push(request.url());
    });
    await page.goto("/#/rankings");
    const tabs = page.getByRole("tab");
    const panel = page.getByRole("tabpanel");
    await expect(tabs).toHaveCount(4);
    await expect(tabs.nth(0)).toHaveAttribute("tabindex", "0");
    await expect(tabs.nth(1)).toHaveAttribute("tabindex", "-1");
    await expect(tabs.nth(0)).toHaveAttribute("aria-selected", "true");
    await expect(panel).toHaveAttribute("aria-labelledby", await tabs.nth(0).getAttribute("id"));
    for (let index = 0; index < 4; index += 1) {
      await expect(tabs.nth(index)).toHaveAttribute("aria-controls", "platform-ranking-result");
    }
    await expect.poll(() => rankingRequests.length).toBe(1);

    await tabs.nth(0).focus();
    await tabs.nth(0).press("ArrowRight");
    await expect(tabs.nth(1)).toBeFocused();
    await expect(tabs.nth(0)).toHaveAttribute("aria-selected", "true");
    await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "false");
    await expect.poll(() => rankingRequests.length).toBe(1);

    await page.keyboard.press("Enter");
    await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true");
    await expect(panel).toHaveAttribute("aria-labelledby", await tabs.nth(1).getAttribute("id"));
    await expect.poll(() => rankingRequests.length).toBe(2);
    expect(new URL(rankingRequests[1]).searchParams.get("kind")).toBe("new");

    await tabs.nth(1).press("Home");
    await expect(tabs.nth(0)).toBeFocused();
    await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true");
    await expect.poll(() => rankingRequests.length).toBe(2);
    await page.keyboard.press("Space");
    await expect(tabs.nth(0)).toHaveAttribute("aria-selected", "true");
    await expect(panel).toHaveAttribute("aria-labelledby", await tabs.nth(0).getAttribute("id"));
    await expect.poll(() => rankingRequests.length).toBe(3);

    await tabs.nth(0).press("End");
    await expect(tabs.nth(3)).toBeFocused();
    await expect(tabs.nth(0)).toHaveAttribute("aria-selected", "true");
    await tabs.nth(3).click();
    await expect(tabs.nth(3)).toHaveAttribute("aria-selected", "true");
    await expect.poll(() => rankingRequests.length).toBe(4);
  });

  test("作品連結可在新分頁直接開啟並在 reload 後保留作品路由", async ({ page, context }, testInfo) => {
    await mockRanking(context);
    await page.goto("/#/rankings");
    const bookLink = page.locator("a.platform-ranking-book").first();
    const href = await bookLink.getAttribute("href");
    const directUrl = new URL(href, page.url()).href;
    let opened;
    if (testInfo.project.name === "chromium") {
      const popup = context.waitForEvent("page");
      await bookLink.click({ modifiers: ["Control"] });
      opened = await popup;
      await opened.waitForLoadState();
      await expect(opened).toHaveURL(directUrl);
      await expect(page).toHaveURL(/#\/rankings$/);
    } else {
      opened = await context.newPage();
      await opened.goto(directUrl);
    }
    await expect(opened.locator(".platform-detail-title")).toContainText(LONG_TITLE);
    await opened.reload();
    await expect(opened).toHaveURL(directUrl);
    await expect(opened.locator(".platform-detail-title")).toContainText(LONG_TITLE);
    await opened.close();
  });

  test("匿名作者沒有假連結、作品與作者不巢狀，320px 長文字不造成橫向溢位", async ({ page, context }) => {
    await mockRanking(context);
    await page.setViewportSize({ width: 320, height: 800 });
    await page.goto("/#/rankings");
    await expect(page.locator("article.platform-ranking-item")).toHaveCount(2);
    const anonymous = page.locator("article.platform-ranking-item").nth(1);
    await expect(anonymous.locator("a[data-author-link]")).toHaveCount(0);
    await expect(anonymous.locator("span.platform-ranking-author")).toContainText("匿名作者");
    const semantics = await page.locator("article.platform-ranking-item").evaluateAll((items) => items.map((item) => ({
      nestedInteractive: item.querySelector("a a, a button, button a, button button") !== null,
      itemScrolls: item.scrollWidth > item.clientWidth,
    })));
    expect(semantics).toEqual([
      { nestedInteractive: false, itemScrolls: false },
      { nestedInteractive: false, itemScrolls: false },
    ]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
});
