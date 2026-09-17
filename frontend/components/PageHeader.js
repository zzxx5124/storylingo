"use strict";

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

export function pageHeader({ title = "頁面", description = "", eyebrow = "", action = "", level = 1, className = "" } = {}) {
  const headingLevel = Math.min(6, Math.max(1, Number(level) || 1));
  return `<header class="foundation-page-header ui-page-header ${esc(className)}"><div class="foundation-page-header-copy">${eyebrow ? `<p class="foundation-page-header-eyebrow">${esc(eyebrow)}</p>` : ""}<h${headingLevel} class="foundation-page-header-title">${esc(title)}</h${headingLevel}>${description ? `<p class="foundation-page-header-description">${esc(description)}</p>` : ""}</div>${action ? `<div class="foundation-page-header-action">${action}</div>` : ""}</header>`;
}

export default pageHeader;
