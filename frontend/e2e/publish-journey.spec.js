import { test, expect } from "@playwright/test";

test.describe("發布旅程（Phase 15c release gate）", () => {
  test("作者送審 → 狀態變待審核", async ({ page }) => {
    let status = "draft";
    let requestStatus = null;
    const mineBook = () => [{
      id: "b-pub", bid: "b-pub", title: "待審小說", synopsis: "",
      category: "vocab", vocabLevel: "AUTO", categories: ["vocab"], serial: "連載",
      status, chapters: [{ id: 1, seq: 0, title: "序章", chars: 10, status: "pending", audio: "ready", error: "", textHash: "h1" }],
      settings: {}, voices: {}, coverImage: null, ownerId: 7, rejectReason: "",
      totalChars: 10, created: "2026-08-17", defaultVoiceId: null, audioMode: "single", audioSettingsVersion: 1,
    }];
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 7, username: "作者一", role: "author" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books(\?.*)?$/, (route) => {
      const url = new URL(route.request().url());
      return fulfill(route, url.searchParams.get("mine") === "1" ? mineBook() : []);
    });
    page.route(/\/api\/requests(\?.*)?$/, (route) => fulfill(route, {
      items: requestStatus ? [{ id: 31, bookId: "b-pub", bookBid: "b-pub", bookTitle: "待審小說", requestType: "publish", status: requestStatus, submittedAt: "2026-08-17T00:00:00Z", reviewer: null }] : [],
      total: requestStatus ? 1 : 0, page: 1, pageSize: 100, totalPages: 1,
    }));
    page.route(/\/api\/books\/b-pub\/requests$/, (route) => {
      if (route.request().method() === "POST") {
        requestStatus = "SUBMITTED";
        return fulfill(route, { id: 31, bookId: "b-pub", requestType: "publish", status: requestStatus, submittedRevision: "rev-1", events: [{ event_type: "submitted" }] });
      }
      return fulfill(route, { ok: true });
    });
    page.on("dialog", (dialog) => dialog.accept());
    await page.goto("/#/mine");
    await expect(page.locator("[data-submit]")).toBeVisible();
    await page.locator("[data-submit]").click();
    await expect(page.locator(".mine-card .status-chip")).toContainText("待審核");
  });

  test("admin 審核通過 → 書籍可下架（狀態 approved）", async ({ page }) => {
    let status = "SUBMITTED";
    const request = () => ({
      id: 31, bookId: "b-pub", bookBid: "b-pub", bookTitle: "待審小說", requestType: "publish", status,
      requester: "作者一", requesterAccountId: 7, reviewer: status === "IN_REVIEW" ? "管理員" : null,
      reviewerAccountId: status === "IN_REVIEW" ? 1 : null, submittedAt: "2026-08-17T00:00:00Z", submittedRevision: "rev-1",
      book: { currentRevision: "rev-1", chapters: [{ seq: 0, title: "序章", text: "內容" }] },
      events: [{ event_type: "submitted", created_at: "2026-08-17T00:00:00Z" }],
    });
    const dashboard = () => ({
      books: { total: 1, by_status: { submitted: status === "SUBMITTED" ? 1 : 0 } },
      users: { total: 2, by_role: { author: 1 } },
      chapters: { audioReady: 1 },
    });
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 1, username: "管理員", role: "admin" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route("**/api/admin/dashboard", (route) => fulfill(route, dashboard()));
    page.route(/\/api\/review\/requests(\?.*)?$/, (route) => fulfill(route, { items: [request()], total: 1, page: 1, pageSize: 20, totalPages: 1 }));
    page.route(/\/api\/review\/requests\/31$/, (route) => fulfill(route, request()));
    page.route(/\/api\/review\/requests\/31\/start$/, (route) => {
      status = "IN_REVIEW";
      return fulfill(route, request());
    });
    page.route(/\/api\/review\/requests\/31\/approve$/, (route) => {
      status = "APPROVED";
      return fulfill(route, request());
    });
    await page.goto("/#/admin");
    await expect(page.locator("[data-review-open='31']")).toBeVisible();
    await page.locator("[data-review-open='31']").click();
    await expect(page.locator("#review-start")).toBeVisible();
    await page.locator("#review-start").click();
    await expect(page.locator("#review-approve")).toBeVisible();
    await page.locator("#review-approve").click();
    await expect(page.locator(".review-detail")).toContainText("已核准");
  });

  test("讀者可閱讀已公開章節並播放音訊", async ({ page }) => {
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 9, username: "讀者一", role: "reader" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route(/\/api\/books\/b-pub\/read\/0$/, (route) => fulfill(route, {
      book: { id: "b-pub", title: "公開小說" },
      chapter: { seq: 0, title: "序章", text: "這是公開章節的正文內容。", chars: 10, audio: "ready" },
      navigation: { previous: null, next: null, total: 1 },
    }));
    page.route(/\/api\/books\/b-pub\/chapters\/0$/, (route) => fulfill(route, { analysis: { segments: [] }, timing: null }));
    page.route(/\/api\/books\/b-pub\/audio\/0$/, (route) => route.fulfill({
      status: 200, contentType: "audio/mpeg",
      body: Buffer.from("ID3" + "\x00".repeat(256)),
    }));
    page.route(/\/api\/books\/b-pub\/view$/, (route) => fulfill(route, { ok: true }));
    page.route("**/api/me/progress", (route) => fulfill(route, { items: [] }));
    await page.goto("/#/read/b-pub/0");
    await expect(page.locator("#reader-body")).toContainText("這是公開章節的正文內容");
    await expect(page.locator("#reader-body audio, .reader-footer audio")).toBeVisible();
  });
});
