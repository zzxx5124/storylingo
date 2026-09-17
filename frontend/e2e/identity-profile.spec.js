import { test, expect } from "@playwright/test";

const author = {
  publicId: "ap-browser",
  slug: "browser-author",
  displayName: "瀏覽作者",
  bio: "作者介紹",
  status: "active",
  link: "/authors/browser-author",
  legacy: false,
};

const book = {
  id: "browser-book",
  title: "作者導覽測試書",
  synopsis: "公開內容",
  serial: "連載",
  category: "zh",
  categories: ["zh"],
  owner: author.displayName,
  author,
  status: "approved",
  chapters: [],
};

function fulfill(route, data) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(data),
  });
}

async function mockPlatformShell(page, user = { id: 7, username: "login-only", role: "reader" }) {
  await page.route("**/api/auth/me", (route) => fulfill(route, {
    authed: true,
    user,
    profile: { displayName: "帳號顯示名", avatar: null },
  }));
  await page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
  await page.route("**/api/home", (route) => fulfill(route, {
    banners: [],
    categories: [],
    continueReading: [],
    rankings: [],
    latest: [book],
    completed: [],
  }));
}

async function mockSettingsEditor(page) {
  const state = {
    fail: false,
    conflict: false,
    writes: 0,
    profile: {
      account: { id: 7, username: "login-only", email: "old@example.com", role: "reader", status: "active", emailVerified: true, hasPasswordCredential: true, externalIdentities: [] },
      authMethods: { google: { available: false } },
      publicProfile: { displayName: "帳號顯示名", bio: "原始簡介", avatar: null },
      authorProfiles: [],
      profileRevision: "revision-1",
    },
  };
  await mockPlatformShell(page);
  await page.route("**/api/auth/profile", async (route) => {
    if (route.request().method() === "PATCH") {
      state.writes += 1;
      await new Promise((resolve) => setTimeout(resolve, 250));
      if (state.conflict) return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "個人設定已在其他分頁更新" }) });
      const payload = route.request().postDataJSON() || {};
      state.profile = { ...state.profile, publicProfile: { ...state.profile.publicProfile, displayName: payload.displayName, bio: payload.bio }, profileRevision: `revision-${state.writes + 1}` };
      return fulfill(route, state.profile);
    }
    if (state.fail) return route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "暫時無法載入" }) });
    return fulfill(route, state.profile);
  });
  await page.goto("/#/home");
  await page.locator("#btn-profile").click();
  await expect(page.locator("#settings-page")).toBeVisible();
  return state;
}

test.describe("Identity Profile 公開導覽與 account-facing 設定", () => {
  test("書卡作者連結不會被書卡 click handler 攔截，並能進入作者頁", async ({ page }) => {
    await mockPlatformShell(page);
    await page.route("**/api/authors/browser-author", (route) => fulfill(route, {
      author,
      works: [],
    }));
    await page.goto("/#/home");
    const authorLink = page.locator("[data-author-link]").filter({ hasText: author.displayName }).first();
    await expect(authorLink).toBeVisible();
    await authorLink.click();
    await expect(page).toHaveURL(/#\/author\/browser-author$/);
    await expect(page.locator(".author-profile-hero h2")).toContainText(author.displayName);
    await expect(page.locator(".author-profile-bio")).toHaveText("作者介紹");
  });

  test("作者頁 deep link/reload 不會落回私有書架", async ({ page }) => {
    await page.route("**/api/auth/me", (route) => fulfill(route, { authed: false }));
    await page.route("**/api/authors/browser-author", (route) => fulfill(route, {
      author,
      works: [],
    }));
    await page.goto("/#/author/browser-author");
    await expect(page.locator("#view-bookshelf")).toBeHidden();
    await expect(page.locator("#view-platform")).toBeVisible();
    await expect(page.locator(".author-profile-hero h2")).toContainText(author.displayName);
  });

  test("登入者可以開啟並儲存 account-facing profile，不會把 login username 當公開名稱", async ({ page }) => {
    await mockPlatformShell(page);
    let patchPayload = null;
    await page.route("**/api/auth/profile", (route) => {
      if (route.request().method() === "PATCH") {
        patchPayload = route.request().postDataJSON();
      return fulfill(route, {
          account: { id: 7, username: "login-only", email: "new@example.com", role: "reader", status: "active" },
          authMethods: { google: { available: false } },
          publicProfile: { displayName: "新的公開名稱", avatar: null },
          authorProfiles: [],
        });
      }
      return fulfill(route, {
        account: { id: 7, username: "login-only", email: "old@example.com", role: "reader", status: "active" },
        authMethods: { google: { available: false } },
        publicProfile: { displayName: "帳號顯示名", avatar: null },
        authorProfiles: [],
      });
    });
    await page.goto("/#/home");
    await page.locator("#btn-profile").click();
    await expect(page).toHaveURL(/#\/settings$/);
    await expect(page.locator("#settings-page")).toBeVisible();
    await page.locator("#settings-display-name").fill("新的公開名稱");
    await page.locator("#settings-email").fill("new@example.com");
    await page.locator("#settings-save").click();
    await expect(page.locator("#settings-status")).toContainText("設定已載入");
    expect(patchPayload).toEqual({ displayName: "新的公開名稱", bio: "", email: "new@example.com" });
    await expect(page.locator("#auth-user")).toContainText("login-only");
  });

  test("個人設定在手機寬度維持分區與可用寬度", async ({ page }) => {
    await mockPlatformShell(page);
    await page.route("**/api/auth/profile", (route) => fulfill(route, {
      account: { id: 7, username: "login-only", email: "old@example.com", role: "reader", status: "active", emailVerified: true, hasPasswordCredential: true, externalIdentities: [] },
      authMethods: { google: { available: false } },
      publicProfile: { displayName: "帳號顯示名", bio: "介紹", avatar: null },
      authorProfiles: [],
    }));
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/#/home");
    await page.locator("#btn-profile").click();
    await expect(page.locator("#settings-page")).toBeVisible();
    await expect(page.getByRole("heading", { name: "個人設定" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Public Profile 公開身份" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Author Profile 作者身份" })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(overflow).toBe(false);
  });

  test("個人設定載入失敗不可儲存，可重試", async ({ page }) => {
    const state = await mockSettingsEditor(page);
    state.fail = true;
    await page.evaluate(() => { location.hash = "#/settings"; });
    await page.waitForTimeout(50);
    await page.reload();
    await expect(page.locator("#settings-status")).toContainText("無法載入");
    await expect(page.locator("#settings-save")).toBeDisabled();
    state.fail = false;
    await page.locator("#settings-retry").click();
    await expect(page.locator("#settings-save")).toBeEnabled();
  });

  test("個人設定防止重複提交，重新整理後找回草稿", async ({ page }) => {
    const state = await mockSettingsEditor(page);
    await page.locator("#settings-display-name").fill("尚未儲存的名稱");
    await page.locator("#settings-save").dblclick();
    await expect(page.locator("#settings-status")).toContainText("設定已載入");
    expect(state.writes).toBe(1);
    await page.locator("#settings-display-name").fill("重新整理仍要保留");
    page.on("dialog", (dialog) => dialog.accept());
    await page.reload();
    await expect(page.locator("#settings-display-name")).toHaveValue("重新整理仍要保留");
  });

  test("個人設定衝突保留輸入，可明確載入目前版本", async ({ page }) => {
    const state = await mockSettingsEditor(page);
    await page.locator("#settings-display-name").fill("我的修改");
    state.conflict = true;
    await page.locator("#settings-save").click();
    await expect(page.locator("#settings-status")).toContainText("其他分頁更新");
    await expect(page.locator("#settings-display-name")).toHaveValue("我的修改");
    state.conflict = false;
    page.on("dialog", (dialog) => dialog.accept());
    await page.locator("#settings-retry").click();
    await expect(page.locator("#settings-display-name")).toHaveValue("帳號顯示名");
  });

  test("個人設定慢儲存完成後不會改寫已離開的頁面", async ({ page }) => {
    const state = await mockSettingsEditor(page);
    await page.locator("#settings-display-name").fill("離開前的修改");
    await page.locator("#settings-save").click();
    await expect(page.locator("#settings-save")).toBeDisabled();
    await page.evaluate(() => { location.hash = "#/home"; });
    await expect(page.locator("#view-platform")).toBeVisible();
    await page.waitForTimeout(400);
    expect(state.writes).toBe(1);
    await expect(page.locator("#view-settings")).toBeHidden();
  });
});
