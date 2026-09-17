import { beforeEach, describe, expect, it, vi } from "vitest";
import "../../services/bootstrap.js";
import "../../services/announcements.js";

const item = (overrides = {}) => ({
  id: 1, displayVersion: 1, title: "平台公告", bodyText: "這是公告內容。",
  displayMode: "once_per_version", ctaLabel: "", ctaTarget: "", ...overrides,
});

describe("StoryLingoAnnouncements", () => {
  const coordinator = window.StoryLingoAnnouncements;

  beforeEach(() => {
    coordinator.resetForTests();
    document.body.innerHTML = "";
    localStorage.clear();
    sessionStorage.clear();
  });

  it("拒絕危險 CTA，只允許站內 hash 或 HTTPS", () => {
    expect(coordinator._isSafeTarget("#/privacy")).toBe(true);
    expect(coordinator._isSafeTarget("https://example.test/help")).toBe(true);
    expect(coordinator._isSafeTarget("javascript:alert(1)")).toBe(false);
    expect(coordinator._isSafeTarget("data:text/html,boom")).toBe(false);
    expect(coordinator._isSafeTarget("http://example.test")).toBe(false);
  });

  it("以 textContent 呈現內容且一次只顯示一個 popup", () => {
    coordinator._state.started = true;
    coordinator._state.queue = [item({ title: "<img src=x>", bodyText: "<script>bad</script>" }), item({ id: 2, title: "第二則" })];
    coordinator._show(coordinator._state.queue[0]);
    expect(document.querySelector(".announcement-modal-title").textContent).toBe("<img src=x>");
    expect(document.querySelector(".announcement-modal-copy").textContent).toBe("<script>bad</script>");
    expect(document.querySelectorAll(".announcement-modal-overlay")).toHaveLength(1);
    document.querySelector(".announcement-modal-close").click();
    expect(localStorage.getItem("storylingo_announcement_ack_v1")).toContain("1:1");
  });

  it("once_per_session 只寫 sessionStorage，不寫 durable localStorage", () => {
    coordinator._state.started = true;
    const sessionItem = item({ displayMode: "once_per_session" });
    coordinator._state.queue = [sessionItem];
    coordinator._show(sessionItem);
    document.querySelector(".announcement-modal-close").click();
    expect(sessionStorage.getItem("storylingo_announcement_session_v1")).toContain("1:1");
    expect(localStorage.getItem("storylingo_announcement_ack_v1")).toBeNull();
  });

  it("既有 critical modal 或播放中的 audio 會 defer，且不搶焦點", () => {
    coordinator._state.started = true;
    const modal = document.createElement("div");
    modal.setAttribute("role", "dialog");
    document.body.appendChild(modal);
    expect(coordinator._show(item())).toBe(false);
    modal.remove();

    const audio = document.createElement("audio");
    Object.defineProperty(audio, "paused", { configurable: true, value: false });
    Object.defineProperty(audio, "ended", { configurable: true, value: false });
    document.body.appendChild(audio);
    expect(coordinator._show(item({ id: 2 }))).toBe(false);
    expect(document.querySelector(".announcement-modal-overlay")).toBeNull();
  });

  it("storage 被拒絕時仍可完成關閉，不阻塞 entry", () => {
    coordinator._state.started = true;
    const storageSpy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage disabled"); });
    expect(() => {
      coordinator._show(item());
      document.querySelector(".announcement-modal-close").click();
    }).not.toThrow();
    expect(document.querySelector(".announcement-modal-overlay")).toBeNull();
    storageSpy.mockRestore();
  });

  it("登入或登出切換會丟棄舊 principal 的候選 popup", () => {
    coordinator._state.started = true;
    coordinator._state.queue = [item()];
    coordinator._show(item());
    document.dispatchEvent(new CustomEvent("storylingo:auth-changed"));
    expect(document.querySelector(".announcement-modal-overlay")).toBeNull();
    expect(coordinator._state.queue).toEqual([]);
  });

  it("entry 最多呈現三則並遵守 server active response", async () => {
    const request = vi.fn(async (path) => path === "/api/auth/me" ? { authed: false } : { items: [item(), item({ id: 2 }), item({ id: 3 }), item({ id: 4 })] });
    window.NovelApi = { request };
    coordinator.start();
    await new Promise((resolve) => setTimeout(resolve, 10));
    for (let i = 0; i < 3; i += 1) {
      document.querySelector(".announcement-modal-close")?.click();
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    expect(coordinator._state.shown).toBe(3);
    expect(document.querySelector(".announcement-modal-overlay")).toBeNull();
    expect(request).toHaveBeenCalledWith("/api/announcements/active");
  });
});
