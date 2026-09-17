import { test, expect } from "@playwright/test";

const json = (route, data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });

function installShellMocks(page, user = null, items = []) {
  let acknowledged = false;
  page.route("**/api/auth/me", (route) => json(route, user ? { authed: true, user } : { authed: false }));
  page.route("**/api/voices", (route) => json(route, { voices: [] }));
  page.route("**/api/categories", (route) => json(route, { categories: [] }));
  page.route(/\/api\/home(?:\?.*)?$/, (route) => json(route, { latest: [], completed: [], popular: [], banners: [], categories: [] }));
  page.route(/\/api\/books(?:\?.*)?$/, (route) => json(route, []));
  page.route("**/api/announcements/active", (route) => json(route, { items: acknowledged ? [] : items, limit: 50 }));
  page.route(/\/api\/announcements\/\d+\/acknowledge$/, (route) => { acknowledged = true; return json(route, { ok: true, acknowledged: true }); });
  page.route("**/api/admin/dashboard", (route) => json(route, { books: { total: 0, by_status: {} }, users: { total: 1, by_role: { admin: 1 } }, chapters: { audioReady: 0 } }));
  page.route(/\/api\/admin\/announcements(?:\?.*)?$/, (route) => json(route, { items: [{ id: 1, title: "後台公告", bodyText: "內容", audienceMode: "everyone", displayMode: "once_per_version", priority: 0, enabled: false, status: "disabled", displayVersion: 1, configVersion: 1, startAt: null, endAt: null, updatedAt: "2026-09-03T00:00:00Z" }], total: 1, page: 1, page_size: 20, total_pages: 1, totalPages: 1 }));
  return () => acknowledged;
}

test.describe("Platform announcements", () => {
  test("guest entry shows one safe text dialog and closes without navigation replay", async ({ page }) => {
    installShellMocks(page, null, [{ id: 1, displayVersion: 1, title: "<公告>", bodyText: "<script>不執行</script>", displayMode: "once_per_session", ctaLabel: "前往", ctaTarget: "#/privacy" }]);
    await page.goto("/#/home");
    await expect(page.locator(".announcement-modal")).toBeVisible();
    await expect(page.locator(".announcement-modal-title")).toHaveText("<公告>");
    await expect(page.locator(".announcement-modal-copy")).toHaveText("<script>不執行</script>");
    await expect(page.locator(".announcement-modal")).toHaveAttribute("role", "dialog");
    await page.locator(".announcement-modal-close").click();
    await expect(page.locator(".announcement-modal-overlay")).toHaveCount(0);
    await page.goto("/#/privacy");
    await expect(page.locator(".announcement-modal-overlay")).toHaveCount(0);
  });

  test("authenticated close acknowledges the display version and a re-entry has no stale popup", async ({ page }) => {
    const wasAcknowledged = installShellMocks(page, { id: 8, username: "讀者", role: "reader" }, [{ id: 2, displayVersion: 1, title: "登入公告", bodyText: "只顯示一次", displayMode: "once_per_version" }]);
    await page.goto("/#/home");
    await expect(page.locator(".announcement-modal")).toBeVisible();
    await page.locator(".announcement-modal-close").click();
    await expect.poll(wasAcknowledged).toBe(true);
    await page.goto("/#/privacy");
    await expect(page.locator(".announcement-modal-overlay")).toHaveCount(0);
  });

  test("Admin Content/Announcements shows bounded governance list without notification UI", async ({ page }) => {
    installShellMocks(page, { id: 10, username: "管理員", role: "admin" });
    await page.goto("/#/admin");
    await page.locator("[data-atab='announcements']").click();
    await expect(page.locator("#announcement-q")).toBeVisible();
    await expect(page.locator(".admin-announcement-row")).toContainText("後台公告");
    await expect(page.locator("#view-admin [data-notification-center]" )).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator(".admin-announcement-row")).toBeVisible();
  });
});
