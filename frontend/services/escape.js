"use strict";
/* 共用 HTML 跳脫（L0-5）：
 * 統一身分驗證與全域一致（含單引號），供 app.js / platform.js 委派，
 * 亦可被 vitest 直接測試。掛在 window.NovelEscape。
 */
(function () {
  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  window.NovelEscape = { esc };
})();