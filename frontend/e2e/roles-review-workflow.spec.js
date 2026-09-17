import { test, expect } from "@playwright/test";

async function openWorkspaceOnMobile(page) {
  const toggle = page.getByRole("button", { name: "開啟導覽選單" });
  if (await toggle.isVisible()) await toggle.click();
  const group = page.locator("#nav-workspace-group");
  if (await group.isVisible() && (await group.getAttribute("open")) === null) await group.locator("summary").click();
}

const fulfill = (route, data, status = 200) => route.fulfill({
  status,
  contentType: "application/json",
  body: JSON.stringify(data),
});

function mockShared(page, user, books = []) {
  page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user }));
  page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
  page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
  page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, books));
}

test.describe("roles-review-workflow minimum browser surfaces", () => {
  test("Author submits a typed publish request and sees pending status", async ({ page }) => {
    let requests = [];
    const book = {
      id: "b-author-flow", bid: "b-author-flow", title: "作者作品", synopsis: "摘要",
      category: "zh", categories: ["zh"], serial: "連載", status: "draft", chapters: [],
      settings: {}, voices: {}, coverImage: null, ownerId: 7, rejectReason: "", totalChars: 0,
      created: "2026-08-17", defaultVoiceId: null, audioMode: "single", audioSettingsVersion: 1,
    };
    mockShared(page, { id: 7, username: "作者一", role: "author" }, [book]);
    page.route(/\/api\/requests(\?.*)?$/, (route) => fulfill(route, {
      items: requests, total: requests.length, page: 1, pageSize: 100, totalPages: 1,
    }));
    page.route(/\/api\/books\/b-author-flow\/requests$/, (route) => {
      requests = [{ id: 41, bookId: book.id, bookBid: book.bid, bookTitle: book.title,
        requestType: "publish", status: "SUBMITTED", submittedAt: "2026-08-17T00:00:00Z", reviewer: null }];
      return fulfill(route, { id: 41, bookId: book.id, requestType: "publish", status: "SUBMITTED", submittedRevision: "rev-1", events: [{ event_type: "submitted" }] });
    });
    page.on("dialog", (dialog) => dialog.accept());

    await page.goto("/#/mine");
    await expect(page.locator("[data-submit='b-author-flow']")).toBeVisible();
    await page.locator("[data-submit='b-author-flow']").click();
    await expect(page.locator(".mine-card .status-chip")).toContainText("待審核");
    await expect(page.locator("[data-submit='b-author-flow']")).toHaveCount(0);
    await openWorkspaceOnMobile(page);
    await expect(page.locator("#nav-requests")).toBeVisible();
    await page.locator("#nav-requests").click();
    await expect(page.locator("#request-list")).toContainText("發布");
    await expect(page.locator("#request-list")).toContainText("待審核");
  });

  test("Author does not receive direct analysis or generation controls", async ({ page }) => {
    const book = {
      id: "b-author-locked", bid: "b-author-locked", title: "待處理作品", synopsis: "",
      category: "vocab", categories: ["vocab"], serial: "連載", status: "draft",
      chapters: [{ id: 1, seq: 0, title: "第一章", chars: 20, status: "pending", audio: "none", error: "", textHash: "h1" }],
      workflow: { mode: "multi", analyzed: 0, ready: 0, total: 1, chapters: { 0: { state: "needs_analysis", nextAction: "analyze", error: "" } } },
      settings: {}, voices: {}, coverImage: null, ownerId: 7, rejectReason: "", totalChars: 20,
      created: "2026-08-17", defaultVoiceId: null, audioMode: "multi", audioSettingsVersion: 1,
    };
    mockShared(page, { id: 7, username: "作者一", role: "author" });
    page.route("**/api/books/b-author-locked", (route) => fulfill(route, book));
    await page.goto("/#/detail/b-author-locked");
    await expect(page.locator("#view-chapters")).toBeVisible();
    await expect(page.locator("#btn-analyze-all")).toBeHidden();
    await expect(page.locator("#btn-tts-all")).toBeHidden();
    await expect(page.locator("#audio-setup")).toBeHidden();
    await expect(page.locator("#btn-book-voices")).toBeHidden();
    await page.locator('[data-toggle="0"]').click();
    await expect(page.locator('[data-detail="0"]')).toContainText("Reviewer");
  });

  test("Reviewer opens the queue, claims a request, and rejects with a reason", async ({ page }) => {
    let status = "SUBMITTED";
    const detail = () => ({
      id: 51, bookId: "b-review-flow", bookBid: "b-review-flow", bookTitle: "待審作品",
      requestType: "publish", status, requester: "作者一", requesterAccountId: 7,
      reviewer: status === "IN_REVIEW" ? "Reviewer一" : null,
      reviewerAccountId: status === "IN_REVIEW" ? 8 : null,
      submittedAt: "2026-08-17T00:00:00Z", submittedRevision: "rev-1",
      book: { currentRevision: "rev-1", chapters: [{ seq: 0, title: "第一章", text: "作者正文" }] },
      events: [{ event_type: "submitted", created_at: "2026-08-17T00:00:00Z" }],
      decisionReason: status === "REJECTED" ? "請補充內容" : null,
    });
    mockShared(page, { id: 8, username: "Reviewer一", role: "reviewer" });
    page.route("**/api/review/requests?*", (route) => fulfill(route, {
      items: [{ id: 51, bookId: "b-review-flow", bookBid: "b-review-flow", bookTitle: "待審作品",
        requestType: "publish", status, requester: "作者一", submittedAt: "2026-08-17T00:00:00Z", reviewer: status === "IN_REVIEW" ? "Reviewer一" : null }],
      total: 1, page: 1, pageSize: 20, totalPages: 1,
    }));
    page.route("**/api/review/requests/51", (route) => fulfill(route, detail()));
    page.route("**/api/review/requests/51/start", (route) => { status = "IN_REVIEW"; return fulfill(route, detail()); });
    page.route("**/api/review/requests/51/reject", (route) => { status = "REJECTED"; return fulfill(route, detail()); });
    await page.goto("/#/admin");
    await expect(page.locator("#review-type")).toBeVisible();
    await expect(page.locator("[data-review-open='51']")).toBeVisible();
    await page.locator("[data-review-open='51']").click();
    await expect(page.locator("#review-start")).toBeVisible();
    await page.locator("#review-start").click();
    await expect(page.locator("#review-reject")).toBeVisible();
    await page.locator("#review-reason").fill("請補充內容");
    await page.locator("#review-reject").click();
    await expect(page.locator(".review-detail")).toContainText("已退回");
    await expect(page.locator(".review-detail")).toContainText("請補充內容");
    await expect(page.locator(".review-book-content pre")).toContainText("作者正文");
  });

  test("Reader receives only the target-request surface, not privileged workflow navigation", async ({ page }) => {
    mockShared(page, { id: 9, username: "讀者一", role: "reader" });
    await page.goto("/#/admin");
    await openWorkspaceOnMobile(page);
    await expect(page.locator("#nav-admin")).toBeHidden();
    await expect(page.locator("#nav-requests")).toBeVisible();
    await expect(page.locator("#view-admin")).toBeHidden();
  });

  test("Super Admin can see the protected workflow surface", async ({ page }) => {
    mockShared(page, { id: 10, username: "Super一", role: "super_admin" });
    page.route("**/api/admin/dashboard", (route) => fulfill(route, {
      books: { total: 0, by_status: {} }, users: { total: 1, by_role: { super_admin: 1 } }, chapters: { audioReady: 0 },
    }));
    page.route("**/api/review/requests?*", (route) => fulfill(route, { items: [], total: 0, page: 1, pageSize: 20, totalPages: 1 }));
    await page.goto("/#/admin");
    await openWorkspaceOnMobile(page);
    await expect(page.locator("#nav-admin")).toBeVisible();
    await expect(page.locator("#nav-requests")).toBeVisible();
    await expect(page.locator("#review-type")).toBeVisible();
    await expect(page.locator("#admin-panel")).toContainText("目前沒有符合條件的申請");
  });
});
