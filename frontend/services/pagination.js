"use strict";

/* Shared view-only pagination primitives.  Domain loaders own their query
 * parameters; this helper only normalizes the envelope and renders controls. */
(() => {
  const DEFAULT_PAGE_SIZE = 20;

  function normalize(payload) {
    if (Array.isArray(payload)) {
      return {
        items: payload,
        page: 1,
        page_size: payload.length || DEFAULT_PAGE_SIZE,
        total: payload.length,
        total_pages: payload.length ? 1 : 0,
        has_next: false,
        has_prev: false,
        legacy: true,
      };
    }
    const value = payload || {};
    const items = Array.isArray(value.items) ? value.items : [];
    const page = Math.max(1, Number(value.page ?? 1) || 1);
    const pageSize = Math.max(1, Number(value.page_size ?? value.pageSize ?? DEFAULT_PAGE_SIZE) || DEFAULT_PAGE_SIZE);
    const total = Math.max(0, Number(value.total ?? items.length) || 0);
    const totalPages = Math.max(0, Number(value.total_pages ?? value.totalPages ?? (total ? Math.ceil(total / pageSize) : 0)) || 0);
    return {
      items,
      page,
      page_size: pageSize,
      total,
      total_pages: totalPages,
      has_next: value.has_next ?? value.hasNext ?? page < totalPages,
      has_prev: value.has_prev ?? value.hasPrev ?? page > 1,
      legacy: false,
    };
  }

  function render(payload, { label = "作品分頁", hrefForPage = () => "#" } = {}) {
    const meta = normalize(payload);
    if (meta.total_pages <= 1 && !meta.has_prev && !meta.has_next) return "";
    const link = (page, text, disabled, current = false) => disabled
      ? `<span class="platform-pagination-button is-disabled" aria-disabled="true">${text}</span>`
      : `<a class="platform-button platform-button-small platform-pagination-button" href="${hrefForPage(page)}"${current ? ' aria-current="page"' : ""}>${text}</a>`;
    return `<nav class="platform-pagination" aria-label="${label}"><span class="platform-pagination-status" aria-live="polite">第 ${meta.page} / ${meta.total_pages || 0} 頁 · 共 ${meta.total} 項</span><div class="platform-pagination-actions">${link(meta.page - 1, "上一頁", !meta.has_prev)}<span class="platform-pagination-current" aria-current="page">第 ${meta.page} 頁</span>${link(meta.page + 1, "下一頁", !meta.has_next)}</div></nav>`;
  }

  window.StoryLingoPagination = { normalize, render };
})();
