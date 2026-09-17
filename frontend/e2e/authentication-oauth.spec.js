import { test, expect } from "@playwright/test";

function fulfill(route, data, status = 200) {
  return route.fulfill({
    status, contentType: "application/json", body: JSON.stringify(data),
  });
}

test.describe("Authentication/OAuth approved UX", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/auth/me", (route) => fulfill(route, {
      authed: false,
      authMethods: { google: { available: true } },
    }));
    await page.goto("/#/home");
    await expect(page.locator("#btn-login")).toBeVisible();
    await expect(page.locator("#app-bootstrap")).toBeHidden();
  });

  test("Google entry is first, password path remains, and forgot password stays generic", async ({ page }) => {
    await page.locator("#btn-login").click();
    await expect(page.locator("#btn-google-login")).toBeVisible();
    await expect(page.locator("#login-username")).toBeVisible();
    await expect(page.locator("#login-password")).toBeVisible();
    await page.locator("#btn-forgot-password").click();
    await expect(page.locator("#forgot-password-panel")).toBeVisible();
    await page.route("**/api/auth/forgot-password", (route) => fulfill(route, {
      ok: true, message: "如果帳號符合條件，系統會寄出重設說明",
    }));
    await page.locator("#forgot-email").fill("unknown@example.test");
    await page.locator("#btn-forgot-submit").click();
    await expect(page.locator(".toast-success").last()).toContainText("如果帳號符合條件");
  });

  test("unconfigured Google shows a safe explanation and keeps password login", async ({ page }) => {
    await page.unroute("**/api/auth/me");
    await page.route("**/api/auth/me", (route) => fulfill(route, {
      authed: false,
      authMethods: { google: { available: false } },
    }));
    await page.reload();
    await page.locator("#btn-login").click();
    await expect(page.locator("#btn-google-login")).toBeHidden();
    await expect(page.locator("#google-unavailable-note")).toBeVisible();
    await expect(page.locator("#login-username")).toBeVisible();
    await expect(page.locator("#login-password")).toBeVisible();
  });

  test("password registration collects email and leaves account pending verification", async ({ page }) => {
    let payload = null;
    await page.route("**/api/auth/register", (route) => {
      payload = route.request().postDataJSON();
      return fulfill(route, { id: 9, username: "browser-reader", verificationRequired: true }, 201);
    });
    await page.locator("#btn-login").click();
    await page.locator("#btn-register-open").click();
    await page.locator("#register-username").fill("browser-reader");
    await page.locator("#register-email").fill("browser@example.test");
    await page.locator("#register-password").fill("secret123");
    await page.locator("#register-confirm").fill("secret123");
    await page.locator("#register-terms").check();
    await page.locator("#register-privacy").check();
    await page.locator("#register-age").check();
    await page.locator("#btn-register-submit").click();
    await expect(page.locator("#register-modal")).toBeHidden();
    await expect(page.locator("#login-modal")).toBeVisible();
    expect(payload).toMatchObject({
      username: "browser-reader",
      email: "browser@example.test",
      password: "secret123",
    });
  });

  test("reset link opens a one-time form and clears the token after submit", async ({ page }) => {
    let payload = null;
    await page.route("**/api/auth/reset-password", (route) => {
      payload = route.request().postDataJSON();
      return fulfill(route, { ok: true });
    });
    await page.goto("/#/reset-password?token=one-time-browser-token");
    await expect(page.locator("#login-modal")).toBeVisible();
    await expect(page.locator("#reset-password-panel")).toBeVisible();
    await page.locator("#reset-password-new").fill("changed123");
    await page.locator("#reset-password-confirm").fill("changed123");
    await page.locator("#btn-reset-submit").click();
    await expect(page.locator(".toast-success").last()).toContainText("密碼已更新");
    expect(payload).toEqual({ token: "one-time-browser-token", newPassword: "changed123" });
    await expect(page).toHaveURL(/#\/home$/);
  });

  test("used reset link shows actionable safe feedback without exposing token details", async ({ page }) => {
    await page.route("**/api/auth/reset-password", (route) => fulfill(route, {
      detail: "重設連結無效或已過期",
    }, 400));
    await page.goto("/#/reset-password?token=used-browser-token");
    await expect(page.locator("#reset-password-panel")).toBeVisible();
    await page.locator("#reset-password-new").fill("changed123");
    await page.locator("#reset-password-confirm").fill("changed123");
    await page.locator("#btn-reset-submit").click();
    const feedback = page.locator("#reset-password-feedback");
    await expect(feedback).toBeVisible();
    await expect(feedback).toContainText("此重設連結已失效或已被使用");
    await expect(feedback).toContainText("重新申請密碼重設連結");
    await expect(feedback).not.toContainText("used-browser-token");
    await expect(page.locator("#reset-password-panel")).toBeVisible();
  });

  test("reset password validation feedback stays distinct from an invalid link", async ({ page }) => {
    await page.route("**/api/auth/reset-password", (route) => fulfill(route, {
      detail: "密碼至少 8 個字元",
    }, 400));
    await page.goto("/#/reset-password?token=validation-browser-token");
    await page.locator("#reset-password-new").fill("short");
    await page.locator("#reset-password-confirm").fill("short");
    await page.locator("#btn-reset-submit").click();
    await expect(page.locator("#reset-password-feedback")).toHaveText("密碼至少 8 個字元");
  });
});
