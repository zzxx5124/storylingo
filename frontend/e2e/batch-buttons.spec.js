import { test, expect } from "@playwright/test";

test.describe("批次按鈕與批次完成刷新（V4 Blackbox remediation）", () => {
  function chapter(seq, title, state, extra = {}) {
    return { id: seq + 1, seq, title, chars: 10, status: "pending", audio: "none", error: "", textHash: `h${seq}`, ...extra };
  }
  function makeBook(chapters, wfChapters, mode = "multi") {
    const cArr = Object.entries(wfChapters).map(([seq, st]) => chapter(Number(seq), `章${seq}`, st));
    return {
      id: "b-batch", bid: "b-batch", title: "批次測試書", synopsis: "",
      category: "zh", vocabLevel: "AUTO", categories: ["zh"], serial: "連載",
      status: "draft", settings: {}, voices: {}, voicePrefs: {},
      coverImage: null, ownerId: 7, rejectReason: "", totalChars: 30,
      created: "2026-08-17", defaultVoiceId: null, audioMode: mode, audioSettingsVersion: 1,
      workflow: {
        mode, hasDefaultVoice: false, aiProviderAvailable: true, ttsProviderAvailable: true,
        state: "needs_analysis", needsAction: true, nextAction: "analyze", providerUnavailable: false,
        total: cArr.length, analyzed: 0, ready: 0,
        chapters: Object.fromEntries(Object.entries(wfChapters).map(([seq, st]) => [seq, { ...st, providerUnavailable: false }])),
      },
      chapters: cArr,
    };
  }

  test("0 剩餘時批次按鈕 disabled；有剩餘時顯示剩餘數並可點", async ({ page }) => {
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 7, username: "生成操作員", role: "admin" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [{ id: "v-zh-1", name: "曉曉", lang: "zh" }] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    // 0 剩餘：全部章節皆完成（multi 全 analyzed+ready）
    page.route(/\/api\/books\/b-batch$/, (route) => fulfill(route, makeBook(
      [], { 0: { state: "audio_ready", nextAction: null }, 1: { state: "audio_ready", nextAction: null } },
    )));
    await page.goto("/#/detail/b-batch");
    await expect(page.locator("#view-chapters")).toBeVisible();
    await expect(page.locator("#btn-analyze-all")).toBeDisabled();
    await expect(page.locator("#btn-tts-all")).toBeDisabled();

    // 有剩餘：1 章待分析 + 1 章可生成
    await page.route(/\/api\/books\/b-batch$/, (route) => fulfill(route, makeBook(
      [], { 0: { state: "needs_analysis", nextAction: "analyze" }, 1: { state: "ready_to_generate", nextAction: "generate" } },
    )));
    await page.reload();
    await expect(page.locator("#btn-analyze-all")).toBeEnabled();
    await expect(page.locator("#btn-analyze-all")).toHaveText("分析剩餘 1 章");
    await expect(page.locator("#btn-tts-all")).toBeEnabled();
    await expect(page.locator("#btn-tts-all")).toHaveText("生成剩餘 1 章");
  });

  test("批次分析完成後自動刷新 workflow（不需手動重整）", async ({ page }) => {
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    let pollCount = 0;
    let batchPosted = false;
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 7, username: "生成操作員", role: "admin" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [{ id: "v-zh-1", name: "曉曉", lang: "zh" }] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route(/\/api\/books\/b-batch\/analysis\/batch$/, (route) => {
      if (route.request().method() === "POST") { batchPosted = true; }
      return fulfill(route, { jobId: 99, status: "queued" });
    });
    page.route(/\/api\/books\/b-batch$/, (route) => {
      pollCount += 1;
      // 初始 / 第一次 poll：待分析；之後：分析完成（needs_voice_configuration）
      const done = batchPosted && pollCount >= 2;
      return fulfill(route, makeBook(
        [], { 0: done ? { state: "needs_voice_configuration", nextAction: "configure_voices" } : { state: "needs_analysis", nextAction: "analyze" } },
      ));
    });

    await page.goto("/#/detail/b-batch");
    await expect(page.locator("#view-chapters")).toBeVisible();
    await expect(page.locator('[data-v4act="analyze"]').first()).toBeVisible();

    await page.locator("#btn-analyze-all").click();
    expect(batchPosted).toBe(true);
    // 不需手動重整：章節動作自動從「開始分析角色」變成「設定各角色聲線」
    await expect(page.locator('[data-v4act="configure-voices"]').first()).toBeVisible({ timeout: 20000 });
    await expect(page.locator('[data-v4act="analyze"]')).toHaveCount(0);
    // 完成 toast
    await expect(page.locator(".toast").last()).toContainText("批次分析完成", { timeout: 20000 });
  });
});
