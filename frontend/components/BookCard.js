"use strict";
/* 共用 書籍卡片 元件（FE-006）。
 * bookCard(book) => 結合 cover / title / author / score / tags / lastChapterTitle
 * book: { bookId, title, author, score, cover, intro, tags[], lastChapterTitle }
 */
export function bookCard(book = {}) {
  const fallbackCover = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="80" height="108"><rect width="100%" height="100%" fill="#ece7df"/><text x="40" y="56" font-size="22" text-anchor="middle" fill="#8a8378">書</text></svg>`
  );
  const cover = book.cover || fallbackCover;
  const score = typeof book.score === "number" && book.score > 0
    ? `<span class="book-score">${book.score.toFixed(1)}</span>` : "";
  const tags = (book.tags || []).slice(0, 3).map((t) => `<span class="book-tag">${esc(t)}</span>`).join("");
  const lastChapter = book.lastChapterTitle
    ? `<p class="book-last-chapter">最新：${esc(book.lastChapterTitle)}</p>` : "";

  return `
    <a class="book-card" href="/book/${encodeURIComponent(book.bookId)}" data-book-id="${esc(book.bookId)}">
      <img class="book-cover" src="${escapeAttr(cover)}" data-fallback="${escapeAttr(fallbackCover)}" onerror="this.onerror=null;this.src=this.dataset.fallback" alt="${esc(book.title || book.bookId)} 封面" loading="lazy">
      <div class="book-info">
        <h3 class="book-title">${esc(book.title || book.bookId)}</h3>
        <p class="book-author">${esc(book.author || "")}</p>
        ${score}
        <p class="book-intro">${esc((book.intro || "").slice(0, 60))}</p>
        ${lastChapter}
        <div class="book-tags">${tags}</div>
      </div>
    </a>`;
}

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}
function escapeAttr(value) {
  return String(value == null ? "" : value).replace(/"/g, "&quot;");
}

export default bookCard;
