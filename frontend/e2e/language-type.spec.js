import { test, expect } from "@playwright/test";

function installAuthorBook(page, { editable = true } = {}) {
  const state = {
    book: {
      id: "b-language-type",
      bid: "b-language-type",
      title: "語言型別測試書",
      synopsis: "",
      category: "vocab",
      vocabLevel: "AUTO",
      categories: ["vocab"],
      categoryId: null,
      serial: "連載",
      status: "draft",
      chapters: [],
      settings: {},
      voices: {},
      voicePrefs: {},
      coverImage: null,
      ownerId: 7,
      rejectReason: "",
      totalChars: 0,
      created: "2026-08-29",
      languageTypeEditable: editable,
      languageTypeLockReason: editable ? "" : "作品已有分析資料，無法直接修改語言型別。",
    },
    updates: [],
  };
  const fulfill = (route, data, status = 200) => route.fulfill({
    status, contentType: "application/json", body: JSON.stringify(data),
  });
  page.route("**/api/auth/me", (route) => fulfill(route, {
    authed: true, user: { id: 7, username: "作者", role: "author" },
  }));
  page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
  page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
  page.route(/\/api\/books(?:\?.*)?$/, (route) => {
    if (new URL(route.request().url()).searchParams.get("mine") === "1") {
      return fulfill(route, [state.book]);
    }
    return fulfill(route, []);
  });
  page.route(/\/api\/books\/b-language-type$/, async (route) => {
    if (route.request().method() === "PUT") {
      const payload = JSON.parse(route.request().postData() || "{}");
      state.updates.push(payload);
      if (payload.category) state.book.category = payload.category;
      return fulfill(route, state.book);
    }
    return fulfill(route, state.book);
  });
  return state;
}

test.describe("作品語言型別編輯", () => {
  test("未分析作品可修改語言型別並保存", async ({ page }) => {
    const state = installAuthorBook(page);
    await page.goto("/#/mine");
    await page.locator('[data-medit="b-language-type"]').click();

    const selector = page.locator("#be-language-type");
    await expect(selector).toBeEnabled();
    await expect(selector.locator('option[value="other"]')).toHaveText("其它");
    await selector.selectOption("zh");
    await page.locator("#be-submit").click();
    await expect(page.getByText("已儲存")).toBeVisible();
    expect(state.updates).toHaveLength(1);
    expect(state.updates[0].category).toBe("zh");

    await page.reload();
    await page.locator('[data-medit="b-language-type"]').click();
    await expect(page.locator("#be-language-type")).toHaveValue("zh");
  });

  test("已有分析資料時語言型別唯讀且不送出修改", async ({ page }) => {
    const state = installAuthorBook(page, { editable: false });
    await page.goto("/#/mine");
    await page.locator('[data-medit="b-language-type"]').click();

    await expect(page.locator("#be-language-type")).toBeDisabled();
    await expect(page.locator("#be-language-type-hint")).toContainText("已有分析資料");
    await page.locator("#be-submit").click();
    expect(state.updates).toHaveLength(1);
    expect(state.updates[0]).not.toHaveProperty("category");
  });
});
