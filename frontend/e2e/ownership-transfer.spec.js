import { test, expect } from "@playwright/test";

async function openWorkspaceOnMobile(page) {
  const toggle = page.getByRole("button", { name: "開啟導覽選單" });
  if (await toggle.isVisible()) await toggle.click();
  const group = page.locator("#nav-workspace-group");
  if (await group.isVisible() && (await group.getAttribute("open")) === null) await group.locator("summary").click();
}

const fulfill = (route, data, status = 200) => route.fulfill({
  status, contentType: "application/json", body: JSON.stringify(data),
});

function installCommon(page, user, books = []) {
  page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user }));
  page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
  page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
  page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, books));
}

const envelope = (items = [], extra = {}) => ({
  items, total: items.length, page: 1, pageSize: 20, page_size: 20,
  totalPages: items.length ? 1 : 0, total_pages: items.length ? 1 : 0, ...extra,
});

test.describe("ownership-transfer browser surfaces", () => {
  test("Author opens exact target selector and submits an invitation", async ({ page }) => {
    const book = { id: "b-transfer-ui", bid: "b-transfer-ui", title: "可移交作品", synopsis: "摘要",
      category: "zh", categories: ["zh"], serial: "連載", status: "draft", chapters: [],
      settings: {}, voices: {}, coverImage: null, ownerId: 7, rejectReason: "", totalChars: 0,
      created: "2026-09-04", defaultVoiceId: null, audioMode: "single", audioSettingsVersion: 1 };
    let transfers = [];
    installCommon(page, { id: 7, username: "作者一", role: "author" }, [book]);
    page.route(/\/api\/requests(\?.*)?$/, (route) => fulfill(route, { items: [], total: 0, page: 1, pageSize: 100, totalPages: 0 }));
    page.route("**/api/ownership-transfers/targets", (route) => fulfill(route, { items: [{ id: 8, username: "目標帳號", role: "reader" }] }));
    page.route("**/api/ownership-transfers?*", (route) => fulfill(route, envelope(transfers)));
    page.route("**/api/books/b-transfer-ui/ownership-transfers", async (route) => {
      transfers = [{ id: 91, bookId: "b-transfer-ui", bookTitle: book.title, status: "REQUESTED", currentOwner: "作者一", target: "目標帳號", requesterAccountId: 7, targetAccountId: 8, expiresAt: "2026-09-11T00:00:00" }];
      await fulfill(route, { id: 91, bookId: "b-transfer-ui", status: "REQUESTED", events: [] });
    });
    page.on("dialog", (dialog) => dialog.accept());
    await page.goto("/#/bookshelf");
    await openWorkspaceOnMobile(page);
    await expect(page.locator("#nav-mine")).toBeVisible();
    await page.locator("#nav-mine").click();
    await page.locator("[data-transfer-book='b-transfer-ui']").click();
    await expect(page.locator("#ownership-transfer-modal")).toBeVisible();
    await expect(page.locator("#ot-target option[value='8']")).toContainText("目標帳號");
    await page.locator("#ot-target").selectOption("8");
    await page.locator("#ot-submit").click();
    await expect(page.locator("#ownership-transfer-modal")).toBeHidden();
    await openWorkspaceOnMobile(page);
    await page.locator("#nav-requests").click();
    await expect(page.locator("#request-list")).toContainText("所有權轉移");
    await expect(page.locator("#request-list")).toContainText("等待目標回應");
  });

  test("Target sees only their invitation actions and can accept", async ({ page }) => {
    const item = { id: 92, bookId: "b-target-ui", bookTitle: "目標作品", status: "REQUESTED", currentOwner: "作者一", target: "讀者一", requesterAccountId: 7, targetAccountId: 9, expiresAt: "2026-09-11T00:00:00" };
    let accepted = false;
    installCommon(page, { id: 9, username: "讀者一", role: "reader" });
    page.route(/\/api\/requests(\?.*)?$/, (route) => fulfill(route, { items: [], total: 0, page: 1, totalPages: 0 }));
    page.route("**/api/ownership-transfers?*", (route) => fulfill(route, envelope(accepted ? [{ ...item, status: "TARGET_ACCEPTED" }] : [item])));
    page.route("**/api/ownership-transfers/92/accept", (route) => { accepted = true; return fulfill(route, { ...item, status: "TARGET_ACCEPTED", events: [] }); });
    await page.goto("/#/requests");
    await openWorkspaceOnMobile(page);
    await expect(page.locator("#nav-requests")).toBeVisible();
    if (await page.locator("#nav-toggle").isVisible()) await page.locator("#nav-toggle").click();
    await expect(page.locator("[data-transfer-accept='92']")).toBeVisible();
    await expect(page.locator("[data-transfer-cancel='92']")).toHaveCount(0);
    await page.locator("[data-transfer-accept='92']").click();
    await expect(page.locator("#request-list")).toContainText("等待審核");
  });

  test("Reviewer uses the shared ownership queue and mobile detail remains usable", async ({ page }) => {
    let status = "TARGET_ACCEPTED";
    const item = () => ({ id: 93, bookId: 93, bookBid: "b-review-transfer", bookTitle: "待審移交", status, currentOwner: "作者一", target: "目標帳號", requester: "作者一", reviewer: status === "IN_REVIEW" ? "Reviewer一" : null, requesterAccountId: 7, targetAccountId: 8, reviewerAccountId: status === "IN_REVIEW" ? 10 : null, createdAt: "2026-09-04T00:00:00", reason: "交接" });
    installCommon(page, { id: 10, username: "Reviewer一", role: "reviewer" });
    page.route("**/api/review/ownership-transfers?*", (route) => fulfill(route, envelope([item()])));
    page.route("**/api/review/ownership-transfers/93", (route) => fulfill(route, { ...item(), sourceRevision: "rev-1", events: [{ event_type: "target_accepted", created_at: "2026-09-04T00:00:00", actor_username: "目標帳號" }] }));
    page.route("**/api/review/ownership-transfers/93/start", (route) => { status = "IN_REVIEW"; return fulfill(route, item()); });
    page.route("**/api/review/ownership-transfers/93/approve", (route) => { status = "COMPLETED"; return fulfill(route, item()); });
    await page.goto("/#/bookshelf");
    await openWorkspaceOnMobile(page);
    await expect(page.locator("#nav-admin")).toBeVisible();
    await page.locator("#nav-admin").click();
    await page.locator('[data-atab="ownership"]').click();
    await expect(page.locator("[data-atab='ownership']")).toBeVisible();
    await expect(page.locator("[data-ownership-open='93']")).toBeVisible();
    await page.locator("[data-ownership-open='93']").click();
    await expect(page.locator("#ownership-start")).toBeVisible();
    await page.locator("#ownership-start").click();
    await expect(page.locator("#ownership-approve")).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(overflow).toBe(false);
  });
});
