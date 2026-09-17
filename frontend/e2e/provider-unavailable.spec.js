import { test, expect } from "@playwright/test";

test.describe("朗讀服務不可用 — 產品狀態（Phase 15b）", () => {
  function installMocks(page) {
    const book = {
      id: "b-outage", bid: "b-outage", title: "服務測試書", synopsis: "",
      category: "vocab", vocabLevel: "AUTO", categories: ["vocab"], serial: "連載",
      status: "draft", settings: {}, voices: {}, voicePrefs: {},
      coverImage: null, ownerId: 7, rejectReason: "", totalChars: 10,
      created: "2026-08-17", defaultVoiceId: null, audioMode: "single", audioSettingsVersion: 1,
      workflow: {
        mode: "single", hasDefaultVoice: false, aiProviderAvailable: true, ttsProviderAvailable: false,
        state: "provider_unavailable", needsAction: true, nextAction: "configure_tts_provider",
        providerUnavailable: true, total: 1, analyzed: 0, ready: 0,
        chapters: { 0: { state: "provider_unavailable", nextAction: "configure_tts_provider", providerUnavailable: true } },
      },
      chapters: [{ id: 1, seq: 0, title: "序章", chars: 10, status: "pending", audio: "none", error: "", textHash: "h1" }],
    };
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 7, username: "作者三", role: "author" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [{ id: "v-zh-1", name: "曉曉", lang: "zh" }] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books\?mine=1/, (route) => fulfill(route, []));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route(/\/api\/books\/b-outage$/, (route) => fulfill(route, book));
  }

  test("provider 不可用時以產品狀態呈現，不暴露底層錯誤", async ({ page }) => {
    await installMocks(page);
    await page.goto("/#/detail/b-outage");
    await expect(page.locator("#view-chapters")).toBeVisible();
    // 產品狀態 banner，不是 provider/Tunnel/Qwen 錯誤
    const banner = page.locator("#ch-provider-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("朗讀服務暫時無法使用");
    await expect(banner).toContainText("不是你的作品設定問題");
    // 章節列沒有生成/分析按鈕，顯示「服務暫停」
    await expect(page.locator(".ch-row", { hasText: "序章" })).toContainText("服務暫停");
    await expect(page.locator("[data-v4act]")).toHaveCount(0);
  });
});
