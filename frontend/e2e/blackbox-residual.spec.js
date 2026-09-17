import { test, expect } from "@playwright/test";

const bookPayload = () => ({
  id: "b-res-1",
  bid: "b-res-1",
  title: "黑箱殘留測試書",
  synopsis: "黑箱殘留 regression 用書",
  category: "vocab",
  vocabLevel: "AUTO",
  categories: ["vocab"],
  serial: "連載",
  status: "approved",
  chapters: [
    { id: 1, seq: 0, title: "序章", chars: 80, status: "analyzed", audio: "ready", error: "", textHash: "h0" },
    { id: 2, seq: 1, title: "第一章", chars: 120, status: "analyzed", audio: "ready", error: "", textHash: "h1" },
  ],
  voices: { 旁白: "v-zh-1", 主角: "v-zh-2", _english: "en-default" },
  voicePrefs: {},
  settings: {},
  coverImage: null,
  ownerId: 7,
  rejectReason: "",
  totalChars: 200,
  follow: false,
  workflow: {
    mode: "multi",
    analyzed: 2,
    ready: 2,
    total: 2,
    chapters: {
      0: { state: "audio_ready", nextAction: null, providerUnavailable: false, error: "" },
      1: { state: "audio_ready", nextAction: null, providerUnavailable: false, error: "" },
    },
  },
});

function mockSession(page, role) {
  const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
  page.route("**/api/auth/me", (route) => fulfill(route, {
    authed: true, user: { id: 7, username: role === "reader" ? "讀者R" : "作者A", role },
  }));
  page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
  page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
  page.route(/\/api\/books($|\?)/, (route) => fulfill(route, []));
  page.route(/\/api\/books\/b-res-1$/, (route) => {
    if (route.request().method() === "GET") return fulfill(route, bookPayload());
    return route.continue();
  });
  page.route(/\/api\/books\/b-res-1\/recommendations$/, (route) => fulfill(route, { items: [] }));
  page.route(/\/api\/books\/b-res-1\/comments$/, (route) => fulfill(route, { items: [] }));
  page.route(/\/api\/me\/library\?kind=favorite$/, (route) => fulfill(route, { items: [] }));
}

test.describe("黑箱殘留 remediation（附錄 B）", () => {
  test("「已分析」filter 顯示已分析章節；0 剩餘批次按鈕停用", async ({ page }) => {
    await mockSession(page, "author");
    await page.goto("/#/detail/b-res-1");
    await expect(page.locator("#view-chapters")).toBeVisible();
    await expect(page.locator("#chapter-list .chapter-item")).toHaveCount(2);

    // 已分析（2 章皆 audio_ready）不得空態
    await page.locator('[data-filter="analyzed"]').click();
    await expect(page.locator("#chapter-list .chapter-item")).toHaveCount(2);
    await page.locator('[data-filter="all"]').click();
    await expect(page.locator("#chapter-list .chapter-item")).toHaveCount(2);

    // 0 剩餘時批次按鈕必須停用（非 silent no-op）
    await expect(page.locator("#btn-analyze-all")).toBeDisabled();
    await expect(page.locator("#btn-tts-all")).toBeDisabled();
  });

  test("375px 下 nav 不溢位（無橫向滾動）", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 844 });
    await mockSession(page, "admin");
    await page.goto("/");
    await page.waitForTimeout(600);
    const nav = page.locator("#main-nav");
    const box = await nav.evaluate((el) => ({ cw: el.clientWidth, sw: el.scrollWidth }));
    expect(box.sw).toBeLessThanOrEqual(box.cw + 1);
  });

  test("Reader 開啟 #/detail 不會看到作者工作區，導向公開作品頁", async ({ page }) => {
    await mockSession(page, "reader");
    await page.goto("/#/detail/b-res-1");
    await expect(page.locator("#view-chapters")).toBeHidden();
    await expect(page).toHaveURL(/#\/book\/b-res-1/);
    await expect(page.locator(".platform-detail-hero")).toBeVisible();
  });
});