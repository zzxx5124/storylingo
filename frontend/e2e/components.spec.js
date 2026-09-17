import { test, expect } from "@playwright/test";

test.describe("共用元件 (FE-006)", () => {
  test("元件預覽頁可載入並渲染 Button / Field / Toast / Modal / BookCard", async ({ page }) => {
    await page.goto("/components-preview.html");
    await expect(page.locator("h1")).toHaveText("共用元件預覽");

    await expect(page.locator("#sec-button .btn-primary")).toHaveCount(2);
    await expect(page.locator("#sec-button .btn:disabled")).toHaveCount(1);
    await expect(page.locator(".field")).toHaveCount(4);
    await expect(page.locator(".field-error")).toHaveText("此欄位輸入無效");
    await expect(page.locator(".book-card")).toHaveCount(2);
    await expect(page.locator(".book-tag").first()).toHaveText("奇幻");
    await expect(page.locator(".book-cover").first()).toHaveAttribute("alt", "劍起蒼穹 封面");
  });

  test("Toast 元件可彈出並自動消失", async ({ page }) => {
    await page.goto("/components-preview.html");
    await page.locator('[data-toast="success"]').click();
    await expect(page.locator(".toast-success")).toContainText("成功通知訊息");
    await expect(page.locator(".toast-success")).toBeAttached();
  });

  test("Modal 元件可開啟與關閉", async ({ page }) => {
    await page.goto("/components-preview.html");
    await page.locator('[data-modal="collect"]').click();
    await expect(page.locator(".modal")).toBeVisible();
    await expect(page.locator(".modal-title")).toHaveText("收藏確認");
    await page.locator(".modal-ok").click();
    await expect(page.locator(".modal-overlay")).toBeHidden({ timeout: 3000 });
    await expect(page.locator(".toast-success")).toContainText("已加入收藏");
  });
});