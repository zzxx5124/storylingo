"use strict";

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

export function filterBar({ content = "", actions = "", label = "篩選條件", className = "" } = {}) {
  return `<section class="foundation-filter-bar ui-filter-bar ${esc(className)}" aria-label="${esc(label)}"><div class="foundation-filter-controls">${content}</div>${actions ? `<div class="foundation-filter-actions">${actions}</div>` : ""}</section>`;
}

export default filterBar;
