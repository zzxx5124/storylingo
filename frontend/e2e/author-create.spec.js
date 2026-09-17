import { test, expect } from "@playwright/test";

test.describe("建立第一本作品（Phase 15a）", () => {
  function mockAuthorSession(page) {
    const state = { chapters: [], title: "我的第一本書", manualCreates: 0, mine: false, coverUploads: 0, coverImage: null };
    const bookPayload = () => ({
      id: "b-manual-1",
      bid: "b-manual-1",
      title: state.title,
      synopsis: "",
      category: "vocab",
      vocabLevel: "AUTO",
      categories: ["vocab"],
      serial: "連載",
      status: "draft",
      chapters: state.chapters,
      settings: {},
      voicePrefs: {},
      coverImage: state.coverImage,
      ownerId: 7,
      rejectReason: "",
      totalChars: 0,
      created: "2026-08-17",
    });
    const fulfill = (route, data) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
    page.route("**/api/auth/me", (route) => fulfill(route, {
      authed: true, user: { id: 7, username: "作者一", role: "author" },
    }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books($|\?)/, (route) => fulfill(route, []));
    page.route(/\/api\/books\?mine=1/, (route) => fulfill(route, state.mine ? [bookPayload()] : []));
    page.route("**/api/books/manual", (route) => {
      state.manualCreates += 1;
      const payload = JSON.parse(route.request().postData() || "{}");
      state.title = payload.title || state.title;
      return fulfill(route, bookPayload());
    });
    page.route(/\/api\/books\/b-manual-1\/chapters$/, (route) => {
      const payload = JSON.parse(route.request().postData() || "{}");
      state.chapters = [{
        id: 1, seq: 0, title: payload.title || "新章節",
        chars: (payload.text || "").length, status: "pending", audio: "none", error: "", textHash: "h1",
      }];
      return fulfill(route, bookPayload());
    });
    page.route(/\/api\/books\/b-manual-1\/cover$/, (route) => {
      state.coverUploads += 1;
      state.coverImage = "/api/books/b-manual-1/cover?v=2";
      return fulfill(route, { coverImage: state.coverImage });
    });
    page.route(/\/api\/books\/b-manual-1$/, (route) => {
      if (route.request().method() === "GET") return fulfill(route, bookPayload());
      return route.continue();
    });
    return state;
  }

  test("作者從 Creation 空狀態建立作品、立即寫第一章，章節出現在列表", async ({ page }) => {
    await mockAuthorSession(page);
    await page.goto("/#/mine");
    const mine = page.locator("#mine-list");
    await expect(mine).toContainText("還沒有作品");
    await expect(mine).not.toContainText("回到書庫上傳第一本小說吧");
    // 空狀態主要 CTA
    await page.locator("#btn-new-book-empty").click();
    await expect(page.locator("#newbook-modal")).toBeVisible();
    await page.locator("#nb-title").fill("我的第一本書");
    await page.locator("#nb-submit").click();
    // 建立成功 → 直接進入該作品 → 自動引導新增第一章
    await expect(page.locator("#view-chapters")).toBeVisible();
    await expect(page.locator("#chapter-modal")).toBeVisible();
    await expect(page.locator("#chapter-modal .modal-title")).toHaveText("新增章節");
    await page.locator("#new-ch-title").fill("第一章 開始");
    await page.locator("#new-ch-text").fill("故事從這裡開始。");
    await page.locator("#new-ch-submit").click();
    await expect(page.locator("#chapter-modal")).toBeHidden();
    await expect(page.locator(".chapter-item").first()).toContainText("第一章 開始");
  });

  test("既有空作品在章節列表提供「新增第一章」CTA", async ({ page }) => {
    await mockAuthorSession(page);
    await page.goto("/#/detail/b-manual-1");
    await expect(page.locator("#view-chapters")).toBeVisible();
    await expect(page.locator("#btn-add-first-chapter")).toBeVisible();
    await page.locator("#btn-add-first-chapter").click();
    await expect(page.locator("#chapter-modal")).toBeVisible();
  });

  test("我的創作點擊封面可開啟選擇器並上傳封面", async ({ page }) => {
    const state = await mockAuthorSession(page);
    state.mine = true;
    await page.goto("/#/mine");
    const cover = page.locator("#mine-list .mine-card .book-cover");
    await expect(cover).toBeVisible();

    const chooserPromise = page.waitForEvent("filechooser");
    await cover.click();
    const chooser = await chooserPromise;
    await chooser.setFiles({
      name: "cover.png",
      mimeType: "image/png",
      buffer: Buffer.from("test-image"),
    });

    await expect(page.getByText("封面已更新")).toBeVisible();
    expect(state.coverUploads).toBe(1);
  });

  test("建立作品快速雙擊只送出一次且按鈕會鎖定", async ({ page }) => {
    const state = await mockAuthorSession(page);
    await page.goto("/#/mine");
    await page.locator("#btn-new-book-empty").click();
    await page.locator("#nb-title").fill("避免重複建立");
    await page.locator("#nb-submit").dblclick();
    await expect(page.locator("#view-chapters")).toBeVisible();
    expect(state.title).toBe("避免重複建立");
    expect(state.manualCreates).toBe(1);
  });

  test("離開作品詳情時不會讓新增章節 modal 攔截下一頁操作", async ({ page }) => {
    await mockAuthorSession(page);
    await page.goto("/#/mine");
    await page.locator("#btn-new-book-empty").click();
    await page.locator("#nb-title").fill("離開作品測試");
    await page.locator("#nb-submit").click();
    await expect(page.locator("#chapter-modal")).toBeVisible();
    await page.goto("/#/mine");
    await expect(page.locator("#chapter-modal")).toBeHidden();
    await expect(page.locator("#mine-list")).toBeVisible();
  });
});
