import { test, expect } from "@playwright/test";

function mockAuthor(page, { hasPrologue = true, hasBook = false } = {}) {
  const state = {
    hasPrologue,
    hasBook,
    manualPayload: null,
    updates: [],
    book: {
      id: "b-prologue",
      bid: "b-prologue",
      title: "序章設定測試書",
      synopsis: "",
      category: "zh",
      vocabLevel: "AUTO",
      categories: ["zh"],
      categoryId: null,
      serial: "連載",
      status: "draft",
      chapters: hasBook ? [{ id: 1, seq: 0, title: "第一章", chars: 5, status: "pending", audio: "none", error: "", textHash: "h1" }] : [],
      settings: {},
      voices: {},
      voicePrefs: {},
      coverImage: null,
      ownerId: 7,
      rejectReason: "",
      totalChars: hasBook ? 5 : 0,
      created: "2026-08-30",
      hasPrologue,
      languageTypeEditable: true,
      languageTypeLockReason: "",
    },
  };
  const fulfill = (route, data) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(data),
  });
  page.route("**/api/auth/me", (route) => fulfill(route, {
    authed: true, user: { id: 7, username: "作者", role: "author" },
  }));
  page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
  page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
  page.route(/\/api\/books(?:\?.*)?$/, (route) => fulfill(route, []));
  page.route(/\/api\/books\?mine=1/, (route) => fulfill(route, state.hasBook ? [state.book] : []));
  page.route("**/api/books/manual", (route) => {
    state.manualPayload = JSON.parse(route.request().postData() || "{}");
    state.hasPrologue = state.manualPayload.hasPrologue;
    state.hasBook = true;
    state.book.hasPrologue = state.hasPrologue;
    return fulfill(route, state.book);
  });
  page.route(/\/api\/books\/b-prologue$/, (route) => {
    if (route.request().method() === "PUT") {
      const payload = JSON.parse(route.request().postData() || "{}");
      state.updates.push(payload);
      if (typeof payload.hasPrologue === "boolean") {
        state.hasPrologue = payload.hasPrologue;
        state.book.hasPrologue = payload.hasPrologue;
      }
    }
    return fulfill(route, state.book);
  });
  return state;
}

test.describe("作品序章設定", () => {
  test("建立作品可選擇無序章並傳給 backend", async ({ page }) => {
    const state = mockAuthor(page);
    await page.goto("/#/mine");
    await page.locator("#btn-new-book-empty").click();
    await page.locator('input[name="nb-prologue"][value="false"]').check();
    await page.locator("#nb-title").fill("沒有序章的書");
    await page.locator("#nb-submit").click();
    await expect(page.locator("#chapter-modal")).toBeVisible();
    expect(state.manualPayload.hasPrologue).toBe(false);
  });

  test("無序章作品的 seq=0 顯示為第 1 章，編輯設定可保存", async ({ page }) => {
    const state = mockAuthor(page, { hasPrologue: false, hasBook: true });
    await page.goto("/#/mine");
    await page.locator('[data-mopen="b-prologue"]').click();
    await expect(page.locator("#view-chapters")).toBeVisible();
    await expect(page.locator(".chapter-item .ch-no")).toHaveText("1");

    await page.goto("/#/mine");
    await page.locator('[data-medit="b-prologue"]').click();
    await expect(page.locator('input[name="be-prologue"][value="false"]')).toBeChecked();
    await page.locator('input[name="be-prologue"][value="true"]').check();
    await page.locator("#be-submit").click();
    await expect(page.getByText("已儲存")).toBeVisible();
    expect(state.updates.at(-1).hasPrologue).toBe(true);
  });
});
