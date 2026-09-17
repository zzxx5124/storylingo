import { test, expect } from "@playwright/test";

test.describe("後台 TTS 服務管理（V4 remediation）", () => {
  const providers = [];
  let nextId = 1;

  function installMocks(page) {
    const fulfill = (route, data, status = 200) => route.fulfill({
      status, contentType: "application/json",
      body: JSON.stringify(data),
    });
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 1, username: "管理員", role: "admin" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route("**/api/admin/dashboard", (route) => fulfill(route, {
      books: { total: 0, by_status: {} }, users: { total: 1, by_role: {} }, chapters: { audioReady: 0 },
    }));
    page.route("**/api/admin/tts/providers", (route) => {
      const method = route.request().method();
      if (method === "POST") {
        const body = JSON.parse(route.request().postData() || "{}");
        if (providers.some((p) => p.name === body.name)) {
          return fulfill(route, { detail: "provider 名稱已存在" }, 409);
        }
        const provider = {
          id: nextId++, name: body.name, providerType: body.providerType || "generic_http",
          baseUrl: body.baseUrl, synthPath: "/synthesize", voicesPath: "/voices",
          authScheme: body.authScheme || "x-api-key", adapterKey: "generic_http",
          enabled: true, isDefault: providers.length === 0, hasSecret: !!body.apiKey,
          configVersion: 1, lastStatus: "unknown", lastError: "", lastCheckedAt: null,
          capabilitiesStatus: "unknown", capabilitiesHash: "", capabilitiesCheckedAt: null,
        };
        providers.push(provider);
        return fulfill(route, provider);
      }
      return fulfill(route, { items: providers.map((p) => ({ ...p })) });
    });
    page.route(/\/api\/admin\/tts\/providers\/\d+$/, (route) => {
      const id = Number(route.request().url().split("/").pop());
      const method = route.request().method();
      const provider = providers.find((p) => p.id === id);
      if (method === "PUT") {
        const body = JSON.parse(route.request().postData() || "{}");
        if (body.name) provider.name = body.name;
        if (body.baseUrl) provider.baseUrl = body.baseUrl;
        if (body.isDefault) providers.forEach((p) => { p.isDefault = p.id === id; });
        if (body.apiKey) provider.hasSecret = true;
        return fulfill(route, { ...provider });
      }
      if (method === "DELETE") {
        const idx = providers.findIndex((p) => p.id === id);
        if (idx >= 0) providers.splice(idx, 1);
        return fulfill(route, { ok: true });
      }
      return fulfill(route, { ...provider });
    });
    page.route(/\/api\/admin\/tts\/providers\/\d+\/test$/, (route) => {
      const id = Number(route.request().url().split("/").at(-2));
      const provider = providers.find((p) => p.id === id);
      if (provider) { provider.lastStatus = "ok"; provider.lastError = ""; }
      return fulfill(route, { ok: true, voices: [{ id: "v1" }] });
    });
  }

  test("名稱欄位可輸入唯一名稱，可新增第二個 provider；重複名稱被拒", async ({ page }) => {
    await installMocks(page);
    await page.goto("/#/admin");
    await expect(page.locator("#view-admin")).toBeVisible();

    await page.locator('[data-atab="tts"]').click();
    // 名稱欄位可見且可輸入（不再固定名稱）
    await expect(page.locator("#tts-name")).toBeVisible();
    await expect(page.locator("#tts-name")).toHaveValue("");
    await expect(page.locator('a[href="/api/tts/spec"][download]')).toBeVisible();
    await expect(page.locator(".site-footer a[href=\"/api/tts/spec\"]")).toHaveCount(0);

    // 新增第一個
    await page.fill("#tts-name", "主力語音");
    await page.fill("#tts-url", "https://tts.example.com");
    await page.fill("#tts-key", "secret");
    await page.locator("#tts-add").click();
    await expect(page.locator('[data-tts-id="1"]')).toContainText("主力語音");
    await expect(page.locator('[data-tts-id="1"]')).toContainText("已設定金鑰");

    // 新增第二個（不同名稱 → 成功）
    await page.fill("#tts-name", "備援語音");
    await page.fill("#tts-url", "https://tts2.example.com");
    await page.fill("#tts-key", "secret2");
    await page.locator("#tts-add").click();
    await expect(page.locator('[data-tts-id="2"]')).toContainText("備援語音");

    // 重複名稱 → 錯誤 toast（backed 409）
    await page.fill("#tts-name", "主力語音");
    await page.fill("#tts-url", "https://tts3.example.com");
    await page.locator("#tts-add").click();
    await expect(page.locator(".toast-error")).toContainText("provider 名稱已存在");
    // 仍只有兩個
    await expect(page.locator("[data-tts-id]")).toHaveCount(2);
  });

  test("capabilities 404 不顯示 error：/voices 健康 + 不支援 capabilities 同時呈現", async ({ page }) => {
    providers.length = 0;
    providers.push({
      id: 1, name: "契約服務", providerType: "generic_http", baseUrl: "https://tts.example.com",
      synthPath: "/synthesize", voicesPath: "/voices", authScheme: "x-api-key", adapterKey: "generic_http",
      enabled: true, isDefault: true, hasSecret: true, configVersion: 1,
      lastStatus: "ok", lastError: "", lastCheckedAt: "2026-08-18T00:00:00",
      capabilitiesStatus: "unsupported", capabilitiesHash: "", capabilitiesCheckedAt: "2026-08-18T00:00:00",
    });
    nextId = 2;
    await installMocks(page);
    await page.goto("/#/admin");
    await expect(page.locator("#view-admin")).toBeVisible();
    await page.locator('[data-atab="tts"]').click();
    const row = page.locator('[data-tts-id="1"]');
    await expect(row).toBeVisible();
    // 連線狀態顯示「可用」+「不支援 capabilities」，且不得出現 error 字樣
    await expect(row).toContainText("連線狀態：可用");
    await expect(row).toContainText("不支援 capabilities");
    await expect(row).not.toContainText("error");
    await expect(row).not.toContainText("HTTP 404");
  });
});
