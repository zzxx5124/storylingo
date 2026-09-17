"use strict";

function updateSeo() {
  const hash = (location.hash || "#/home").replace(/^#\/?/, "");
  const parts = hash.split(/[/?]/);
  const route = parts[0];
  const title = route === "read" ? "正在閱讀 · 小說朗讀" : route === "book" ? "小說詳情 · 小說朗讀" : route === "search" ? "搜尋小說 · 小說朗讀" : route === "rankings" ? "小說排行榜 · 小說朗讀" : route === "shelf" ? "我的書架 · 小說朗讀" : "免費小說閱讀與 AI 有聲朗讀平台";
  document.title = title;
  const description = document.querySelector('meta[name="description"]');
  if (description) description.setAttribute("content", route === "read" ? "閱讀公開小說章節，調整適合你的閱讀設定。" : "免費閱讀公開小說，使用 AI 聲音繼續你的故事。");
  const canonical = document.querySelector('link[rel="canonical"]') || document.head.appendChild(Object.assign(document.createElement("link"), { rel: "canonical" }));
  canonical.href = `${location.origin}${location.pathname}`;
}

window.addEventListener("hashchange", updateSeo);
window.addEventListener("DOMContentLoaded", () => {
  updateSeo();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js?v=27", { updateViaCache: "none" })
      .then((registration) => registration.update())
      .catch(() => {});
  }
});
