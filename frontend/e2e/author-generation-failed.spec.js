import { test, expect } from "@playwright/test";

test.describe("生成失敗 UX（V4 Blackbox remediation）", () => {
  test("job failed 時章節顯示「生成失敗」＋錯誤訊息＋重試入口", async ({ page }) => {
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    const book = {
      id: "b-fail", bid: "b-fail", title: "失敗測試書", synopsis: "",
      category: "en", vocabLevel: "AUTO", categories: ["en"], serial: "連載",
      status: "draft", settings: {}, voices: { 旁白: "af-heart", _english: "af-heart" }, voicePrefs: {},
      coverImage: null, ownerId: 7, rejectReason: "", totalChars: 10,
      created: "2026-08-17", defaultVoiceId: "af-heart", audioMode: "single", audioSettingsVersion: 2,
      workflow: {
        mode: "single", hasDefaultVoice: true, aiProviderAvailable: true, ttsProviderAvailable: true,
        state: "audio_failed", needsAction: true, nextAction: "generate", providerUnavailable: false,
        total: 1, analyzed: 0, ready: 0,
        chapters: { 0: { state: "audio_failed", nextAction: "generate", providerUnavailable: false, error: "provider 回應 HTTP 400" } },
      },
      chapters: [{ id: 1, seq: 0, title: "序章", chars: 10, status: "pending", audio: "none", error: "", textHash: "h1" }],
    };
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 7, username: "生成操作員", role: "admin" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [{ id: "af-heart", name: "Heart", lang: "en" }] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route(/\/api\/books\/b-fail$/, (route) => fulfill(route, book));

    await page.goto("/#/detail/b-fail");
    await expect(page.locator("#view-chapters")).toBeVisible();
    const row = page.locator(".chapter-item");
    // 產品化錯誤狀態，不是退回「可生成」
    await expect(row.locator(".badge")).toContainText("生成失敗");
    await expect(row.locator(".ch-error")).toContainText("provider 回應 HTTP 400");
    // 重試入口
    await expect(row.locator("[data-v4act='generate']")).toContainText("重試生成");
  });
});
