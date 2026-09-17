import { test, expect } from "@playwright/test";

const fulfill = (route, data, status = 200) => route.fulfill({
  status,
  contentType: "application/json",
  body: JSON.stringify(data),
});

function installAdminMocks(page) {
  page.route("**/api/auth/me", (route) => fulfill(route, {
    authed: true,
    user: { id: 1, username: "console-admin", role: "admin" },
  }));
  page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
  page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
  page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
  page.route("**/api/admin/dashboard", (route) => fulfill(route, {
    books: { total: 12, by_status: { approved: 7, submitted: 2 } },
    users: { total: 9, by_role: { admin: 1, reviewer: 2 } },
    chapters: { audioReady: 4 },
  }));
  page.route("**/api/admin/overview", (route) => fulfill(route, {
    asOf: "2026-09-03T10:00:00Z",
    users: { total: 9, byRole: { admin: 1, reviewer: 2 } },
    books: { total: 12, published: 7 },
    governance: { pendingAuthorApplications: 2, activeContentRequests: 1 },
    generation: {
      queuedAI: 2,
      queuedTTS: 1,
      summary: {
        providers: [{ name: "Local TTS", serviceType: "TTS", enabled: true, activeCount: 1, maxConcurrency: 2, queueCount: 1, healthState: "healthy" }],
        workers: [{ active: true }],
        staleWorkerCount: 0,
      },
    },
    alerts: [{ severity: "warning", label: "Local TTS 容量已滿", scope: "TTS", count: 1, href: "#/admin?tab=generation" }],
  }));
  page.route(/\/api\/admin\/users\?.*/, (route) => fulfill(route, {
    items: [{ id: 4, username: "book-admin", email: "admin@example.test", role: "admin", account_status: "active", created_at: "2026-09-01", ownedBookCount: 0 }],
    total: 1, page: 1, page_size: 20, total_pages: 1,
  }));
  page.route(/\/api\/admin\/books\?.*/, (route) => fulfill(route, {
    items: [{
      id: "book-console-1", title: "後台作品", owner: "owner-account", ownerId: 22,
      ownerAccount: { id: 22, username: "owner-account" },
      authorProfile: { id: 8, displayName: "公開署名" },
      publicationState: "published", generationState: "ready", chapterCount: 3,
      audioReadyCount: 3, requestState: { publish: "APPROVED", unpublish: null, audiobook: "APPROVED" },
      updatedAt: "2026-09-03T09:00:00Z",
    }],
    total: 1, page: 1, page_size: 20, total_pages: 1,
  }));
}

test.describe("Admin Console vNext", () => {
  test("Overview is bounded, actionable, and preserves non-sensitive route state", async ({ page }) => {
    installAdminMocks(page);
    await page.goto("/#/admin?tab=overview");
    await expect(page.locator("[data-admin-overview]")).toBeVisible();
    await expect(page.locator(".admin-overview-card").first()).toContainText("9");
    await expect(page.locator(".admin-alert[data-admin-overview-tab='generation']")).toContainText("容量已滿");

    await page.locator("[data-admin-overview-tab='users']").first().click();
    await expect(page.locator("#user-q")).toBeVisible();
    expect(page.url()).toContain("tab=users");
    expect(page.url()).not.toContain("q=");

    await page.reload();
    await expect(page.locator("#user-q")).toBeVisible();
    await expect(page.locator(".arev-row").filter({ hasText: "book-admin" })).toBeVisible();
  });

  test("Books projection keeps owner, attribution, publication, request, and generation state separate", async ({ page }) => {
    installAdminMocks(page);
    await page.goto("/#/admin?tab=books");
    await expect(page.locator("[data-admin-book-row='book-console-1']")).toContainText("Owner：owner-account");
    await expect(page.locator("[data-admin-book-row='book-console-1']")).toContainText("Attribution：公開署名");
    await expect(page.locator("[data-admin-book-row='book-console-1']")).toContainText("Publish APPROVED");
    await expect(page.locator("[data-admin-book-row='book-console-1']")).toContainText("Audiobook APPROVED");
    await expect(page.locator("[data-admin-book-row='book-console-1']")).not.toContainText("chapters");

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(overflow).toBe(false);
  });
});
