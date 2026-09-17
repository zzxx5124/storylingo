import { beforeEach, describe, expect, it, vi } from "vitest";
import "../../services/bootstrap.js";
import "../../services/notifications.js";

describe("StoryLingoNotifications", () => {
  const coordinator = window.StoryLingoNotifications;

  beforeEach(() => {
    coordinator.resetForTests();
    document.body.innerHTML = '<nav><a id="nav-notifications" href="#/notifications">通知<span id="notification-badge" hidden></span></a></nav>';
    window.NovelApi = { request: vi.fn() };
  });

  it("只允許站內 hash target", () => {
    expect(coordinator._safeRoute("#/requests/10")).toBe(true);
    expect(coordinator._safeRoute("https://example.test")).toBe(false);
    expect(coordinator._safeRoute("#/requests//10")).toBe(false);
    expect(coordinator._safeRoute("javascript:alert(1)")).toBe(false);
  });

  it("late target response 不會覆寫使用者後來選擇的 route", () => {
    location.hash = "#/home";
    const navigation = coordinator.captureNavigation();
    location.hash = "#/notifications?filter=unread";
    expect(coordinator.navigateToTarget("#/home", navigation)).toBe(false);
    expect(location.hash).toBe("#/notifications?filter=unread");
  });

  it("principal 變更後不會套用舊通知的 target", () => {
    location.hash = "#/home";
    const navigation = coordinator.captureNavigation();
    document.dispatchEvent(new CustomEvent("storylingo:auth-changed"));
    expect(coordinator.navigateToTarget("#/requests/10", navigation)).toBe(false);
    expect(location.hash).toBe("#/home");
  });

  it("refresh 只取得 bounded badge/count，不會自動標記已讀", async () => {
    window.NovelApi.request.mockImplementation(async (path) => {
      if (path === "/api/auth/me") return { authed: true, user: { id: 7 } };
      if (path === "/api/notifications/unread-count") return { count: 120 };
      throw new Error(`unexpected ${path}`);
    });
    coordinator._state.started = true;
    await coordinator.refresh();
    expect(coordinator._state.unread).toBe(120);
    expect(document.querySelector("#notification-badge").textContent).toBe("99+");
    expect(window.NovelApi.request).not.toHaveBeenCalledWith("/api/notifications/read-all", expect.anything());
  });

  it("mark one/all 使用明確 mutation，且 badge 會同步", async () => {
    window.NovelApi.request.mockImplementation(async (path) => {
      if (path === "/api/notifications/9/read") return { ok: true };
      if (path === "/api/notifications/read-all") return { ok: true };
      return { authed: true, user: { id: 7 } };
    });
    coordinator._state.principalId = 7;
    coordinator._state.unread = 2;
    await coordinator.markRead(9);
    expect(window.NovelApi.request).toHaveBeenCalledWith("/api/notifications/9/read", { method: "POST", body: {} });
    expect(coordinator._state.unread).toBe(2);
    await coordinator.markRead(10, true);
    expect(coordinator._state.unread).toBe(1);
    await coordinator.markAllRead();
    expect(coordinator._state.unread).toBe(0);
    expect(window.NovelApi.request).toHaveBeenCalledWith("/api/notifications/read-all", { method: "POST", body: {} });
  });

  it("auth-changed 會清除舊帳號 badge、popover 與列表", () => {
    coordinator._state.principalId = 7;
    coordinator._state.unread = 3;
    coordinator._state.recent = [{ id: 1 }];
    document.dispatchEvent(new CustomEvent("storylingo:auth-changed"));
    expect(coordinator._state.principalId).toBeNull();
    expect(coordinator._state.unread).toBeNull();
    expect(coordinator._state.recent).toEqual([]);
    expect(document.querySelector("#notification-badge").hidden).toBe(true);
  });

  it("center list 以 query 傳送 filter/page，不接受未授權帳號 scope", async () => {
    window.NovelApi.request.mockImplementation(async (path) => {
      if (path === "/api/auth/me") return { authed: true, user: { id: 7 } };
      return { items: [], total: 0, unread: 0 };
    });
    const result = await coordinator.listCenter({ page: 2, pageSize: 20, filter: "unread", category: "generation" });
    expect(result.items).toEqual([]);
    expect(window.NovelApi.request).toHaveBeenCalledWith("/api/notifications?page=2&page_size=20&filter=unread&category=generation", undefined);
    expect(window.NovelApi.request.mock.calls.some(([path]) => path.includes("account_id"))).toBe(false);
  });
});
