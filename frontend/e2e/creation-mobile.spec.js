import { test, expect } from "@playwright/test";

test.describe("行動版 Creator smoke（Phase 15c release gate）", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("手機上：空狀態建立作品 → 寫第一章 → 章節出現", async ({ page }) => {
    const state = { chapters: [] };
    const bookPayload = () => ({
      id: "b-mob", bid: "b-mob", title: "行動作品",
      synopsis: "", category: "vocab", vocabLevel: "AUTO", categories: ["vocab"], serial: "連載",
      status: "draft", chapters: state.chapters, settings: {}, voicePrefs: {}, voices: {},
      coverImage: null, ownerId: 7, rejectReason: "", totalChars: 0, created: "2026-08-17",
      defaultVoiceId: null, audioMode: "single", audioSettingsVersion: 1,
    });
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 7, username: "手機作者", role: "author" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books\?mine=1/, (route) => fulfill(route, []));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route("**/api/books/manual", (route) => fulfill(route, bookPayload()));
    page.route(/\/api\/books\/b-mob\/chapters$/, (route) => {
      const payload = JSON.parse(route.request().postData() || "{}");
      state.chapters = [{ id: 1, seq: 0, title: payload.title || "新章節", chars: (payload.text || "").length, status: "pending", audio: "none", error: "", textHash: "h1" }];
      return fulfill(route, bookPayload());
    });
    page.route(/\/api\/books\/b-mob$/, (route) => fulfill(route, bookPayload()));

    await page.goto("/#/mine");
    // 空狀態 CTA 在手機上可用
    await page.locator("#btn-new-book-empty").click();
    await expect(page.locator("#newbook-modal")).toBeVisible();
    await page.locator("#nb-title").fill("行動作品");
    await page.locator("#nb-submit").click();
    await expect(page.locator("#chapter-modal")).toBeVisible();
    await page.locator("#new-ch-title").fill("第一章 起點");
    await page.locator("#new-ch-text").fill("手機上寫下的第一章。");
    await page.locator("#new-ch-submit").click();
    await expect(page.locator(".chapter-item").first()).toContainText("第一章 起點");
  });
});
