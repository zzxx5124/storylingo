import { test, expect } from "@playwright/test";

test.describe("章節 filter（V4 Blackbox remediation）", () => {
  function installMocks(page) {
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    const chapters = [
      { id: 1, seq: 0, title: "分析失敗章", chars: 10, status: "pending", audio: "none", error: "", textHash: "h1" },
      { id: 2, seq: 1, title: "待分析章", chars: 10, status: "pending", audio: "none", error: "", textHash: "h2" },
      { id: 3, seq: 2, title: "可生成章", chars: 10, status: "pending", audio: "none", error: "", textHash: "h3" },
      { id: 4, seq: 3, title: "可播放章", chars: 10, status: "analyzed", audio: "ready", error: "", textHash: "h4" },
    ];
    const workflow = {
      mode: "multi", hasDefaultVoice: false, aiProviderAvailable: true, ttsProviderAvailable: true,
      state: "needs_analysis", needsAction: true, nextAction: "analyze", providerUnavailable: false,
      total: 4, analyzed: 1, ready: 1,
      chapters: {
        0: { state: "analysis_failed", nextAction: "analyze", providerUnavailable: false, error: "AI 回應格式錯誤" },
        1: { state: "needs_analysis", nextAction: "analyze", providerUnavailable: false, error: "" },
        2: { state: "ready_to_generate", nextAction: "generate", providerUnavailable: false, error: "" },
        3: { state: "audio_ready", nextAction: null, providerUnavailable: false, error: "" },
      },
    };
    const book = {
      id: "b-filter", bid: "b-filter", title: "filter 測試書", synopsis: "",
      category: "zh", vocabLevel: "AUTO", categories: ["zh"], serial: "連載",
      status: "draft", settings: {}, voices: {}, voicePrefs: {},
      coverImage: null, ownerId: 7, rejectReason: "", totalChars: 40,
      created: "2026-08-17", defaultVoiceId: null, audioMode: "multi", audioSettingsVersion: 1,
      workflow, chapters,
    };
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 7, username: "審核員", role: "admin" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [{ id: "v-zh-1", name: "曉曉", lang: "zh" }] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route(/\/api\/books\/b-filter$/, (route) => fulfill(route, book));
  }

  test("失敗 filter 只顯示 audio_failed；未分析 filter 只顯示 needs_analysis；空結果顯示 empty state", async ({ page }) => {
    await installMocks(page);
    await page.goto("/#/detail/b-filter");
    await expect(page.locator("#view-chapters")).toBeVisible();
    await expect(page.locator(".chapter-item")).toHaveCount(4);

    // 失敗 filter → 只顯示「分析失敗章」
    await page.locator('#ch-filter [data-filter="error"]').click();
    await expect(page.locator(".chapter-item")).toHaveCount(1);
    await expect(page.locator(".chapter-item")).toContainText("分析失敗章");
    await expect(page.locator(".chapter-item")).toContainText("重新分析");

    // 未分析 filter → 只顯示「待分析章」
    await page.locator('#ch-filter [data-filter="pending"]').click();
    await expect(page.locator(".chapter-item")).toHaveCount(1);
    await expect(page.locator(".chapter-item")).toContainText("待分析章");

    // 已分析 filter → 可生成章（ready_to_generate）＋可播放章（audio_ready）
    await page.locator('#ch-filter [data-filter="analyzed"]').click();
    await expect(page.locator(".chapter-item")).toHaveCount(2);
    await expect(page.locator(".chapter-item").filter({ hasText: "可生成章" })).toHaveCount(1);
    await expect(page.locator(".chapter-item").filter({ hasText: "可播放章" })).toHaveCount(1);

    // 可播放 filter → 可播放章
    await page.locator('#ch-filter [data-filter="ready"]').click();
    await expect(page.locator(".chapter-item")).toHaveCount(1);
    await expect(page.locator(".chapter-item")).toContainText("可播放章");

    // 全部 → 回到 4 章
    await page.locator('#ch-filter [data-filter="all"]').click();
    await expect(page.locator(".chapter-item")).toHaveCount(4);
  });

  test("空結果時顯示 empty state 而非全部章節", async ({ page }) => {
    await installMocks(page);
    await page.goto("/#/detail/b-filter");
    await expect(page.locator(".chapter-item")).toHaveCount(4);
    // 全部章節都不是 needs_analysis？——待分析章存在；改用「無失敗章」情境：
    // 把 mock 改為全部成功後，失敗 filter 應顯示 empty state
    await page.route(/\/api\/books\/b-filter$/, (route) => route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        id: "b-filter", bid: "b-filter", title: "filter 測試書", synopsis: "",
        category: "zh", vocabLevel: "AUTO", categories: ["zh"], serial: "連載",
        status: "draft", settings: {}, voices: {}, voicePrefs: {},
        coverImage: null, ownerId: 7, rejectReason: "", totalChars: 40,
        created: "2026-08-17", defaultVoiceId: null, audioMode: "multi", audioSettingsVersion: 1,
        workflow: {
          mode: "multi", hasDefaultVoice: false, aiProviderAvailable: true, ttsProviderAvailable: true,
          state: "audio_ready", needsAction: false, nextAction: null, providerUnavailable: false,
          total: 1, analyzed: 1, ready: 1,
          chapters: { 0: { state: "audio_ready", nextAction: null, providerUnavailable: false, error: "" } },
        },
        chapters: [{ id: 1, seq: 0, title: "完成章", chars: 10, status: "analyzed", audio: "ready", error: "", textHash: "h1" }],
      }),
    }));
    await page.reload();
    await expect(page.locator(".chapter-item")).toHaveCount(1);
    await page.locator('#ch-filter [data-filter="error"]').click();
    // 沒有失敗章 → empty state，且不顯示任何章節列
    await expect(page.locator(".chapter-item")).toHaveCount(0);
    await expect(page.locator("#chapter-list")).toContainText("還沒有章節");
  });

  test("分析進度顯示 backend chunk state，不使用假時間進度", async ({ page }) => {
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    const book = {
      id: "b-progress", bid: "b-progress", title: "進度測試書", synopsis: "",
      category: "zh", vocabLevel: "AUTO", categories: ["zh"], serial: "連載",
      status: "draft", settings: {}, voices: {}, voicePrefs: {}, coverImage: null,
      ownerId: 7, rejectReason: "", totalChars: 100, defaultVoiceId: null,
      audioMode: "multi", audioSettingsVersion: 1,
      workflow: {
        mode: "multi", hasDefaultVoice: false, aiProviderAvailable: true,
        ttsProviderAvailable: true, state: "analysis_running", needsAction: false,
        nextAction: null, providerUnavailable: false, total: 1, analyzed: 0, ready: 0,
        chapters: { 0: {
          state: "analysis_running", nextAction: null, providerUnavailable: false, error: "",
          analysisProgress: {
            totalChunks: 2, completedChunks: 1, runningChunks: 1, retryCount: 0,
            currentStage: "analyzing", progressPercent: 50, lastError: null,
          },
        } },
      },
      chapters: [{ id: 1, seq: 0, title: "分析進度章", chars: 100, status: "pending", audio: "none", error: "", textHash: "h1" }],
    };
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 7, username: "審核員", role: "admin" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [{ id: "v-zh-1", name: "曉曉", lang: "zh" }] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route(/\/api\/books\/b-progress$/, (route) => fulfill(route, book));

    await page.goto("/#/detail/b-progress");
    await expect(page.locator("#view-chapters")).toBeVisible();
    await expect(page.locator(".chapter-item")).toContainText("分析中 50% · 已完成 1/2 段");
    await expect(page.locator(".chapter-item")).toContainText("處理中 1 段");
  });
});
