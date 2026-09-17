import { test, expect } from "@playwright/test";

const fulfill = (route, data, status = 200) => route.fulfill({
  status, contentType: "application/json", body: JSON.stringify(data),
});

function installCommon(page, user) {
  page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user }));
  page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
  page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
  page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
  page.route("**/api/admin/dashboard", (route) => fulfill(route, {
    books: { total: 0, by_status: {} }, users: { total: 1, by_role: {} }, chapters: { audioReady: 0 },
  }));
}

const operation = {
  id: 101, requestId: 55, bookId: "book-101", operationType: "audiobook",
  authorizationState: "AUTHORIZED_FOR_GENERATION", status: "running",
  sourceRevision: "revision-101", jobs: [{
    id: 202, serviceType: "TTS", providerLabel: "Local TTS", status: "running",
    progress: 40, attempts: 1, maxAttempts: 3, failureCategory: "", error: "",
    attemptsHistory: [{ attempt_number: 1, outcome: "running", failure_category: "" }],
  }],
};

function installGenerationRoutes(page, prefix) {
  page.route(`${prefix}/generation/summary`, (route) => fulfill(route, {
    counts: [
      { service_type: "AI", status: "running", n: 1 },
      { service_type: "TTS", status: "running", n: 1 },
    ], workers: [{ instance_id: "worker-1" }], providers: [{ id: 1, name: "Local TTS", maxConcurrency: 1 }],
  }));
  const rolePath = prefix.includes("admin") ? "admin" : "review";
  page.route(new RegExp(`/api/${rolePath}/generation/operations\\?.*`), (route) => fulfill(route, {
    items: [operation], total: 1, page: 1, page_size: 20, total_pages: 1,
  }));
  page.route(`${prefix}/generation/operations/101`, (route) => fulfill(route, operation));
  page.route(`${prefix}/generation/jobs/202/cancel`, (route) => fulfill(route, operation.jobs[0]));
  page.route(`${prefix}/generation/jobs/202/retry`, (route) => fulfill(route, operation.jobs[0]));
}

test.describe("generation-orchestration operational surfaces", () => {
  test("Admin sees separate AI/TTS operational status without secrets", async ({ page }) => {
    installCommon(page, { id: 1, username: "admin", role: "admin" });
    installGenerationRoutes(page, "**/api/admin");
    await page.goto("/#/admin");
    await page.locator('[data-atab="generation"]').click();
    await expect(page.locator(".generation-summary")).toContainText("AI 排隊");
    await expect(page.locator(".generation-op-row")).toContainText("Local TTS");
    await expect(page.locator(".generation-op-row")).toContainText("running");
    await page.locator("[data-generation-open='101']").click();
    await expect(page.locator(".review-detail")).toContainText("AUTHORIZED_FOR_GENERATION");
    await expect(page.locator(".review-detail")).not.toContainText("secret");
  });

  test("Reviewer can inspect approved operation and remains within mobile layout", async ({ page }) => {
    installCommon(page, { id: 2, username: "reviewer", role: "reviewer" });
    installGenerationRoutes(page, "**/api/review");
    await page.goto("/#/admin");
    await page.locator('[data-atab="generation"]').click();
    await expect(page.locator(".generation-op-row")).toBeVisible();
    await page.locator("[data-generation-open='101']").click();
    await expect(page.locator(".review-detail")).toContainText("TTS");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(overflow).toBe(false);
  });
});
