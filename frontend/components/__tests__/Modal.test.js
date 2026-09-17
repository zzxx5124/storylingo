import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { modal, closeModal } from "../Modal.js";

function fireKey(key) {
  document.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
}

describe("Modal 元件", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });
  afterEach(() => {
    closeModal();
    document.body.classList.remove("modal-open");
  });

  it("建立 overlay / dialog 並顯示標題與內容", () => {
    modal({ title: "確認刪除", content: "<p>確定？</p>" });
    expect(document.querySelector(".modal-overlay")).not.toBeNull();
    expect(document.querySelector(".modal-title").textContent).toBe("確認刪除");
    expect(document.querySelector(".modal-body p").textContent).toBe("確定？");
  });

  it("加入 role=dialog 與 aria-modal", () => {
    modal({ title: "x" });
    const overlay = document.querySelector(".modal-overlay");
    expect(overlay).toHaveAttribute("role", "dialog");
    expect(overlay).toHaveAttribute("aria-modal", "true");
    expect(overlay.getAttribute("aria-labelledby")).toBe(document.querySelector(".modal-title").id);
  });

  it("body 加上 modal-open 防止滾動", () => {
    modal({ title: "x" });
    expect(document.body.classList.contains("modal-open")).toBe(true);
  });

  it("點擊關閉按鈕後移除 overlay", () => {
    vi.useFakeTimers();
    modal({ title: "x" });
    document.querySelector(".modal-close").click();
    vi.advanceTimersByTime(200);
    expect(document.querySelector(".modal-overlay")).toBeNull();
    expect(document.body.classList.contains("modal-open")).toBe(false);
    vi.useRealTimers();
  });

  it("ESC 鍵關閉 modal", () => {
    vi.useFakeTimers();
    modal({ title: "x" });
    fireKey("Escape");
    vi.advanceTimersByTime(200);
    expect(document.querySelector(".modal-overlay")).toBeNull();
    vi.useRealTimers();
  });

  it("render footer 並在 onClose 觸發", () => {
    const onClose = vi.fn();
    vi.useFakeTimers();
    modal({ title: "x", footer: "<button class='ok'>好</button>", onClose });
    expect(document.querySelector(".modal-footer .ok")).not.toBeNull();
    document.querySelector(".modal-close").click();
    vi.advanceTimersByTime(200);
    expect(onClose).toHaveBeenCalled();
    vi.useRealTimers();
  });

  it("自動聚焦第一個可聚焦元素", () => {
    modal({ title: "x", content: "<button class='first'>first</button><button>second</button>" });
    vi.useFakeTimers();
    vi.advanceTimersByTime(20);
    expect(document.activeElement).not.toBeNull();
    vi.useRealTimers();
  });

  it("關閉後恢復 opener focus 且重複 close 不重複 callback", () => {
    vi.useFakeTimers();
    const opener = document.createElement("button");
    opener.textContent = "開啟";
    document.body.appendChild(opener);
    opener.focus();
    const onClose = vi.fn();
    const instance = modal({ title: "x", onClose });
    instance.close();
    instance.close();
    vi.advanceTimersByTime(200);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(opener);
    vi.useRealTimers();
  });

  it("Tab 在 dialog 內循環", () => {
    modal({ title: "x", content: "<button class='first'>第一個</button><button class='last'>最後</button>" });
    const overlay = document.querySelector(".modal-overlay");
    const last = overlay.querySelector(".last");
    last.focus();
    const event = new KeyboardEvent("keydown", { key: "Tab", bubbles: true });
    document.dispatchEvent(event);
    expect(document.activeElement).toBe(overlay.querySelector(".modal-close"));
  });

  it("size 對應 modal-large / modal-small class", () => {
    modal({ title: "a", size: "large" });
    expect(document.querySelector(".modal-large")).not.toBeNull();
    closeModal();
    modal({ title: "b", size: "small" });
    expect(document.querySelector(".modal-small")).not.toBeNull();
  });

  it("標題跳脫避免 XSS", () => {
    modal({ title: "<img src=x onerror=alert(1)>" });
    expect(document.querySelector(".modal-title img")).toBeNull();
  });
});
