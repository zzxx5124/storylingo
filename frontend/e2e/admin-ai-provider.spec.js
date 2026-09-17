import { test, expect } from "@playwright/test";

test.describe("後台 AI 服務管理（V4 remediation）", () => {
  const providers = [];
  let nextId = 1;

  function deferred() {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
  }

  function installMocks(page, { dashboardGate = null } = {}) {
    const fulfill = (route, data, status = 200) => route.fulfill({
      status, contentType: "application/json",
      body: JSON.stringify(data),
    });
    page.route("**/api/auth/me", (route) => fulfill(route, { authed: true, user: { id: 1, username: "管理員", role: "admin" } }));
    page.route("**/api/voices", (route) => fulfill(route, { voices: [] }));
    page.route("**/api/categories", (route) => fulfill(route, { categories: [] }));
    page.route(/\/api\/books(\?.*)?$/, (route) => fulfill(route, []));
    page.route("**/api/admin/dashboard", async (route) => {
      if (dashboardGate) await dashboardGate.promise;
      return fulfill(route, {
      books: { total: 0, by_status: {} }, users: { total: 1, by_role: {} }, chapters: { audioReady: 0 },
      });
    });
    page.route("**/api/admin/ai/providers", (route) => {
      const method = route.request().method();
      if (method === "POST") {
        const body = JSON.parse(route.request().postData() || "{}");
        const provider = {
          id: nextId++, name: body.name, providerType: body.providerType || "openai_compatible",
          baseUrl: body.baseUrl, model: body.model, fallbackModel: body.fallbackModel || null,
          enabled: body.enabled !== false, isDefault: !!body.isDefault, hasSecret: !!body.apiKey,
          configVersion: 1, lastStatus: "unknown", lastError: "", lastCheckedAt: null,
        };
        if (provider.isDefault) providers.forEach((p) => { p.isDefault = false; });
        providers.push(provider);
        return fulfill(route, provider);
      }
      return fulfill(route, { items: providers.map((p) => ({ ...p })) });
    });
    page.route(/\/api\/admin\/ai\/providers\/\d+$/, (route) => {
      const id = Number(route.request().url().split("/").pop());
      const method = route.request().method();
      const provider = providers.find((p) => p.id === id);
      if (method === "PUT") {
        const body = JSON.parse(route.request().postData() || "{}");
        if (body.name) provider.name = body.name;
        if (body.baseUrl) provider.baseUrl = body.baseUrl;
        if (body.model) provider.model = body.model;
        if (body.providerType) provider.providerType = body.providerType;
        if (body.fallbackModel !== undefined) provider.fallbackModel = body.fallbackModel;
        if (body.enabled !== undefined) provider.enabled = body.enabled;
        if (body.isDefault !== undefined) {
          provider.isDefault = body.isDefault;
          if (body.isDefault) providers.forEach((p) => { if (p.id !== id) p.isDefault = false; });
        }
        if (body.apiKey) provider.hasSecret = true;
        provider.configVersion += 1;
        return fulfill(route, { ...provider });
      }
      if (method === "DELETE") {
        const idx = providers.findIndex((p) => p.id === id);
        if (idx >= 0) providers.splice(idx, 1);
        return fulfill(route, { ok: true });
      }
      return fulfill(route, { ...provider });
    });
    page.route(/\/api\/admin\/ai\/providers\/\d+\/test$/, (route) => {
      const id = Number(route.request().url().split("/").at(-2));
      const provider = providers.find((p) => p.id === id);
      if (provider) { provider.lastStatus = "ok"; provider.lastError = ""; }
      return fulfill(route, { ok: true, models: 2, statusCode: 200 });
    });
  }

  test.beforeEach(() => {
    providers.length = 0;
    nextId = 1;
  });

  test("dashboard 晚到時不會清除已切換 AI tab 的輸入", async ({ page }) => {
    const dashboardGate = deferred();
    await installMocks(page, { dashboardGate });
    await page.goto("/#/admin");
    await expect(page.locator("#view-admin")).toBeVisible();

    await page.locator('[data-atab="ai"]').click();
    await expect(page.locator("#ai-submit")).toBeVisible();
    await page.fill("#ai-name", "延遲穩定");

    const dashboardResponse = page.waitForResponse((response) => response.url().endsWith("/api/admin/dashboard"));
    dashboardGate.resolve();
    await dashboardResponse;
    await expect(page.locator("#ai-name")).toHaveValue("延遲穩定");
  });

  test("admin 可新增／編輯／設預設／測試／刪除 AI provider，且金鑰不回傳", async ({ page }) => {
    await installMocks(page);
    await page.goto("/#/admin");
    await expect(page.locator("#view-admin")).toBeVisible();

    // 切到 AI 服務 tab
    await page.locator('[data-atab="ai"]').click();
    await expect(page.locator("#ai-submit")).toBeVisible();

    // 新增（含 API key）
    await page.fill("#ai-name", "主力分析");
    await page.fill("#ai-url", "https://api.openai.com/v1");
    await page.fill("#ai-model", "gpt-4o-mini");
    await page.fill("#ai-key", "sk-topsecret");
    await page.locator("#ai-default").check();
    await page.locator("#ai-submit").click();
    await expect(page.locator('[data-ai-id="1"]')).toContainText("主力分析");
    await expect(page.locator('[data-ai-id="1"]')).toContainText("gpt-4o-mini");
    await expect(page.locator('[data-ai-id="1"]')).toContainText("已設定金鑰");
    await expect(page.locator('[data-ai-id="1"]')).toContainText("預設");
    // API key 絕不出現在頁面
    await expect(page.locator("body")).not.toContainText("sk-topsecret");

    // 測試連線
    await page.locator('[data-ai-test="1"]').click();
    await expect(page.locator('[data-ai-id="1"]')).toContainText("可用");

    // 編輯：載入既有值，更新 model
    await page.locator('[data-ai-edit="1"]').click();
    await expect(page.locator("#ai-submit")).toHaveText("儲存變更");
    await expect(page.locator("#ai-name")).toHaveValue("主力分析");
    await expect(page.locator("#ai-model")).toHaveValue("gpt-4o-mini");
    // 編輯不顯示金鑰，留空代表不更換
    await expect(page.locator("#ai-key")).toHaveValue("");
    await page.fill("#ai-model", "gpt-4o");
    const updateResponse = page.waitForResponse((response) =>
      response.request().method() === "PUT" && /\/api\/admin\/ai\/providers\/1$/.test(response.url()));
    await page.locator("#ai-submit").click();
    await updateResponse;
    await expect(page.locator("#ai-submit")).toHaveText("新增服務");
    await expect(page.locator('[data-ai-id="1"] .arev-sub').first()).toHaveText(/· gpt-4o ·/);

    // 新增第二個並設為預設 → 第一個不再是預設
    await page.fill("#ai-name", "備援分析");
    await page.fill("#ai-url", "https://ai.example.com/v1");
    await page.fill("#ai-model", "deepseek-v4");
    await page.locator("#ai-default").check();
    await page.locator("#ai-submit").click();
    await expect(page.locator('[data-ai-id="2"]')).toContainText("備援分析");
    await expect(page.locator('[data-ai-id="2"] .chip')).toContainText("預設");
    await expect(page.locator('[data-ai-id="1"] .chip')).not.toContainText("預設");

    // 停用再啟用
    await page.locator('[data-ai-toggle="2"]').click();
    await expect(page.locator('[data-ai-id="2"]')).toContainText("停用");
    await page.locator('[data-ai-toggle="2"]').click();
    await expect(page.locator('[data-ai-id="2"]')).toContainText("啟用");

    // 刪除
    page.on("dialog", (d) => d.accept());
    await page.locator('[data-ai-delete="1"]').click();
    await expect(page.locator('[data-ai-id="1"]')).toHaveCount(0);
    await expect(page.locator('[data-ai-id="2"]')).toHaveCount(1);
  });
});
