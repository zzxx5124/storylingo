import { describe, it, expect, vi } from "vitest";
import { getByRole } from "@testing-library/dom";
import { button } from "../Button.js";

describe("Button 元件", () => {
  it("渲染主要按鈕與文字", () => {
    document.body.innerHTML = button({ label: "儲存", variant: "primary" });
    const el = getByRole(document.body, "button", { name: "儲存" });
    expect(el).toHaveClass("btn");
    expect(el).toHaveClass("btn-primary");
  });

  it("danger / ghost / icon variant 對應正確 class", () => {
    document.body.innerHTML =
      button({ label: "刪除", variant: "danger" }) +
      button({ label: "", variant: "ghost", className: "btn-icon" }) +
      button({ label: "✕", variant: "secondary" });
    expect(document.querySelector(".btn-danger")).not.toBeNull();
    expect(document.querySelector(".btn-ghost.btn-icon")).not.toBeNull();
    expect(document.querySelectorAll(".btn").length).toBe(3);
  });

  it("disabled 屬性正確套用", () => {
    document.body.innerHTML = button({ label: "停用", disabled: true });
    expect(document.querySelector("button")).toBeDisabled();
  });

  it("loading 狀態會禁用按鈕並顯示 spinner", () => {
    document.body.innerHTML = button({ label: "送出中", loading: true });
    const el = document.querySelector("button");
    expect(el).toBeDisabled();
    expect(el).toHaveAttribute("aria-busy", "true");
    expect(el.querySelector(".spinner")).not.toBeNull();
  });

  it("預設 type 為 button（避免在外層表單誤觸發 submit）", () => {
    document.body.innerHTML = button({ label: "ok" });
    expect(document.querySelector("button")).toHaveAttribute("type", "button");
  });

  it("title 與自訂 attrs 會寫入 DOM", () => {
    document.body.innerHTML = button({ label: "x", title: "提示", attrs: { "data-testid": "foo" } });
    const el = document.querySelector("button");
    expect(el).toHaveAttribute("title", "提示");
    expect(el).toHaveAttribute("data-testid", "foo");
  });

  it("內容會做 HTML 跳脫，避免 XSS", () => {
    document.body.innerHTML = button({ label: "<script>alert(1)</script>" });
    expect(document.body.querySelector("script")).toBeNull();
    expect(document.body.textContent).toContain("<script>alert(1)</script>");
  });
});
