import { test, expect } from "@playwright/test";

const json = (route, data, status = 200) => route.fulfill({
  status, contentType: "application/json", body: JSON.stringify(data),
});

function installShell(page, role = "super_admin") {
  page.route("**/api/auth/me", (route) => json(route, {
    authed: true, user: { id: 11, username: role === "super_admin" ? "最高管理員" : "一般管理員", role },
  }));
  page.route("**/api/voices", (route) => json(route, { voices: [] }));
  page.route("**/api/categories", (route) => json(route, { categories: [] }));
  page.route(/\/api\/books(\?.*)?$/, (route) => json(route, []));
  page.route("**/api/admin/dashboard", (route) => json(route, {
    books: { total: 0, by_status: {} }, users: { total: 1, by_role: { [role]: 1 } }, chapters: { audioReady: 0 },
  }));
  page.route(/\/api\/admin\/audit-logs\?/, (route) => json(route, {
    items: [{ id: 42, action: "set_user_status", actorLabel: "最高管理員", username: "最高管理員",
      target_type: "user", target_id: "12", created_at: "2026-09-05T10:00:00", detailsText: "{\"status\":\"disabled\"}" }],
    total: 1, page: 1, page_size: 50, total_pages: 1,
  }));
  page.route(/\/api\/admin\/audit-logs\/42$/, (route) => json(route, {
    id: 42, action: "set_user_status", actorLabel: "最高管理員", category: "governance",
    policyVersion: "R18-v1", targetType: "user", targetId: "12", createdAt: "2026-09-05T10:00:00",
    details: { status: "disabled" },
  }));
}

test.describe("R18 audit governance UX", () => {
  test("Super Admin can inspect a safe detail and request a bounded export", async ({ page }) => {
    installShell(page, "super_admin");
    let exportCalls = 0;
    page.route("**/api/admin/audit-logs/export", async (route) => {
      exportCalls += 1;
      return json(route, { items: [], total: 0, filters: { format: "json" }, policy: { purgeEnabled: false } });
    });
    await page.goto("/#/admin?tab=audit");
    await expect(page.locator("#audit-export")).toBeVisible();
    await page.locator('[data-audit-open="42"]').click();
    await expect(page.locator(".audit-detail-json")).toContainText('"status": "disabled"');
    await page.locator("#audit-detail-back").click();
    await page.locator("#audit-export").click();
    await expect.poll(() => exportCalls).toBe(1);
    await expect(page.locator(".toast-success").last()).toContainText("已匯出");
  });

  test("ordinary Admin can browse but cannot see the full-export control", async ({ page }) => {
    installShell(page, "admin");
    await page.goto("/#/admin?tab=audit");
    await expect(page.locator("#audit-export")).toHaveCount(0);
    await expect(page.locator('[data-audit-open="42"]')).toBeVisible();
  });

  test("audit detail and controls remain usable on mobile", async ({ page }) => {
    installShell(page, "super_admin");
    await page.goto("/#/admin?tab=audit");
    await page.locator('[data-audit-open="42"]').click();
    await expect(page.locator("#audit-detail-back")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });
});
