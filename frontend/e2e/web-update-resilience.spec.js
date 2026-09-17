import { test, expect } from "@playwright/test";

test.describe("Web release/cache resilience", () => {
  test("open SPA detects a new release without clearing user storage or dirty work", async ({ page }) => {
    let releaseId = "release-a";
    await page.route("**/api/health/live", (route) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, service: "novel-reader-v3", releaseId }),
    }));

    await page.goto("/#/home");
    await page.evaluate(() => localStorage.setItem("storylingo-test-data", "keep-me"));
    await expect.poll(() => page.evaluate(() => Boolean(window.__storyLingoCheckRelease))).toBe(true);

    releaseId = "release-b";
    await page.evaluate(() => window.__storyLingoCheckRelease());
    await expect(page.locator("#release-update-banner")).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem("storylingo-test-data"))).toBe("keep-me");

    await page.locator("body").evaluate((body) => body.setAttribute("data-unsaved", "true"));
    page.once("dialog", (dialog) => dialog.dismiss());
    await page.locator(".release-update-button").click();
    await expect(page.locator("#release-update-banner")).toBeVisible();

    await page.locator("body").evaluate((body) => body.removeAttribute("data-unsaved"));
    page.once("dialog", (dialog) => dialog.accept());
    await Promise.all([
      page.waitForLoadState("domcontentloaded"),
      page.locator(".release-update-button").click(),
    ]);
    await expect(page).toHaveURL(/#\/home$/);
    expect(await page.evaluate(() => localStorage.getItem("storylingo-test-data"))).toBe("keep-me");
  });

  test("HTML and API observables are not treated as shared static cache data", async ({ request }) => {
    const shell = await request.get("/");
    expect(shell.headers()["cache-control"]).toBe("no-cache, must-revalidate");
    expect(await shell.text()).toContain('/seo.js?v=14');
    expect(await shell.text()).toContain('/platform.js?v=16');
    expect(shell.headers()["cloudflare-cdn-cache-control"]).toBe("no-store");
    const worker = await request.get("/sw.js?v=27");
    expect(worker.headers()["cache-control"]).toBe("no-cache, must-revalidate");
    expect(worker.headers()["cloudflare-cdn-cache-control"]).toBe("no-store");
    const asset = await request.get("/app.js?v=43");
    expect(asset.headers()["cache-control"]).toContain("immutable");
    const api = await request.get("/api/health/live");
    expect(api.headers()["cache-control"]).toBe("private, no-store");
  });
});
