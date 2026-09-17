import { test, expect } from "@playwright/test";

test.use({ reducedMotion: "reduce" });

const json = (route, data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
const book = (id, extra = {}) => ({ id, title: `故事 ${id}`, synopsis: "一封未寄出的信，讓兩個陌生人在雨天的書店相遇。", category: "zh", serial: "連載", owner: "故事作者", hasPrologue: false, ...extra });
const home = (extra = {}) => ({ banners: [], categories: [{ id: 1, name: "愛情" }], continueReading: [], latest: [book("one")], rankings: [], completed: [], ...extra });
async function setup(page, data, user = null) {
  await page.route("**/api/auth/me", (r) => json(r, { authed: !!user, user }));
  await page.route("**/api/announcements/active", (r) => json(r, { items: [] }));
  await page.route("**/api/notifications**", (r) => json(r, { items: [], count: 0 }));
  await page.route("**/api/home", (r) => json(r, data));
}
async function noOverflow(page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
}

test("訪客少量內容先看到故事，不重複書牆；可搜尋與開啟作品", async ({ page }, testInfo) => {
  await setup(page, home({ rankings: [{ bid: "one", title: "故事 one" }], completed: [book("one")] }));
  await page.goto("/#/home");
  await expect(page.locator(".home-book-grid .platform-card")).toHaveCount(1);
  await expect(page.locator(".home-intro h1")).toHaveText("找一本故事，開始閱讀");
  await expect(page.locator(".home-resume")).toHaveCount(0);
  expect((await page.locator(".home-book-grid").boundingBox()).y).toBeLessThan(500);
  await expect(page.getByRole("link", { name: "瀏覽有聲書 →" })).toHaveAttribute("href", "#/audiobooks");
  await noOverflow(page);
  await page.screenshot({ path: `logs/home-after-${testInfo.project.name}-few.png`, fullPage: true });
  await page.locator(".platform-card-main").click();
  await expect(page).toHaveURL(/#\/book\/one$/);
  await page.goBack();
  await page.locator("#home-search-input").fill("雨天");
  await page.locator("#platform-home-search button").click();
  await expect(page).toHaveURL(/#\/search\?q=/);
});

test("沒有閱讀紀錄的登入者先選書，作者入口保持次要", async ({ page }) => {
  await setup(page, home(), { id: 8, username: "作者", role: "author" });
  await page.goto("/#/home");
  await expect(page.locator(".home-book-grid")).toBeVisible();
  await expect(page.locator(".home-resume")).toHaveCount(0);
  await expect(page.locator(".home-author-entry a")).toHaveAttribute("href", "#/mine");
  await noOverflow(page);
});

test("登入者優先繼續最近章節，重整與直接閱讀路由維持模式", async ({ page }, testInfo) => {
  const recent = book("resume", { readingProgress: { chapterSeq: 2, chapterTitle: "雨停之前", percent: 42, lastMode: "read" } });
  await setup(page, home({ continueReading: [recent, book("older")] }), { id: 8, username: "讀者", role: "reader" });
  await page.route("**/api/books/resume/read/2", (r) => json(r, { book: recent, chapter: { seq: 2, title: "雨停之前", text: "書店的門緩緩打開。", audio: "none" }, navigation: { previous: null, next: null, total: 1 } }));
  await page.route("**/api/books/resume/chapters/2", (r) => json(r, { analysis: null }));
  await page.route("**/api/me/progress", (r) => json(r, { items: [] }));
  await page.route("**/api/books/resume/view", (r) => json(r, { ok: true }));
  await page.goto("/#/home");
  await expect(page.locator(".home-resume")).toContainText("第 3 章〈雨停之前〉 · 純文字閱讀 · 42%");
  expect((await page.locator(".home-resume").boundingBox()).y).toBeLessThan((await page.locator(".home-discovery").boundingBox()).y);
  await noOverflow(page);
  await page.screenshot({ path: `logs/home-after-${testInfo.project.name}-returning.png`, fullPage: true });
  await page.reload();
  await page.getByRole("link", { name: "繼續閱讀", exact: true }).click();
  await expect(page).toHaveURL(/#\/read\/resume\/2\?mode=reading$/);
  await expect(page.locator(".reader-sheet")).toContainText("書店的門緩緩打開");
  await page.reload();
  await expect(page.locator(".reader-sheet")).toBeVisible();
});

test("舊或失效紀錄回到作品，訪客不顯示私人紀錄", async ({ page }) => {
  const data = home({ continueReading: [book("old")] });
  await setup(page, data, { id: 8, username: "讀者", role: "reader" });
  await page.goto("/#/home");
  await expect(page.getByRole("link", { name: "返回作品", exact: true })).toHaveAttribute("href", "#/book/old");
  await expect(page.getByRole("link", { name: "繼續閱讀", exact: true })).toHaveCount(0);
  await page.route("**/api/auth/me", (r) => json(r, { authed: false }));
  await page.reload();
  await expect(page.locator(".home-book-grid")).toBeVisible();
  await expect(page.locator(".home-resume")).toHaveCount(0);
});

test("大量內容只有六本；鍵盤切換清單、返回與重整保留選擇", async ({ page }, testInfo) => {
  await setup(page, home({ latest: Array.from({ length: 12 }, (_, i) => book(`new-${i}`)), rankings: [{ bid: "hot", title: "熱門故事" }], completed: [book("end")] }));
  await page.goto("/#/home");
  await expect(page.locator(".home-book-grid .platform-card")).toHaveCount(6);
  await page.screenshot({ path: `logs/home-after-${testInfo.project.name}-many.png`, fullPage: true });
  const hot = page.getByRole("link", { name: "熱門作品", exact: true });
  await hot.focus(); await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/collection=popular/);
  await expect(page.locator(".home-book-grid")).toContainText("熱門故事");
  await expect(hot).toHaveAttribute("aria-current", "page");
  await page.reload();
  await expect(page.locator(".home-book-grid")).toContainText("熱門故事");
  await page.getByRole("link", { name: "完結推薦", exact: true }).click();
  await expect(page.locator(".home-book-grid")).toContainText("故事 end");
  await page.goBack();
  await expect(page.locator(".home-book-grid")).toContainText("熱門故事");
  await noOverflow(page);
});

test("全空與單一清單空狀態不誤導，錯誤可原地重試", async ({ page }) => {
  await setup(page, home({ latest: [], categories: [] }));
  await page.goto("/#/home");
  await expect(page.locator(".home-empty")).toContainText("故事正在準備中");
  await expect(page.locator(".home-collections")).toHaveCount(0);
  await expect(page.locator(".platform-card")).toHaveCount(0);
  await page.route("**/api/home", (r) => json(r, { detail: "internal detail" }, 503));
  await page.getByRole("button", { name: "重新查看作品" }).click();
  await expect(page.locator(".platform-error")).toContainText("首頁目前無法載入");
  await expect(page.locator(".platform-error")).not.toContainText("internal detail");
  await page.route("**/api/home", (r) => json(r, home()));
  await page.getByRole("button", { name: "重新載入", exact: true }).click();
  await expect(page.locator(".home-book-grid")).toBeVisible();
  await page.getByRole("link", { name: "完結推薦", exact: true }).click();
  await expect(page.locator(".home-empty")).toContainText("目前沒有完結推薦的作品");
  await page.getByRole("link", { name: "看看其他作品" }).click();
  await expect(page.locator(".home-book-grid")).toBeVisible();
  await noOverflow(page);
});

test("載入可理解，離開首頁後晚到回應不覆蓋搜尋", async ({ page }) => {
  await setup(page, home());
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route("**/api/home", async (r) => { await gate; await json(r, home()); });
  await page.goto("/#/home");
  await expect(page.locator(".platform-loading")).toContainText("正在載入作品");
  await page.evaluate(() => { location.hash = "#/search"; });
  await expect(page.getByRole("heading", { name: "搜尋小說" })).toBeVisible();
  release();
  await page.waitForTimeout(150);
  await expect(page.locator(".home-content")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "搜尋小說" })).toBeVisible();
});
