import { test, expect } from "@playwright/test";

test.describe("播放器逐句字幕（V4 canonical analysis，remediation）", () => {
  test("播放器從 chapter data 的 V4 canonical analysis 渲染逐句字幕", async ({ page }) => {
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    const book = {
      id: "b-play", bid: "b-play", title: "播放測試書", synopsis: "",
      category: "zh", vocabLevel: "AUTO", categories: ["zh"], serial: "連載",
      status: "draft", settings: {}, voices: { 旁白: "v-zh-1", _english: "en-US-JennyNeural" }, voicePrefs: {},
      coverImage: null, ownerId: 7, rejectReason: "", totalChars: 30,
      created: "2026-08-17", defaultVoiceId: null, audioMode: "multi", audioSettingsVersion: 1,
      workflow: {
        mode: "multi", hasDefaultVoice: false, aiProviderAvailable: true, ttsProviderAvailable: true,
        state: "audio_ready", needsAction: false, nextAction: null, providerUnavailable: false,
        total: 1, analyzed: 1, ready: 1,
        chapters: { 0: { state: "audio_ready", nextAction: null, providerUnavailable: false } },
      },
      chapters: [{ id: 1, seq: 0, title: "序章", chars: 30, status: "analyzed", audio: "ready", error: "", textHash: "h1" }],
    };
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 7, username: "作者一", role: "author" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [{ id: "v-zh-1", name: "曉曉", lang: "zh" }] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route(/\/api\/books\/b-play$/, (route) => fulfill(route, book));
    // 關鍵：chapter data 回傳 V4 canonical analysis（含逐句 segments）
    page.route(/\/api\/books\/b-play\/chapters\/0$/, (route) => fulfill(route, {
      analysis: {
        analysisId: 5, status: "ready", schemaVersion: 2, chapterKey: "ck-1", sourceTextHash: "h1",
        speakers: [{ name: "旁白" }, { name: "主角" }],
        segments: [
          { id: "s1", text: "夜色漸深。", speaker: "旁白", emotion: { value: "neutral" } },
          { id: "s2", text: "「我們走吧。」", speaker: "主角", emotion: { value: "happy" } },
        ],
      },
      timing: { segments: [{ dur: 2.0 }, { dur: 2.5 }] },
    }));
    page.route(/\/api\/books\/b-play\/audio\/0$/, (route) => route.fulfill({
      status: 200, contentType: "audio/mpeg",
      body: Buffer.from("ID3" + "\x00".repeat(256)),
    }));

    await page.goto("/#/detail/b-play");
    await expect(page.locator("#view-chapters")).toBeVisible();
    await page.locator("[data-play]").first().click();
    await expect(page.locator("#view-player")).toBeVisible();
    // 逐句字幕由 canonical analysis 渲染
    await expect(page.locator("#transcript .seg")).toHaveCount(2);
    await expect(page.locator("#transcript")).toContainText("夜色漸深。");
    await expect(page.locator("#transcript")).toContainText("我們走吧。");
    await page.locator("#transcript .seg").first().evaluate((element) => element.classList.add("active"));
    await expect(page.locator("#transcript .seg.active .body")).toHaveCSS("font-weight", "600");
  });

});
