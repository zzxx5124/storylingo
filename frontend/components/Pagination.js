"use strict";

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

export function pagination({ page = 1, totalPages = 0, total = 0, hasPrev = page > 1, hasNext = page < totalPages, hrefForPage = (value) => `#page-${value}`, label = "分頁" } = {}) {
  const currentPage = Math.max(1, Number(page) || 1);
  const pages = Math.max(0, Number(totalPages) || 0);
  const link = (target, text, disabled) => disabled
    ? `<span class="foundation-pagination-button is-disabled" aria-disabled="true">${esc(text)}</span>`
    : `<a class="foundation-pagination-button" href="${esc(hrefForPage(target))}">${esc(text)}</a>`;
  if (pages <= 1 && !hasPrev && !hasNext) return "";
  return `<nav class="foundation-pagination ui-pagination" aria-label="${esc(label)}"><span class="foundation-pagination-status" aria-live="polite">第 ${currentPage} / ${pages || 0} 頁 · 共 ${Math.max(0, Number(total) || 0)} 項</span><div class="foundation-pagination-actions">${link(currentPage - 1, "上一頁", !hasPrev)}<span class="foundation-pagination-current" aria-current="page">第 ${currentPage} 頁</span>${link(currentPage + 1, "下一頁", !hasNext)}</div></nav>`;
}

export default pagination;
