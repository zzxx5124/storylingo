import { test, expect } from "@playwright/test";

test.describe("公開搜尋 visibility 前端呈現（V4 remediation）", () => {
  test("搜尋頁只渲染 API 回傳的已上架作品；草稿詳情顯示錯誤頁", async ({ page }) => {
    const fulfill = (route, data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
    const published = [
      { id: "b-pub-1", title: "已上架作品", synopsis: "公開內容", category: "zh", categories: ["zh"], serial: "連載", cover: null, owner: "作者甲", status: "approved", audioReadyCount: 0 },
    ];
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 9, username: "讀者", role: "reader" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route(/\/api\/search\?/, (route) => fulfill(route, { items: published, total: 1, page: 1, page_size: 20, total_pages: 1 }));
    // 草稿詳情 → 後端回 404 → 前端顯示錯誤頁（不洩漏書名）
    page.route(/\/api\/books\/b-draft-1$/, (route) => fulfill(route, { detail: "找不到書" }, 404));

    await page.goto("/#/search?q=公開作品");
    await expect(page.locator(".platform-card")).toHaveCount(1);
    await expect(page.locator(".platform-card")).toContainText("已上架作品");
    // 結果清單不該出現草稿書名
    await expect(page.locator(".platform-card-grid")).not.toContainText("草稿書");

    // 直接開啟草稿詳情 → 錯誤頁，無書名洩漏
    await page.goto("/#/book/b-draft-1");
    await expect(page.locator(".platform-error")).toBeVisible();
    await expect(page.locator("body")).not.toContainText("草稿書");
  });

  test("首頁/排行區塊只顯示 API 回傳的已上架作品", async ({ page }) => {
    const fulfill = (route, data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 9, username: "讀者", role: "reader" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route("**/api/home", (route) => fulfill(route, {
      banners: [], categories: [], continueReading: [], rankings: [], completed: [],
      latest: [{ id: "b-pub-2", title: "最新上架", synopsis: "", category: "zh", categories: ["zh"], serial: "連載", cover: null, owner: "作者乙", status: "approved", audioReadyCount: 0 }],
    }));
    await page.goto("/#/home");
    await expect(page.locator(".platform-card")).toHaveCount(1);
    await expect(page.locator(".platform-card")).toContainText("最新上架");
  });
});