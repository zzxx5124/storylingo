import { describe, it, expect } from "vitest";
import "../../services/escape.js";

describe("NovelEscape.esc（L0-5 共用跳脫）", () => {
  const { esc } = window.NovelEscape;

  it("跳脫 HTML 保留字與引號（含單引號）", () => {
    expect(esc(`<script>alert("x")</script>'`)).toBe(
      "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;&#039;",
    );
  });

  it("null/undefined → 空字串，數字保留", () => {
    expect(esc(null)).toBe("");
    expect(esc(undefined)).toBe("");
    expect(esc(0)).toBe("0");
  });

  it("封面 URL 惡意注入被中和（L0-5）", () => {
    const evil = `x" onerror="alert(1)` + `' onerror='alert(2)`;
    const out = esc(evil);
    expect(out).not.toContain('"');
    expect(out).not.toContain("'");
    expect(out).not.toContain("<");
  });
});