import { describe, it, expect, afterEach } from "vitest";
import "../../services/toast.js";

afterEach(() => {
  document.querySelectorAll(".toast-container").forEach((node) => node.remove());
});

describe("NovelToast（L1-1 共用 toast）", () => {
  const { toast } = window.NovelToast;

  it("建立 .toast-container 並顯示訊息", () => {
    toast("測試訊息", "success");
    const container = document.querySelector(".toast-container");
    expect(container).toBeTruthy();
    const el = container.querySelector(".toast");
    expect(el.textContent).toBe("測試訊息");
    expect(el.classList.contains("toast-success")).toBe(true);
  });

  it("type 對應 css class（error → alert role）", () => {
    toast("出錯了", "error");
    const el = document.querySelectorAll(".toast-container .toast")[0];
    expect(el.classList.contains("toast-error")).toBe(true);
    expect(el.getAttribute("role")).toBe("alert");
  });

  it("dismissAll 清空全部", () => {
    toast("a");
    toast("b");
    window.NovelToast.toast.dismissAll();
    expect(document.querySelectorAll(".toast-container .toast").length).toBe(0);
  });

  it("點擊可關閉", () => {
    const dismiss = toast("可關閉");
    const el = document.querySelector(".toast-container .toast");
    el.click();
    expect(document.querySelectorAll(".toast-container .toast").length).toBe(0);
  });
});