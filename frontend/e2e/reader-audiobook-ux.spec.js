import { test, expect } from "@playwright/test";

function fulfill(route, data, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
}

function readPayload(seq, next = null, audio = "ready") {
  return {
    book: { id: "demo", title: "Reader UX 測試書", category: "zh" },
    chapter: { seq, title: "第 " + (seq + 1) + " 章", text: "這是一段可閱讀的正文。\n第二段正文。", chars: 20, audio },
    navigation: { previous: seq > 0 ? seq - 1 : null, next, total: next == null ? seq + 1 : next + 1 },
  };
}

async function mockReader(page, options = {}) {
  await page.route("**/api/auth/me", (route) => fulfill(route, options.user ? { authed: true, user: options.user } : { authed: false }));
  await page.route("**/api/books/demo/read/*", (route) => {
    const seq = Number(new URL(route.request().url()).pathname.split("/").pop());
    return fulfill(route, readPayload(seq, options.nextBySeq?.[seq] ?? null, options.audioBySeq?.[seq] || "ready"));
  });
  await page.route("**/api/books/demo/chapters/*", (route) => fulfill(route, {
    analysis: { segments: [{ type: "narration", text: "這是一段可閱讀的正文。", speaker: "旁白" }] },
    timing: { segments: [{ dur: 2 }] },
  }));
  await page.route("**/api/books/demo/audio/*", (route) => route.fulfill({
    status: 200, contentType: "audio/mpeg", body: Buffer.from("ID3" + "\x00".repeat(256)),
  }));
  await page.route("**/api/books/demo/view", (route) => fulfill(route, { ok: true }));
  await page.route("**/api/me/progress", (route) => fulfill(route, { items: [] }));
}

test.describe("公開 Reader / Audiobook UX", () => {
  test("listen reader 提供明確 controls，初次載入不自動播放", async ({ page }) => {
    await mockReader(page, { nextBySeq: { 0: 1, 1: null } });
    await page.goto("/#/read/demo/0?mode=listen");
    await expect(page.locator("#reader-audio")).toBeVisible();
    await expect(page.locator("#reader-auto-next")).toBeVisible();
    await expect(page.locator("#reader-bookmark")).toHaveAccessibleName("加入書籤");
    await expect(page.locator("#reader-fullscreen")).toHaveAccessibleName("全螢幕閱讀");
    await expect(page.locator("#reader-audio")).toHaveJSProperty("paused", true);
    await expect(page.locator("#reader-controls-toggle")).toHaveAttribute("aria-expanded", "true");
  });

  test("啟用 auto-next 後只銜接下一個 playable chapter", async ({ page }) => {
    await mockReader(page, { nextBySeq: { 0: 1, 1: null }, audioBySeq: { 0: "ready", 1: "ready" } });
    await page.goto("/#/read/demo/0?mode=listen");
    await page.locator("#reader-auto-next").check();
    await page.locator("#reader-audio").dispatchEvent("ended");
    await expect.poll(() => page.url()).toContain("/read/demo/1");
    await expect(page.locator("#reader-audio")).toHaveJSProperty("paused", true);
  });

  test("下一章沒有音訊時停在邊界，不跳到純文字章節", async ({ page }) => {
    await mockReader(page, { nextBySeq: { 0: 1, 1: null }, audioBySeq: { 0: "ready", 1: "none" } });
    await page.goto("/#/read/demo/0?mode=listen");
    await page.locator("#reader-auto-next").check();
    await page.locator("#reader-audio").dispatchEvent("ended");
    await expect(page.locator("#reader-audio-status")).toContainText("尚未生成可播放音訊");
    await expect(page).toHaveURL(/\/read\/demo\/0/);
  });

  test("audio error 顯示安全訊息，375px controls 不造成主要水平溢出", async ({ page }) => {
    await mockReader(page, { nextBySeq: { 0: null } });
    await page.goto("/#/read/demo/0?mode=listen");
    await page.locator("#reader-audio").dispatchEvent("error");
    await expect(page.locator("#reader-audio-status")).toContainText("音訊暫時無法載入");
    await expect(page.locator("#reader-audio-retry")).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    expect(overflow).toBe(false);
  });

  test("chapter API denied 時只顯示公開安全錯誤，不渲染 backend detail", async ({ page }) => {
    await page.route("**/api/auth/me", (route) => fulfill(route, { authed: false }));
    await page.route("**/api/books/demo/read/*", (route) => fulfill(route, { detail: "provider secret / storage path", token: "do-not-render" }, 403));
    await page.goto("/#/read/demo/0?mode=reading");
    await expect(page.locator(".platform-error")).toContainText("此章節目前不可閱讀");
    await expect(page.locator(".platform-error")).not.toContainText("provider");
    await expect(page.locator(".platform-error")).not.toContainText("do-not-render");
  });

  test("登入使用者可在目前章節建立 Account-scoped bookmark", async ({ page }) => {
    await mockReader(page, { user: { id: 7, username: "讀者", role: "reader" }, nextBySeq: { 0: null } });
    let bookmarkBody = null;
    await page.route("**/api/me/bookmarks", async (route) => {
      if (route.request().method() === "POST") {
        bookmarkBody = route.request().postDataJSON();
        return fulfill(route, { id: 9 });
      }
      return fulfill(route, { items: [] });
    });
    await page.goto("/#/read/demo/0?mode=reading");
    await page.locator("#reader-bookmark").click();
    await expect(page.locator("#reader-bookmark")).toHaveText("已加入書籤");
    expect(bookmarkBody).toMatchObject({ bookId: "demo", chapterSeq: 0, note: "" });
  });

  test("訪客捲動會保存 bounded reader progress，不含敏感資料", async ({ page }) => {
    await mockReader(page, { nextBySeq: { 0: null } });
    await page.goto("/#/read/demo/0?mode=reading");
    await page.evaluate(() => {
      const body = document.querySelector("#reader-body");
      Object.defineProperty(body, "scrollHeight", { configurable: true, value: 1200 });
      Object.defineProperty(body, "clientHeight", { configurable: true, value: 400 });
      Object.defineProperty(body, "scrollTop", { configurable: true, writable: true, value: 120 });
      body.dispatchEvent(new Event("scroll"));
    });
    await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem("novel-reader:demo") || "null"))).toMatchObject({
      bid: "demo", seq: 0, position: 120, lastMode: "read",
    });
    const stored = await page.evaluate(() => localStorage.getItem("novel-reader:demo"));
    expect(stored).not.toContain("token");
    expect(stored).not.toContain("password");
  });

  test("Wake Lock 與 Media Session 是 progressive enhancement，不取代 native audio", async ({ page }) => {
    await page.addInitScript(() => {
      window.__wakeCalls = 0;
      Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
      Object.defineProperty(navigator, "wakeLock", {
        configurable: true,
        value: { request: async () => { window.__wakeCalls += 1; return { release: async () => {}, addEventListener: () => {} }; } },
      });
      window.MediaMetadata = class { constructor(values) { window.__mediaMetadata = values; Object.assign(this, values); } };
      Object.defineProperty(navigator, "mediaSession", {
        configurable: true,
        value: { metadata: null, handlers: {}, setActionHandler(action, handler) { this.handlers[action] = handler; } },
      });
    });
    await mockReader(page, { nextBySeq: { 0: null } });
    await page.goto("/#/read/demo/0?mode=listen");
    await expect.poll(() => page.evaluate(() => window.__mediaMetadata?.title || "")).toBe("第 1 章");
    await page.evaluate(() => {
      const audio = document.querySelector("#reader-audio");
      Object.defineProperty(audio, "paused", { configurable: true, value: false });
      audio.dispatchEvent(new Event("play"));
    });
    await expect.poll(() => page.evaluate(() => window.__wakeCalls)).toBe(1);
  });

  test("舊 route 的慢回應與舊 audio ended 不得污染新章節", async ({ page }) => {
    let delayFirstRead = true;
    await page.route("**/api/auth/me", (route) => fulfill(route, { authed: false }));
    await page.route("**/api/books/demo/read/*", async (route) => {
      const seq = Number(new URL(route.request().url()).pathname.split("/").pop());
      if (seq === 0 && delayFirstRead) await new Promise((resolve) => setTimeout(resolve, 180));
      return fulfill(route, readPayload(seq, null, "ready"));
    });
    await page.route("**/api/books/demo/chapters/*", (route) => fulfill(route, {
      analysis: { segments: [{ type: "narration", text: "章節正文。", speaker: "旁白" }] },
      timing: { segments: [{ dur: 2 }] },
    }));
    await page.route("**/api/books/demo/audio/*", (route) => route.fulfill({ status: 200, contentType: "audio/mpeg", body: Buffer.from("ID3") }));
    await page.route("**/api/books/demo/view", (route) => fulfill(route, { ok: true }));
    await page.route("**/api/me/progress", (route) => fulfill(route, { items: [] }));
    await page.goto("/#/read/demo/0?mode=listen");
    const oldAudio = await page.locator("#reader-audio").elementHandle().catch(() => null);
    await page.evaluate(() => { location.hash = "#/read/demo/1?mode=listen"; });
    await expect(page.locator("#reader-sheet h2")).toHaveText("第 2 章");
    if (oldAudio) await oldAudio.evaluate((audio) => audio.dispatchEvent(new Event("ended")));
    await expect(page).toHaveURL(/\/read\/demo\/1/);
    delayFirstRead = false;
  });
});
