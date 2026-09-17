import { test, expect } from "@playwright/test";

test.describe("Admin Operations（pagination / jobs clear / users search）V4 remediation", () => {
  function installMocks(page) {
    const fulfill = (route, data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
    const jobsPage1 = { items: [
      { id: 1, job_type: "audio_single", status: "success", progress: 100, error: "", created_at: "2026-08-19T00:00:00" },
      { id: 2, job_type: "audio_multi", status: "failed", progress: 100, error: "provider down", created_at: "2026-08-19T00:00:00" },
      { id: 3, job_type: "speaker_analysis", status: "running", progress: 40, error: "", created_at: "2026-08-19T00:00:00" },
    ], total: 4, page: 1, page_size: 3, total_pages: 2 };
    const usersPage1 = { items: [
      { id: 10, username: "admin", email: "", role: "admin", account_status: "active", created_at: "2026-08-01T00:00:00", last_login_at: "2026-08-18T00:00:00", books: 0 },
      { id: 11, username: "author1", email: "a@x.com", role: "author", account_status: "active", created_at: "2026-08-02T00:00:00", last_login_at: null, books: 3 },
      { id: 12, username: "reader1", email: "r@x.com", role: "reader", account_status: "disabled", created_at: "2026-08-03T00:00:00", last_login_at: null, books: 0 },
    ], total: 4, page: 1, page_size: 3, total_pages: 2 };
    const apps = { items: [
      { id: 1, pen_name: "阿明", username: "user1", status: "pending", bio: "寫作", created_at: "2026-08-10T00:00:00" },
      { id: 2, pen_name: "小華", username: "user2", status: "approved", bio: "出版", created_at: "2026-08-11T00:00:00" },
    ], total: 2, page: 1, page_size: 20, total_pages: 1 };
    const audit = { items: [
      { action: "create_tts_provider", username: "admin", target_type: "tts_provider", target_id: 1, created_at: "2026-08-12T00:00:00", details: "{}" },
      { action: "set_user_status", username: "admin", target_type: "user", target_id: 12, created_at: "2026-08-12T01:00:00", details: "{}" },
    ], total: 2, page: 1, page_size: 50, total_pages: 1 };
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 10, username: "admin", role: "admin" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route("**/api/admin/dashboard", (route) => fulfill(route, { books: { total: 0, by_status: {} }, users: { total: 4, by_role: {} }, chapters: { audioReady: 0 } }));
    page.route(/\/api\/admin\/jobs\?/, (route) => fulfill(route, jobsPage1));
    page.route(/\/api\/admin\/jobs\/clear$/, (route) => fulfill(route, { ok: true, cleared: 3 }));
    page.route(/\/api\/admin\/jobs\/\d+$/, (route) => fulfill(route, { ok: true }));
    page.route(/\/api\/admin\/users\?/, (route) => fulfill(route, usersPage1));
    page.route(/\/api\/admin\/users\/\d+\/status$/, (route) => fulfill(route, { ok: true }));
    page.route(/\/api\/admin\/author-applications\?/, (route) => fulfill(route, apps));
    page.route(/\/api\/admin\/audit-logs\?/, (route) => fulfill(route, audit));
  }

  test("類別 tab：管理員可批次套用動態分類", async ({ page }) => {
    const fulfill = (route, data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
    const adminBooks = { items: [
      { id: "b-1", title: "批次書一", status: "draft", chapterCount: 1, owner: "bulk-author", created: "2026-08-01" },
      { id: "b-2", title: "批次書二", status: "draft", chapterCount: 2, owner: "bulk-author", created: "2026-08-02" },
    ] };
    page.route("**/api/admin/books", (route) => fulfill(route, adminBooks));
    page.route("**/api/admin/categories", (route) => fulfill(route, { categories: [{ id: 21, name: "新分類", sort: 0, bookCount: 0 }] }));
    let bulkRequests = 0;
    page.route("**/api/admin/books/bulk-category", async (route) => {
      bulkRequests += 1;
      await new Promise((resolve) => setTimeout(resolve, 250));
      return fulfill(route, { ok: true, updated: 2, categoryId: 21, bookIds: ["b-1", "b-2"] });
    });
    await installMocks(page);
    await page.goto("/#/admin");
    await page.locator('[data-atab="categories"]').click();
    await expect(page.locator("[data-admin-book-select]")).toHaveCount(2);
    await page.locator("#admin-bulk-category-apply").click();
    await expect(page.locator(".toast-error").last()).toContainText("請先選擇要套用的書籍");
    await page.locator("[data-admin-book-select]").nth(0).check();
    await page.locator("#admin-bulk-category-apply").click();
    await expect(page.locator(".toast-error").last()).toContainText("請先選擇分類");
    expect(bulkRequests).toBe(0);
    await page.locator("[data-admin-book-select]").nth(1).check();
    await page.locator("#admin-bulk-category").selectOption("21");
    const request = page.waitForRequest("**/api/admin/books/bulk-category");
    await page.locator("#admin-bulk-category-apply").click();
    await expect(page.locator("#admin-bulk-category-apply")).toBeDisabled();
    await request;
    await expect(page.locator(".toast-success").last()).toContainText("已套用分類至 2 本書");
    expect(bulkRequests).toBe(1);
  });

  test("jobs tab：分頁資訊顯示、批次清除需確認、running 任務無清除鈕", async ({ page }) => {
    await installMocks(page);
    await page.goto("/#/admin");
    await page.locator('[data-atab="jobs"]').click();
    await expect(page.locator('[data-job-id="1"]')).toContainText("#1 audio_single");
    // 分頁資訊
    await expect(page.locator(".pager-info")).toContainText("第 1 / 2 頁 · 共 4 筆");
    // 成功/失敗任務有清除鈕；running 任務沒有
    await expect(page.locator('[data-job-clear="1"]')).toBeVisible();
    await expect(page.locator('[data-job-clear="2"]')).toBeVisible();
    await expect(page.locator('[data-job-clear="3"]')).toHaveCount(0);
    // 批次清除（需 confirm dialog）
    page.on("dialog", (d) => d.accept());
    await page.locator("#job-clear-failed").click();
    await expect(page.locator(".toast-success").last()).toContainText("已清除失敗/取消任務歷史");
  });

  test("audit tab：顯示分頁與篩選，無刪除按鈕", async ({ page }) => {
    await installMocks(page);
    await page.goto("/#/admin");
    await page.locator('[data-atab="audit"]').click();
    await expect(page.locator(".arev-row").filter({ hasText: "create_tts_provider" })).toBeVisible();
    await expect(page.locator(".pager-info")).toContainText("共 2 筆");
    await expect(page.locator('[data-audit-delete]')).toHaveCount(0);
  });

  test("authors tab：狀態篩選 + 分頁", async ({ page }) => {
    await installMocks(page);
    await page.goto("/#/admin");
    await page.locator('[data-atab="authors"]').click();
    await expect(page.locator(".arev-row").filter({ hasText: "阿明" })).toBeVisible();
    await expect(page.locator(".pager-info")).toContainText("共 2 筆");
    await page.locator("#author-status-filter").selectOption("approved");
    await expect(page.locator(".arev-row").filter({ hasText: "小華" })).toBeVisible();
  });

  test("users tab：搜尋輸入、分頁、狀態切換", async ({ page }) => {
    await installMocks(page);
    await page.goto("/#/admin");
    await page.locator('[data-atab="users"]').click();
    await expect(page.locator(".arev-row").filter({ hasText: "author1" })).toBeVisible();
    // 停用狀態顯示
    await expect(page.locator(".arev-row").filter({ hasText: "reader1" })).toContainText("已停用");
    // 狀態切換按鈕（啟用/停用）存在
    await expect(page.locator('[data-ustatus="11"]')).toBeVisible();
    page.on("dialog", (d) => d.accept());
    await page.locator('[data-ustatus="11"]').click();
    await expect(page.locator(".toast-success").last()).toContainText("帳號已停用");
  });
});
