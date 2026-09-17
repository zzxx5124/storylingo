import { test, expect } from "@playwright/test";

test.describe("收藏按鈕即時狀態（V4 Blackbox remediation）", () => {
  function installMocks(page, { initialFavorite = false, failNext = false } = {}) {
    let favorite = initialFavorite;
    let fail = failNext;
    const fulfill = (route, data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
    const book = {
      id: "b-fav", bid: "b-fav", title: "收藏測試書", synopsis: "", category: "zh",
      vocabLevel: "AUTO", categories: ["zh"], serial: "連載", status: "approved",
      settings: {}, voices: {}, voicePrefs: {}, coverImage: null, ownerId: 1, rejectReason: "",
      totalChars: 10, created: "2026-08-17", defaultVoiceId: null, audioMode: "single",
      chapters: [{ id: 1, seq: 0, title: "序章", chars: 10, status: "pending", audio: "none", error: "", textHash: "h1" }],
    };
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 9, username: "讀者", role: "reader" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route(/\/api\/books\/b-fav$/, (route) => fulfill(route, book));
    page.route("**/api/me/library?kind=favorite", (route) => fulfill(route, { items: favorite ? [{ id: "b-fav" }] : [] }));
    page.route("**/api/books/b-fav/recommendations", (route) => fulfill(route, { items: [] }));
    page.route("**/api/books/b-fav/comments", (route) => fulfill(route, { items: [] }));
    page.route("**/api/books/b-fav/favorite", async (route) => {
      if (fail) { fail = false; return route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "server boom" }) }); }
      favorite = route.request().method() === "POST";
      return fulfill(route, { ok: true, favorite });
    });
    return {
      getFavorite: () => favorite,
      setFail: () => { fail = true; },
    };
  }

  test("加入收藏 → 按鈕立即變「已收藏」；再點取消 → 變回「加入收藏」", async ({ page }) => {
    await installMocks(page);
    await page.goto("/#/book/b-fav");
    const btn = page.locator("#platform-favorite");
    await expect(btn).toHaveText("加入收藏");
    await btn.click();
    await expect(btn).toHaveText("已收藏");
    await expect(page.locator(".toast-success").last()).toContainText("已加入收藏");
    await btn.click();
    await expect(btn).toHaveText("加入收藏");
    await expect(page.locator(".toast-success").last()).toContainText("已取消收藏");
  });

  test("已收藏狀態載入時按鈕顯示「已收藏」；失敗時 rollback 並顯示錯誤", async ({ page }) => {
    const state = installMocks(page, { initialFavorite: true });
    await page.goto("/#/book/b-fav");
    const btn = page.locator("#platform-favorite");
    await expect(btn).toHaveText("已收藏");
    state.setFail();
    await btn.click();
    // 失敗 → 按鈕維持「已收藏」（rollback），顯示錯誤 toast
    await expect(btn).toHaveText("已收藏");
    await expect(page.locator(".toast-error").last()).toContainText("server boom");
  });
});