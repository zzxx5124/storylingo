import { test, expect } from "@playwright/test";

async function openWorkspaceOnMobile(page) {
  const toggle = page.getByRole("button", { name: "開啟導覽選單" });
  if (await toggle.isVisible()) await toggle.click();
  const group = page.locator("#nav-workspace-group");
  if (await group.isVisible() && (await group.getAttribute("open")) === null) await group.locator("summary").click();
}

test.describe("作者申請（Phase 15a）", () => {
  async function mockSession(page, { role = "reader" } = {}) {
    await page.route("**/api/auth/me", (route) => route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ authed: true, user: { id: 7, username: role === "reader" ? "讀者一" : "作者一", role } }),
    }));
    await page.route("**/api/voices", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ voices: [] }) }));
    await page.route("**/api/categories", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ categories: [] }) }));
    await page.route(/\/api\/books($|\?)/, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) }));
  }

  test("讀者可從正常 nav 找到作者申請並送出後看到審核中", async ({ page }) => {
    await mockSession(page);
    await page.route("**/api/authors/application", (route) => route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify({ application: null }),
    }));
    await page.route("**/api/authors/apply", (route) => route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify({ id: 1, status: "pending" }),
    }));
    await page.goto("/#/home");
    await openWorkspaceOnMobile(page);
    // 入口在正常 nav，不是藏在頁面底部
    await expect(page.locator("#nav-apply-author")).toBeVisible();
    await page.locator("#nav-apply-author").click();
    await expect(page.locator("#apply-author-modal")).toBeVisible();
    await expect(page.locator("#apply-form")).toBeVisible();
    await page.locator("#apply-pen-name").fill("新作者");
    await page.locator("#apply-rights").check();
    await page.locator("#apply-submit").click();
    await expect(page.locator("#apply-status")).toBeVisible();
    await expect(page.locator("#apply-status")).toContainText("審核中");
    await expect(page.locator("#apply-form")).toBeHidden();
  });

  test("審核中的申請再次開啟時顯示狀態而不重複表單", async ({ page }) => {
    await mockSession(page);
    await page.route("**/api/authors/application", (route) => route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ application: { id: 1, status: "pending", pen_name: "新作者" } }),
    }));
    await page.goto("/#/home");
    await openWorkspaceOnMobile(page);
    await page.locator("#nav-apply-author").click();
    await expect(page.locator("#apply-status")).toBeVisible();
    await expect(page.locator("#apply-status")).toContainText("審核中");
    await expect(page.locator("#apply-form")).toBeHidden();
  });

  test("被拒絕的申請顯示原因並可重新申請", async ({ page }) => {
    await mockSession(page);
    await page.route("**/api/authors/application", (route) => route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ application: { id: 2, status: "rejected", reject_reason: "請補作者簡介", pen_name: "筆名" } }),
    }));
    await page.goto("/#/home");
    await openWorkspaceOnMobile(page);
    await page.locator("#nav-apply-author").click();
    await expect(page.locator("#apply-status")).toBeVisible();
    await expect(page.locator("#apply-status")).toContainText("已拒絕");
    await expect(page.locator("#apply-status")).toContainText("請補作者簡介");
    await expect(page.locator("#apply-form")).toBeVisible();
  });

  test("作者帳號不會看到申請入口", async ({ page }) => {
    await mockSession(page, { role: "author" });
    await page.goto("/#/home");
    await expect(page.locator("#nav-apply-author")).toBeHidden();
  });
});
