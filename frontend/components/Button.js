"use strict";
/* 共用 Button 元件（FE-006）。
 * 回傳 HTML 字串；亦可經由 NovelUI.Button 取得。
 * variants: primary / secondary / ghost / danger / icon
 * states: loading / disabled
 */
export function button({ label = "", variant = "secondary", className = "", loading = false, disabled = false, type = "button", title = "", onClick, attrs = {} } = {}) {
  const classes = ["btn", `btn-${variant}`, "ui-button", className].filter(Boolean).join(" ");
  const baseAttrs = {
    type,
    class: classes,
    disabled: disabled || loading ? "disabled" : null,
    "aria-busy": loading ? "true" : null,
  };
  if (title) baseAttrs.title = title;
  for (const [key, value] of Object.entries(attrs)) baseAttrs[key] = value;
  const attrString = Object.entries(baseAttrs)
    .filter(([, v]) => v !== null && v !== undefined && v !== false)
    .map(([k, v]) => ` ${k}="${String(v).replace(/"/g, "&quot;")}"`)
    .join("");
  const content = loading ? `<span class="spinner" aria-hidden="true"></span> ${esc(label)}` : esc(label);
  return `<button${attrString}>${content}</button>`;
}

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

export default button;
