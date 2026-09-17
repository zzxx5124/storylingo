import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { toast } from "../Toast.js";

describe("Toast 元件", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("建立 toast container 並顯示訊息", () => {
    toast("儲存成功", { type: "success" });
    const container = document.getElementById("toast-container");
    expect(container).not.toBeNull();
    expect(document.querySelector(".toast-success .toast-message").textContent).toBe("儲存成功");
  });

  it("error type 使用 role=alert，其餘使用 status", () => {
    toast("失敗", { type: "error" });
    toast("資訊", { type: "info" });
    expect(document.querySelector(".toast-error")).toHaveAttribute("role", "alert");
    expect(document.querySelector(".toast-info")).toHaveAttribute("role", "status");
  });

  it("手動關閉按鈕可移除 toast", () => {
    vi.useFakeTimers();
    const dismiss = toast("可關閉", { duration: 0 });
    expect(document.querySelectorAll(".toast").length).toBe(1);
    document.querySelector(".toast-close").click();
    vi.advanceTimersByTime(300);
    expect(document.querySelectorAll(".toast").length).toBe(0);
    expect(dismiss).toBeTypeOf("function");
    vi.useRealTimers();
  });

  it("duration 到期後自動消失", () => {
    toast("自動消失", { type: "success", duration: 3000 });
    expect(document.querySelectorAll(".toast").length).toBe(1);
    vi.advanceTimersByTime(3000);
    vi.advanceTimersByTime(300); // 加上消失動畫
    expect(document.querySelectorAll(".toast").length).toBe(0);
  });

  it("success/error/info/warning 捷徑存在", () => {
    toast.success("s");
    toast.error("e");
    toast.info("i");
    toast.warning("w");
    expect(document.querySelectorAll(".toast-success").length).toBe(1);
    expect(document.querySelectorAll(".toast-error").length).toBe(1);
    expect(document.querySelectorAll(".toast-info").length).toBe(1);
    expect(document.querySelectorAll(".toast-warning").length).toBe(1);
  });

  it("dismissAll 清空所有 toast", () => {
    toast("a"); toast("b");
    toast.dismissAll();
    expect(document.querySelectorAll(".toast").length).toBe(0);
  });

  it("訊息內容跳脫避免 XSS", () => {
    toast("<img src=x onerror=alert(1)>");
    expect(document.body.querySelector("img")).toBeNull();
  });
});