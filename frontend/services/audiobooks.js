"use strict";

/* 公開有聲書查詢的純資料規則；頁面與測試共用，避免 URL 狀態各自解讀。 */
(() => {
  const SORTS = ["newest", "updated", "title"];
  const LANGUAGES = ["zh", "vocab", "bilingual", "en", "other"];

  function positivePage(value) {
    const page = Number(value);
    return Number.isFinite(page) && page >= 1 ? Math.floor(page) : 1;
  }

  function normalizeQuery(search = "") {
    const params = search instanceof URLSearchParams ? search : new URLSearchParams(String(search).replace(/^\?/, ""));
    const sort = SORTS.includes(params.get("sort")) ? params.get("sort") : "newest";
    const language = LANGUAGES.includes(params.get("language")) ? params.get("language") : "";
    const categoryId = (params.get("category_id") || "").trim().slice(0, 40);
    return {
      q: (params.get("q") || "").trim().slice(0, 80),
      category_id: categoryId,
      language,
      sort,
      page: positivePage(params.get("page")),
    };
  }

  function toQuery(query = {}) {
    const normalized = normalizeQuery(new URLSearchParams(query));
    const params = new URLSearchParams();
    if (normalized.q) params.set("q", normalized.q);
    if (normalized.category_id) params.set("category_id", normalized.category_id);
    if (normalized.language) params.set("language", normalized.language);
    if (normalized.sort !== "newest") params.set("sort", normalized.sort);
    if (normalized.page > 1) params.set("page", String(normalized.page));
    return params.toString();
  }

  function availabilityLabel(item = {}) {
    return item.availability === "partial" ? "部分章節可聆聽" : item.availability === "available" ? "可聆聽" : "目前不可聆聽";
  }

  function safeListenRoute(item = {}) {
    const route = typeof item.listenRoute === "string" ? item.listenRoute : "";
    const match = /^#\/read\/([^\/?#\\]+)\/(\d+)\?mode=listen$/.exec(route);
    if (!match || /[\u0000-\u001f\u007f]/.test(route)) return null;
    let bid;
    try { bid = decodeURIComponent(match[1]); } catch (_) { return null; }
    if (!bid || bid === "." || bid === ".." || bid.includes("/") || bid.includes("\\")) return null;
    return route;
  }

  window.StoryLingoAudiobooks = {
    SORTS,
    LANGUAGES,
    normalizeQuery,
    toQuery,
    availabilityLabel,
    safeListenRoute,
  };
})();
