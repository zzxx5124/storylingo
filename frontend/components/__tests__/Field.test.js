import { describe, it, expect } from "vitest";
import { getByLabelText } from "@testing-library/dom";
import { input, select, textarea } from "../Field.js";

describe("Field 表單元件", () => {
  it("input 渲染 label / wrapper 並支援前面 label", () => {
    document.body.innerHTML = input({ id: "title", label: "書名", required: true });
    const field = getByLabelText(document.body, /書名/);
    expect(field).toHaveAttribute("id", "title");
    expect(field).toHaveAttribute("required");
    expect(document.querySelector(".required-mark")).not.toBeNull();
  });

  it("help 文字與 aria-describedby 正確對應", () => {
    document.body.innerHTML = input({ id: "intro", label: "簡介", help: "最多 100 字" });
    const field = getByLabelText(document.body, "簡介");
    expect(field).toHaveAttribute("aria-describedby", "intro-help");
    expect(document.querySelector("#intro-help").textContent).toContain("最多 100 字");
  });

  it("error 會進入 field-invalid 狀態並顯示 aria-invalid", () => {
    document.body.innerHTML = input({ id: "cat", label: "分類", error: "此欄位無效" });
    const field = getByLabelText(document.body, "分類");
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(field).toHaveAttribute("aria-describedby", "cat-error");
    expect(document.querySelector(".field-invalid")).not.toBeNull();
    expect(document.querySelector("#cat-error")).toHaveTextContent("此欄位無效");
  });

  it("help 與 error 同時存在時保留兩個 described-by references", () => {
    document.body.innerHTML = input({ id: "both", label: "標題", help: "最多 100 字", error: "格式不正確" });
    expect(getByLabelText(document.body, "標題")).toHaveAttribute("aria-describedby", "both-help both-error");
  });

  it("input 的 value、placeholder、name 與 autocomplete 正確寫入", () => {
    document.body.innerHTML = input({ id: "q", name: "query", value: "abc", placeholder: "搜尋", autocomplete: "off" });
    const field = document.querySelector("input");
    expect(field).toHaveAttribute("name", "query");
    expect(field).toHaveAttribute("value", "abc");
    expect(field).toHaveAttribute("placeholder", "搜尋");
    expect(field).toHaveAttribute("autocomplete", "off");
  });

  it("select 渲染 options 並預選指定 value", () => {
    document.body.innerHTML = select({
      id: "genre", label: "分類", value: "scifi",
      options: [{ value: "fantasy", label: "奇幻" }, { value: "scifi", label: "科幻" }],
    });
    const el = getByLabelText(document.body, "分類");
    expect(el.tagName).toBe("SELECT");
    expect(el.value).toBe("scifi");
    expect(el.options.length).toBe(2);
  });

  it("select placeholder 產生空選項", () => {
    document.body.innerHTML = select({
      id: "g", options: [{ value: "a", label: "A" }], placeholder: "請選擇",
    });
    const el = document.getElementById("g");
    expect(el.options[0].value).toBe("");
    expect(el.options[0].textContent).toBe("請選擇");
  });

  it("textarea 渲染 rows、內容與 maxlength", () => {
    document.body.innerHTML = textarea({ id: "body", label: "正文", value: "從前有座山", rows: 6, maxlength: 500 });
    const el = getByLabelText(document.body, "正文");
    expect(el.tagName).toBe("TEXTAREA");
    expect(el).toHaveAttribute("rows", "6");
    expect(el).toHaveValue("從前有座山");
    expect(el).toHaveAttribute("maxlength", "500");
  });

  it("disabled state 正確套用", () => {
    document.body.innerHTML = input({ id: "lock", label: "鎖定", disabled: true });
    expect(getByLabelText(document.body, "鎖定")).toBeDisabled();
  });

  it("內容跳脫避免 XSS", () => {
    document.body.innerHTML = textarea({ id: "t", label: `<img src=x onerror="alert(1)">` });
    expect(document.body.querySelector("img")).toBeNull();
  });
});
