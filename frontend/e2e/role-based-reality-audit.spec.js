import { test, expect } from "@playwright/test";

function fulfill(route, data, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
}

async function mockShell(page, { role = "reader", authed = true } = {}) {
  await page.route("**/api/auth/me", (route) => fulfill(route, authed ? {
    authed: true,
    user: { id: 7, username: `audit-${role}`, role },
    profile: { displayName: "稽核帳號", avatar: null },
    authMethods: { google: { available: false } },
  } : {
    authed: false,
    authMethods: { google: { available: false } },
  }));
  await page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
  await page.route("**/api/home", (route) => fulfill(route, {
    banners: [], categories: [], continueReading: [], rankings: [], latest: [], completed: [],
  }));
}

test.describe("Role-based break-the-product reality audit", () => {
  test("anonymous login entry fails closed for Google and preserves password path", async ({ page }) => {
    await mockShell(page, { authed: false });
    await page.goto("/#/home");
    await page.locator("#btn-login").click();
    await expect(page.locator("#btn-google-login")).toBeHidden();
    await expect(page.locator("#google-unavailable-note")).toBeVisible();
    await expect(page.locator("#login-username")).toBeVisible();
    await expect(page.locator("#login-password")).toBeVisible();
    await expect(page.locator("#btn-profile")).toBeHidden();
  });

  test("reader natural entry exposes settings but no workspace privilege", async ({ page }) => {
    await mockShell(page, { role: "reader" });
    await page.route("**/api/auth/profile", (route) => fulfill(route, {
      account: { id: 7, username: "audit-reader", email: "reader@example.test", role: "reader", status: "active", emailVerified: true, hasPasswordCredential: true, externalIdentities: [] },
      authMethods: { google: { available: false } },
      publicProfile: { displayName: "稽核帳號", bio: "", avatar: null }, authorProfiles: [],
    }));
    await page.goto("/#/home");
    await expect(page.locator("#nav-mine")).toBeHidden();
    await expect(page.locator("#nav-admin")).toBeHidden();
    await page.locator("#btn-profile").click();
    await expect(page).toHaveURL(/#\/settings$/);
    await expect(page.locator("#settings-page")).toBeVisible();
  });

  for (const role of ["author", "reviewer", "admin", "super_admin"]) {
    test(`${role} natural workspace entry keeps role-specific navigation`, async ({ page }) => {
      await mockShell(page, { role });
      await page.goto("/#/home");
      if (await page.locator("#nav-toggle").isVisible()) {
        await page.locator("#nav-toggle").click();
        await expect(page.locator("#mobile-workspace")).toBeVisible();
      } else {
        await expect(page.locator("#nav-workspace-group")).toBeVisible();
      }
      if (role === "author") {
        await expect(page.locator("#nav-mine")).toBeVisible();
        await expect(page.locator("#nav-requests")).toBeVisible();
        await expect(page.locator("#nav-admin")).toBeHidden();
      } else {
        await expect(page.locator("#nav-admin")).toBeVisible();
        await expect(page.locator("#nav-requests")).toBeVisible();
      }
      await expect(page.locator("#btn-profile")).toBeVisible();
    });
  }
});
