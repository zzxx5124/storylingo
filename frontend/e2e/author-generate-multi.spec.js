import { test, expect } from "@playwright/test";

test.describe("作者有聲生成 — 多語者模式（Phase 15b）", () => {
  function installMocks(page) {
    const state = {
      analysis: "none", // none | running | ready
      voicesAssigned: false,
      genStep: 0, // 0=none, 1=generating(一次), 2=ready
      genPolled: false,
    };
    const speakers = [{ name: "旁白" }, { name: "主角", gender: "女" }];
    const chapter = () => ({
      id: 1, seq: 0, title: "序章", chars: 10,
      status: state.analysis === "ready" ? "analyzed" : "pending",
      audio: state.genStep === 2 ? "ready" : "none",
      error: "", textHash: "h1",
      activeAudioGenerationId: state.genStep === 2 ? 8 : null,
    });
    const workflow = () => {
      let chapterState = "needs_analysis";
      let nextAction = "analyze";
      if (state.genStep === 1) { chapterState = "audio_generating"; nextAction = null; }
      else if (state.genStep === 2) { chapterState = "audio_ready"; nextAction = null; }
      else if (state.analysis === "running") { chapterState = "analysis_running"; nextAction = null; }
      else if (state.analysis === "ready") {
        chapterState = state.voicesAssigned ? "ready_to_generate" : "needs_voice_configuration";
        nextAction = state.voicesAssigned ? "generate" : "configure_voices";
      }
      return {
        mode: "multi", hasDefaultVoice: false, aiProviderAvailable: true, ttsProviderAvailable: true,
        state: chapterState, needsAction: !!nextAction, nextAction, providerUnavailable: false,
        total: 1, analyzed: state.analysis === "ready" ? 1 : 0,
        ready: state.genStep === 2 ? 1 : 0,
        chapters: { 0: { state: chapterState, nextAction, providerUnavailable: false } },
      };
    };
    const book = () => ({
      id: "b-gen-m", bid: "b-gen-m", title: "多角色測試書", synopsis: "",
      category: "vocab", vocabLevel: "AUTO", categories: ["vocab"], serial: "連載",
      status: "draft", settings: {}, voices: state.voicesAssigned ? { 旁白: "v-zh-1", 主角: "v-zh-2", _english: "en-US-JennyNeural" } : {},
      voicePrefs: {}, coverImage: null, ownerId: 7, rejectReason: "", totalChars: 10,
      created: "2026-08-17", defaultVoiceId: null, audioMode: "multi", audioSettingsVersion: 1,
      workflow: workflow(), chapters: [chapter()],
    });
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 7, username: "生成操作員", role: "admin" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [
      { id: "v-zh-1", name: "曉曉", lang: "zh" },
      { id: "v-zh-2", name: "雲希", lang: "zh" },
    ] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books\?mine=1/, (route) => fulfill(route, []));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route(/\/api\/books\/b-gen-m$/, (route) => {
      if (route.request().method() === "GET" && state.genStep === 1) {
        if (state.genPolled) state.genStep = 2;
        else state.genPolled = true;
      }
      return fulfill(route, book());
    });
    page.route(/\/api\/books\/b-gen-m\/voices\/ai-match$/, (route) => {
      if (route.request().method() === "POST") {
        return fulfill(route, {
          matches: [{ speaker: "主角", voice_id: "v-zh-2", reason: "test" }],
          total: 2, assigned: 1,
        });
      }
      return fulfill(route, { matches: [], total: 0, assigned: 0 });
    });
    page.route(/\/api\/books\/b-gen-m\/voices$/, (route) => {
      if (route.request().method() === "PUT") {
        state.voicesAssigned = true;
        return fulfill(route, { voices: { 旁白: "v-zh-1", 主角: "v-zh-2" }, prefs: {} });
      }
      return fulfill(route, { voices: {}, prefs: {} });
    });
    page.route(/\/api\/books\/b-gen-m\/chapters\/0\/analysis$/, (route) => {
      if (route.request().method() === "POST") {
        state.analysis = "ready";
        return fulfill(route, { analysisId: 5, jobId: 6, status: "queued" });
      }
      if (state.analysis === "ready") {
        return fulfill(route, {
          status: "ready", analysisId: 5, schemaVersion: 2, chapterKey: "ck-1",
          sourceTextHash: "h1", speakers,
          segments: [
            { id: "s1", text: "內容", speaker: "旁白", emotion: { value: "neutral" } },
            { id: "s2", text: "「出發。」", speaker: "主角", emotion: { value: "happy" } },
          ],
        });
      }
      return fulfill(route, { analysisId: null, status: "none" });
    });
    page.route(/\/api\/books\/b-gen-m\/chapters\/0\/audio-generations$/, (route) => {
      if (route.request().method() === "POST") {
        state.genStep = 1;
        state.genPolled = false;
        return fulfill(route, { generationId: 8, jobId: 9, status: "queued" });
      }
      return fulfill(route, { items: [] });
    });
    page.route(/\/api\/books\/b-gen-m\/chapters\/0$/, (route) => fulfill(route, { analysis: { segments: [] }, timing: null }));
    page.route(/\/api\/books\/b-gen-m\/audio\/0$/, (route) => route.fulfill({
      status: 200, contentType: "audio/mpeg",
      body: Buffer.from("ID3" + "\x00".repeat(256)),
    }));
    return state;
  }

  test("分析 → 角色與情緒 → 聲線對映 → 生成 → 可播放", async ({ page }) => {
    const state = installMocks(page);
    await page.goto("/#/detail/b-gen-m");
    await expect(page.locator("#view-chapters")).toBeVisible();
    // 多語者第一步：開始分析角色
    await expect(page.locator("[data-v4act='analyze']").first()).toBeVisible();
    await page.locator("[data-v4act='analyze']").first().click();
    // 分析完成 → 展開章節、顯示角色聲線卡片與情緒摘要
    await expect(page.locator(".speaker-card", { hasText: "主角" })).toBeVisible({ timeout: 15000 });
    await expect(page.locator(".detail-section", { hasText: "本章情緒" })).toContainText("開心");
    // 為角色指派聲線並儲存
    const speakerCards = page.locator(".speaker-card");
    await speakerCards.filter({ hasText: "旁白" }).locator("select[data-spk]").selectOption("v-zh-1");
    await speakerCards.filter({ hasText: "主角" }).locator("select[data-spk]").selectOption("v-zh-2");
    await page.locator("[data-save-voices]").click();
    // 聲線完成 → 生成音訊（mock 的 PUT voices 已切換 voicesAssigned）
    await expect(page.locator("[data-v4act='generate']").first()).toBeVisible({ timeout: 15000 });
    await page.locator("[data-v4act='generate']").first().click();
    await expect(page.locator(".badge", { hasText: "可播放" }).first()).toBeVisible({ timeout: 15000 });
    await expect(page.locator("[data-play]").first()).toBeVisible();
    await page.locator("[data-play]").first().click();
    await expect(page.locator("#view-player")).toBeVisible();
  });

  test("AI 匹配語者顯示真實計數（已自動匹配 X / Y）", async ({ page }) => {
    const state = installMocks(page);
    await page.goto("/#/detail/b-gen-m");
    await expect(page.locator("#view-chapters")).toBeVisible();
    await page.locator("[data-v4act='analyze']").first().click();
    await expect(page.locator(".speaker-card", { hasText: "主角" })).toBeVisible({ timeout: 15000 });
    // 展開的章節 detail 有「AI 匹配語者」按鈕
    await page.locator("[data-ai-match-voices]").first().click();
    // 成功訊息含實際數量，而非無條件「AI 匹配完成」
    await expect(page.locator(".toast-success").last()).toContainText("已自動匹配 1 / 2 個角色", { timeout: 10000 });
  });
});
