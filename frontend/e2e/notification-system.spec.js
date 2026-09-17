import { test, expect } from "@playwright/test";

async function openWorkspaceOnMobile(page) {
  const toggle = page.getByRole("button", { name: "開啟導覽選單" });
  if (await toggle.isVisible()) await toggle.click();
  const group = page.locator("#nav-workspace-group");
  if (await group.isVisible() && (await group.getAttribute("open")) === null) await group.locator("summary").click();
}

const json = (route, data, status = 200, headers = {}) => route.fulfill({
  status, contentType: "application/json", headers, body: JSON.stringify(data),
});

function installNotificationMocks(page, { delayRead = false } = {}) {
  const mutations = [];
  let releaseRead;
  const readGate = new Promise((resolve) => { releaseRead = resolve; });
  page.route("**/api/auth/me", (route) => json(route, { authed: true, user: { id: 7, username: "通知讀者", role: "reader" } }));
  page.route("**/api/voices", (route) => json(route, { voices: [] }));
  page.route("**/api/categories", (route) => json(route, { categories: [] }));
  page.route("**/api/announcements/active", (route) => json(route, { items: [] }));
  page.route(/\/api\/home(?:\?.*)?$/, (route) => json(route, { latest: [], completed: [], popular: [], banners: [], categories: [] }));
  page.route(/\/api\/notifications\/unread-count$/, (route) => json(route, { count: 2 }));
  page.route(/\/api\/notifications(?:\?.*)?$/, (route) => json(route, {
    items: [{ id: 1, eventType: "content_request.rejected", category: "review", title: "申請需要補充資料", body: "請補充摘要", targetRoute: "#/home", createdAt: "2026-09-03T00:00:00Z", readAt: null },
      { id: 2, eventType: "generation.ready", category: "generation", title: "有聲書已完成", body: "可以開始播放", targetRoute: "#/home", createdAt: "2026-09-02T00:00:00Z", readAt: "2026-09-02T01:00:00Z" }],
    page: 1, page_size: 20, total: 2, total_pages: 1, has_next: false, unread: 2,
  }));
  page.route(/\/api\/notifications\/\d+\/read$/, async (route) => {
    mutations.push(route.request().url());
    if (delayRead) await readGate;
    return json(route, { ok: true });
  });
  page.route("**/api/notifications/read-all", (route) => { mutations.push(route.request().url()); return json(route, { ok: true }); });
  return { mutations, releaseRead: () => releaseRead?.() };
}

test.describe("Notification System", () => {
  test("authenticated bell/popover does not auto-read and center supports explicit read", async ({ page }) => {
    const { mutations, releaseRead } = installNotificationMocks(page, { delayRead: true });
    await page.goto("/#/home");
    await expect(page.locator("#nav-notifications")).toBeVisible();
    await expect(page.locator("#nav-notifications .notification-icon")).toBeVisible();
    await expect(page.locator("#notification-badge")).toHaveText("2");
    await expect(page.locator(".home-intro")).toBeVisible();
    expect(mutations).toEqual([]);

    await page.locator("#nav-notifications").click();
    await expect(page.locator(".notification-popover")).toContainText("申請需要補充資料");
    expect(mutations).toEqual([]);
    await page.locator(".notification-popover-item").first().click();
    await expect.poll(() => mutations.length).toBe(1);
    expect(mutations[0]).toContain("/api/notifications/1/read");
    await expect(page).toHaveURL(/#\/home$/);

    await page.goto("/#/notifications?filter=unread");
    releaseRead();
    await expect.poll(() => page.evaluate(() => location.hash)).toBe("#/notifications?filter=unread");
    await expect(page.locator(".notification-center")).toBeVisible();
    await expect(page.locator(".platform-notice-list")).toContainText("申請需要補充資料");
    await page.locator("#notifications-read-all").click();
    await expect.poll(() => mutations.length).toBe(2);
  });

  test("guest does not see private notification navigation", async ({ page }) => {
    await page.route("**/api/auth/me", (route) => json(route, { authed: false }));
    await page.route("**/api/voices", (route) => json(route, { voices: [] }));
    await page.route("**/api/categories", (route) => json(route, { categories: [] }));
    await page.route("**/api/announcements/active", (route) => json(route, { items: [] }));
    await page.route(/\/api\/home(?:\?.*)?$/, (route) => json(route, { latest: [], completed: [], popular: [], banners: [], categories: [] }));
    await page.goto("/#/home");
    await expect(page.locator("#nav-notifications")).toBeHidden();
    await page.goto("/#/notifications");
    await expect(page.locator(".platform-login-empty")).toBeVisible();
  });

  test("authenticated notification target preserves Back/Forward and reload", async ({ page }) => {
    installNotificationMocks(page);
    await page.goto("/#/notifications?filter=unread");
    await expect(page.locator(".notification-center")).toBeVisible();
    await page.locator("[data-notification-target]").first().click();
    await expect.poll(() => page.evaluate(() => location.hash)).toBe("#/home");
    await page.goBack();
    await expect.poll(() => page.evaluate(() => location.hash)).toBe("#/notifications?filter=unread");
    await expect(page.locator(".notification-center")).toBeVisible();
    await page.goForward();
    await expect.poll(() => page.evaluate(() => location.hash)).toBe("#/home");
    await page.reload();
    await expect.poll(() => page.evaluate(() => location.hash)).toBe("#/home");
    await expect(page.locator(".home-intro")).toBeVisible();
  });
});
