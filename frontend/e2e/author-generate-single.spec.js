import { test, expect } from "@playwright/test";

function wavSilence() {
  const sampleRate = 8000;
  const sampleCount = sampleRate / 2;
  const dataSize = sampleCount * 2;
  const buffer = Buffer.alloc(44 + dataSize);
  buffer.write("RIFF", 0);
  buffer.writeUInt32LE(36 + dataSize, 4);
  buffer.write("WAVE", 8);
  buffer.write("fmt ", 12);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(sampleRate * 2, 28);
  buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36);
  buffer.writeUInt32LE(dataSize, 40);
  return buffer;
}

test.describe("作者有聲生成 — 單語者模式（Phase 15b）", () => {
  function installMocks(page, initial) {
    const state = {
      mode: "single",
      hasVoice: initial.hasVoice ?? false,
      genStep: initial.genStep ?? 0, // 0=none, 1=generating(回一次), 2=ready
    };
    const lang = initial.lang || "zh";
    const enVoice = initial.enVoice || "";
    const voiceList = initial.voices || (lang === "en"
      ? [{ id: "af-heart", name: "Heart", lang: "en" }, { id: "am-adam", name: "Adam", lang: "en" }]
      : [{ id: "v-zh-1", name: "曉曉", lang: "zh" }]);
    const chapter = () => ({
      id: 1, seq: 0, title: "序章", chars: 10,
      status: "pending", audio: state.genStep === 2 ? "ready" : "none",
      error: "", textHash: "h1",
      activeAudioGenerationId: state.genStep === 2 ? 7 : null,
    });
    const workflow = () => {
      let chapterState = "needs_voice_configuration";
      let nextAction = "set_voice";
      if (state.genStep === 1) { chapterState = "audio_generating"; nextAction = null; }
      else if (state.genStep === 2) { chapterState = "audio_ready"; nextAction = null; }
      else if (state.hasVoice) { chapterState = "ready_to_generate"; nextAction = "generate"; }
      return {
        mode: state.mode, hasDefaultVoice: state.hasVoice,
        aiProviderAvailable: true, ttsProviderAvailable: true,
        state: chapterState, needsAction: !!nextAction, nextAction,
        providerUnavailable: false, total: 1, analyzed: 0,
        ready: state.genStep === 2 ? 1 : 0,
        chapters: { 0: { state: chapterState, nextAction, providerUnavailable: false } },
      };
    };
    const book = () => ({
      id: "b-gen-1", bid: "b-gen-1", title: "生成測試書", synopsis: "",
      category: lang, vocabLevel: "AUTO", categories: [lang], serial: "連載",
      status: "draft", settings: {}, voices: {}, voicePrefs: {},
      coverImage: null, ownerId: 7, rejectReason: "", totalChars: 10,
      created: "2026-08-17", defaultVoiceId: state.hasVoice ? (lang === "en" ? enVoice : "v-zh-1") : null,
      audioMode: state.mode, audioSettingsVersion: 1,
      workflow: workflow(), chapters: [chapter()],
    });
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 7, username: "生成操作員", role: "admin" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: voiceList }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books\?mine=1/, (route) => fulfill(route, []));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route(/\/api\/books\/b-gen-1$/, (route) => {
      const method = route.request().method();
      if (method === "PUT") {
        const body = JSON.parse(route.request().postData() || "{}");
        if (body.defaultVoiceId !== undefined) state.hasVoice = !!body.defaultVoiceId;
        if (body.audioMode) state.mode = body.audioMode;
        return fulfill(route, book());
      }
      // 第一次 poll 顯示 generating，之後 ready
      if (state.genStep === 1) {
        if (state.genPolled) state.genStep = 2;
        else state.genPolled = true;
      }
      return fulfill(route, book());
    });
    page.route(/\/api\/books\/b-gen-1\/chapters\/0\/audio-generations$/, (route) => {
      if (route.request().method() === "POST") {
        state.genStep = 1;
        state.genPolled = false;
        return fulfill(route, { generationId: 7, jobId: 9, status: "queued" });
      }
      return fulfill(route, { items: [] });
    });
    page.route(/\/api\/books\/b-gen-1\/chapters\/0$/, (route) => fulfill(route, { analysis: { segments: [] }, timing: null }));
    page.route(/\/api\/books\/b-gen-1\/audio\/0$/, (route) => route.fulfill({
      status: 200, contentType: "audio/mpeg",
      body: Buffer.from("ID3" + "\x00".repeat(256)),
    }));
    return state;
  }

  test("選聲線 → 不需分析 → 生成 → 可播放", async ({ page }) => {
    const state = installMocks(page, { hasVoice: false });
    await page.goto("/#/detail/b-gen-1");
    await expect(page.locator("#view-chapters")).toBeVisible();
    // 未選聲線 → 主要 CTA 是「選擇朗讀聲線」
    await expect(page.locator("[data-v4act='set-voice']").first()).toBeVisible();
    await page.locator("[data-v4act='set-voice']").first().click();
    await expect(page.locator("#audio-setup")).toBeVisible();
    await page.locator("#sel-default-voice").selectOption("v-zh-1");
    // 選好聲線 → 下一步是「生成音訊」（不需分析）
    await expect(page.locator("[data-v4act='generate']").first()).toBeVisible();
    // 不該出現分析入口
    await expect(page.locator("[data-v4act='analyze']")).toHaveCount(0);
    await page.locator("[data-v4act='generate']").first().click();
    // 等 poll：generating → ready
    await expect(page.locator(".badge", { hasText: "可播放" }).first()).toBeVisible({ timeout: 15000 });
    await expect(page.locator("[data-play]").first()).toBeVisible();
    expect(state.genStep).toBe(2);
    // 播放進入 player
    await page.locator("[data-play]").first().click();
    await expect(page.locator("#view-player")).toBeVisible();
    await expect(page.locator("#pl-title")).toContainText("序章");
  });

  test("390px 章節操作列不會超出視窗", async ({ page }) => {
    installMocks(page, { hasVoice: true, genStep: 2 });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/#/detail/b-gen-1");
    await expect(page.locator("#view-chapters")).toBeVisible();
    await expect(page.locator(".ch-actions").first()).toBeVisible();

    const metrics = await page.evaluate(() => ({
      viewport: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      actionRights: [...document.querySelectorAll(".ch-actions .btn")]
        .map((button) => button.getBoundingClientRect().right),
    }));
    expect(metrics.documentWidth).toBeLessThanOrEqual(metrics.viewport);
    expect(metrics.bodyWidth).toBeLessThanOrEqual(metrics.viewport);
    expect(metrics.actionRights.every((right) => right <= metrics.viewport)).toBe(true);
  });

  test("聲線試聽顯示 loading/playing 並防止重複 request", async ({ page }) => {
    installMocks(page, { hasVoice: false });
    let requests = 0;
    await page.addInitScript(() => {
      HTMLMediaElement.prototype.play = () => Promise.resolve();
    });
    await page.route("**/api/preview-tts", async (route) => {
      requests += 1;
      await new Promise((resolve) => setTimeout(resolve, 250));
      await route.fulfill({ status: 200, contentType: "audio/wav", body: wavSilence() });
    });
    await page.goto("/#/detail/b-gen-1");
    await page.locator("[data-v4act='set-voice']").first().click();
    await page.locator("#sel-default-voice").selectOption("v-zh-1");
    const preview = page.locator("#btn-preview-default-voice");
    await preview.click();
    await expect(preview).toBeDisabled();
    await expect(preview).toContainText("載入中…");
    await page.evaluate(() => document.querySelector("#btn-preview-default-voice").dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(requests).toBe(1);
    await expect(preview).toContainText("播放中…");
  });

  test("聲線試聽失敗顯示安全訊息並恢復按鈕", async ({ page }) => {
    installMocks(page, { hasVoice: false });
    await page.route("**/api/preview-tts", (route) => route.abort());
    await page.goto("/#/detail/b-gen-1");
    await page.locator("[data-v4act='set-voice']").first().click();
    await page.locator("#sel-default-voice").selectOption("v-zh-1");
    const preview = page.locator("#btn-preview-default-voice");
    await preview.click();
    await expect(page.locator(".toast-error").last()).toContainText("試聽失敗");
    await expect(preview).toBeEnabled();
  });

  test("en 書的 single 聲線下拉提供 en 聲線（不硬編碼 zh）", async ({ page }) => {
    const state = installMocks(page, { hasVoice: false, lang: "en", enVoice: "af-heart" });
    await page.goto("/#/detail/b-gen-1");
    await expect(page.locator("#view-chapters")).toBeVisible();
    await page.locator("[data-v4act='set-voice']").first().click();
    await expect(page.locator("#audio-setup")).toBeVisible();
    const opts = await page.locator("#sel-default-voice option").evaluateAll((els) =>
      els.map((o) => o.value).filter((v) => v));
    // en 書 → 應列出 en 聲線，而非只有「未指定」
    expect(opts).toContain("af-heart");
  });

  test("en 書 single 完整流程：選聲線 → 生成 → 可播放", async ({ page }) => {
    const state = installMocks(page, { hasVoice: false, lang: "en", enVoice: "af-heart" });
    await page.goto("/#/detail/b-gen-1");
    await expect(page.locator("#view-chapters")).toBeVisible();
    await page.locator("[data-v4act='set-voice']").first().click();
    await page.locator("#sel-default-voice").selectOption("af-heart");
    await expect(page.locator("[data-v4act='generate']").first()).toBeVisible();
    await page.locator("[data-v4act='generate']").first().click();
    await expect(page.locator(".badge", { hasText: "可播放" }).first()).toBeVisible({ timeout: 15000 });
    expect(state.genStep).toBe(2);
  });

  test("zh 書 + en-only 聲線：顯示語言不支援提示", async ({ page }) => {
    installMocks(page, {
      hasVoice: false, lang: "zh",
      voices: [{ id: "af-heart", name: "Heart", lang: "en" }, { id: "am-adam", name: "Adam", lang: "en" }],
    });
    await page.goto("/#/detail/b-gen-1");
    await expect(page.locator("#view-chapters")).toBeVisible();
    await page.locator("[data-v4act='set-voice']").first().click();
    await expect(page.locator("#audio-setup")).toBeVisible();
    const hint = page.locator("#voice-lang-hint");
    await expect(hint).toBeVisible();
    await expect(hint).toContainText("沒有支援此語言");
    // 下拉只有「未指定」
    const opts = await page.locator("#sel-default-voice option").evaluateAll((els) =>
      els.map((o) => o.value).filter((v) => v));
    expect(opts).toEqual([]);
  });
});
