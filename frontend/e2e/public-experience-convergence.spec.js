import { test, expect } from "@playwright/test";

const fulfill = (route, data) => route.fulfill({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify(data),
});

test.describe("Public Experience UI convergence", () => {
  test("公開 shell 在手機版以可關閉導覽呈現", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/#/home");
    const toggle = page.getByRole("button", { name: "開啟導覽選單" });
    await expect(toggle).toBeVisible();
    await expect(page.locator("#main-nav")).toBeHidden();
    await toggle.click();
    await expect(page.getByRole("button", { name: "關閉導覽選單" })).toBeVisible();
    await expect(page.locator("#main-nav")).toBeVisible();
    await expect(page.locator("#main-nav")).toContainText("首頁");
    await expect.poll(async () => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.getByRole("button", { name: "關閉導覽選單" }).press("Escape");
    await expect(page.getByRole("button", { name: "開啟導覽選單" })).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator("#main-nav")).toBeHidden();
  });

  test("公開卡片提供真正的作品連結，首頁避免重複作品", async ({ page }) => {
    await page.route("**/api/auth/me", (route) => fulfill(route, { authed: false }));
    await page.route("**/api/home", (route) => fulfill(route, {
      banners: [],
      categories: [{ id: 1, name: "奇幻" }],
      rankings: [{ bid: "same", title: "同一本作品", synopsis: "作品簡介", owner: "作者", category: "zh", category_name: "奇幻" }],
      latest: [{ id: "same", title: "同一本作品", synopsis: "作品簡介", owner: "作者", category: "zh", categoryName: "奇幻" }],
      completed: [{ id: "other", title: "另一部作品", synopsis: "另一個簡介", owner: "作者", category: "zh", categoryName: "奇幻" }],
    }));
    await page.goto("/#/home");
    await expect(page.getByRole("link", { name: "閱讀 同一本作品" })).toHaveAttribute("href", "#/book/same");
    await expect(page.locator(".platform-card")).toHaveCount(1);
    await expect(page.locator(".platform-card-main")).toHaveCount(1);
    await expect(page.locator(".platform-card").first()).not.toHaveAttribute("role", "link");
    await page.getByRole("link", { name: "完結推薦", exact: true }).click();
    await expect(page.getByRole("link", { name: "閱讀 另一部作品" })).toHaveAttribute("href", "#/book/other");
    await expect(page.locator(".platform-card")).toHaveCount(1);
  });

  test("公開搜尋錯誤只提供安全的下一步", async ({ page }) => {
    let requests = 0;
    await page.route("**/api/auth/me", (route) => fulfill(route, { authed: false }));
    await page.route("**/api/search**", (route) => { requests += 1; return route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "provider secret must not leak" }),
    }); });
    await page.goto("/#/search?q=錯誤");
    await expect(page.locator("#platform-search-result")).toContainText("搜尋目前無法完成");
    await expect(page.locator("#platform-search-result")).not.toContainText("provider secret");
    await expect(page.getByRole("button", { name: "重新載入" })).toBeVisible();
    await page.getByRole("button", { name: "重新載入" }).click();
    await expect.poll(() => requests).toBe(2);
    await expect(page.locator("#platform-search-result")).toContainText("搜尋目前無法完成");
  });

  test("公開搜尋在支援尺寸都能重排且維持操作空間", async ({ page }) => {
    for (const width of [320, 375, 390, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/#/search");
      await expect(page.getByRole("heading", { name: "搜尋小說" })).toBeVisible();
      await expect(page.getByRole("searchbox", { name: "搜尋小說" })).toBeVisible();
      await expect.poll(async () => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    }
  });
});
