import { test, expect } from "@playwright/test";

test.describe("作者取消章節分析", () => {
  test("取消後顯示分析失敗並可重新分析", async ({ page }) => {
    let cancelled = false;
    let cancelRequests = 0;
    const fulfill = (route, data) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(data),
    });
    const workflow = () => ({
      mode: "multi", hasDefaultVoice: false,
      aiProviderAvailable: true, ttsProviderAvailable: true,
      state: cancelled ? "analysis_failed" : "analysis_running",
      needsAction: cancelled, nextAction: cancelled ? "analyze" : null,
      providerUnavailable: false, total: 1, analyzed: 0, ready: 0,
      chapters: {
        0: {
          state: cancelled ? "analysis_failed" : "analysis_running",
          nextAction: cancelled ? "analyze" : null,
          providerUnavailable: false,
          error: cancelled ? "分析已取消" : "",
          analysisProgress: {
            totalChunks: 2, completedChunks: 0, runningChunks: 1,
            retryCount: 0, currentStage: "analyzing", progressPercent: 0,
            lastError: null,
          },
        },
      },
    });
    const book = () => ({
      id: "b-analysis-cancel", bid: "b-analysis-cancel", title: "取消分析測試書",
      synopsis: "", category: "zh", categories: ["zh"], status: "draft",
      settings: {}, voices: {}, voicePrefs: {}, coverImage: null, ownerId: 7,
      totalChars: 100, audioMode: "multi", defaultVoiceId: null,
      workflow: workflow(),
      chapters: [{ id: 1, seq: 0, title: "第一章", chars: 100, status: "pending", audio: "none" }],
    });

    page.route("**/api/auth/me", (route) => fulfill(route, {
      authed: true, user: { id: 7, username: "生成操作員", role: "admin" },
    }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books\?mine=1/, (route) => fulfill(route, []));
    page.route(/\/api\/books\/b-analysis-cancel$/, (route) => fulfill(route, book()));
    page.route(/\/api\/books\/b-analysis-cancel\/chapters\/0\/analysis\/cancel$/, (route) => {
      cancelRequests += 1;
      cancelled = true;
      return fulfill(route, {
        analysisId: 9, jobId: 10, status: "cancelled", analysisStatus: "failed",
      });
    });

    await page.goto("/#/detail/b-analysis-cancel");
    await expect(page.locator("[data-v4act='cancel-analysis']").first()).toBeVisible();
    page.on("dialog", (dialog) => dialog.accept());
    await page.locator("[data-v4act='cancel-analysis']").first().click();
    await expect.poll(() => cancelRequests).toBe(1);
    await expect(page.locator(".badge", { hasText: "分析失敗" }).first()).toBeVisible();
    await expect(page.locator("[data-v4act='analyze']").first()).toContainText("重新分析");
  });

  test("批次分析可整批取消並回到可重新分析", async ({ page }) => {
    let batchState = "idle";
    let batchCreateRequests = 0;
    let batchCancelRequests = 0;
    const fulfill = (route, data) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(data),
    });
    const chapterWorkflow = (state, nextAction, error = "") => ({
      state, nextAction, providerUnavailable: false, error,
      analysisProgress: {
        totalChunks: 2, completedChunks: state === "analysis_failed" ? 0 : 0,
        runningChunks: state === "analysis_running" ? 1 : 0, retryCount: 0,
        currentStage: state === "analysis_running" ? "analyzing" : state === "analysis_failed" ? "failed" : "queued",
        progressPercent: 0, lastError: null,
      },
    });
    const workflow = () => ({
      mode: "multi", hasDefaultVoice: false,
      aiProviderAvailable: true, ttsProviderAvailable: true,
      state: batchState === "running" ? "analysis_running" : "needs_analysis",
      needsAction: true, nextAction: "analyze", providerUnavailable: false,
      total: 2, analyzed: 0, ready: 0,
      chapters: {
        0: batchState === "running"
          ? chapterWorkflow("analysis_running", null)
          : chapterWorkflow("analysis_failed", "analyze", "批次分析已取消"),
        1: chapterWorkflow("needs_analysis", "analyze"),
      },
    });
    const book = () => ({
      id: "b-analysis-batch-cancel", bid: "b-analysis-batch-cancel", title: "批次取消測試書",
      synopsis: "", category: "zh", categories: ["zh"], status: "draft",
      settings: {}, voices: {}, voicePrefs: {}, coverImage: null, ownerId: 7,
      totalChars: 200, audioMode: "multi", defaultVoiceId: null,
      workflow: workflow(),
      chapters: [
        { id: 1, seq: 0, title: "第一章", chars: 100, status: "pending", audio: "none" },
        { id: 2, seq: 1, title: "第二章", chars: 100, status: "pending", audio: "none" },
      ],
    });

    page.route("**/api/auth/me", (route) => fulfill(route, {
      authed: true, user: { id: 7, username: "生成操作員", role: "admin" },
    }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books\?mine=1/, (route) => fulfill(route, []));
    page.route(/\/api\/books\/b-analysis-batch-cancel$/, (route) => fulfill(route, book()));
    page.route(/\/api\/books\/b-analysis-batch-cancel\/analysis\/batch$/, (route) => {
      if (route.request().method() === "POST") {
        batchCreateRequests += 1;
        batchState = "running";
        return fulfill(route, { jobId: 11, status: "queued" });
      }
      return route.continue();
    });
    page.route(/\/api\/books\/b-analysis-batch-cancel\/analysis\/batch\/cancel$/, (route) => {
      batchCancelRequests += 1;
      batchState = "cancelled";
      return fulfill(route, {
        jobId: 11, status: "cancelled", cancelledAnalysisCount: 1,
      });
    });

    await page.goto("/#/detail/b-analysis-batch-cancel");
    await expect(page.locator("#btn-analyze-all")).toBeVisible();
    page.on("dialog", (dialog) => dialog.accept());
    await page.locator("#btn-analyze-all").click();
    await expect(page.locator("#btn-cancel-analyze-all")).toBeVisible();
    await page.locator("#btn-cancel-analyze-all").click();
    await expect.poll(() => batchCancelRequests).toBe(1);
    await expect.poll(() => batchCreateRequests).toBe(1);
    await expect(page.locator("#btn-cancel-analyze-all")).toBeHidden();
    await expect(page.locator("#btn-analyze-all")).toContainText("分析剩餘");
  });
});
