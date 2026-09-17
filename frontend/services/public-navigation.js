"use strict";

/* 公開 shell 導覽：只管理 disclosure、目前位置與 keyboard 行為。
 * 角色權限仍由 app.js + backend canonical policy 決定；本檔不授權任何操作。 */
(() => {
  const publicRoutes = new Set(["home", "search", "category", "rankings", "audiobooks", "book", "read", "shelf", "author"]);
  let bound = false;
  const mobile = () => window.matchMedia?.("(max-width: 760px)").matches || false;

  function layout() {
    const workspace = document.querySelector("#nav-workspace-group");
    const flat = document.querySelector("#mobile-workspace");
    const menu = document.querySelector(".nav-workspace-menu");
    if (workspace && flat && menu) {
      (mobile() ? flat : workspace).append(menu);
      flat.hidden = !mobile() || workspace.hidden;
    }
    const account = document.querySelector("#mobile-account");
    const auth = document.querySelector("#auth-control");
    const user = document.querySelector("#auth-user");
    const login = document.querySelector("#btn-login");
    const profile = document.querySelector("#btn-profile");
    const loginModal = document.querySelector("#login-modal");
    // Reader 隱藏全域頁首，但書籤仍可觸發登入；保留同一個 dialog 與事件。
    if (auth && loginModal) {
      (mobile() && routeParts()[0] === "read" ? document.body : auth).append(loginModal);
    }
    if (account && auth && user && login && profile) {
      const inside = mobile() && !user.hidden;
      account.hidden = !inside;
      if (inside) account.append(user, login);
      else {
        auth.insertBefore(user, profile);
        auth.insertBefore(login, loginModal?.parentElement === auth ? loginModal : null);
      }
    }
    const shortcut = document.querySelector("#mobile-nav-mine");
    if (shortcut) shortcut.hidden = document.querySelector("#nav-mine")?.hidden !== false;
  }

  function routeParts() {
    const raw = (location.hash || "#/home").replace(/^#\/?/, "");
    return raw.split("?")[0].split("/").filter(Boolean);
  }

  function close() {
    const toggle = document.querySelector("#nav-toggle");
    const nav = document.querySelector("#main-nav");
    if (!toggle || !nav) return;
    nav.classList.remove("nav-open");
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-label", "開啟導覽選單");
  }

  function sync() {
    const [route] = routeParts();
    layout();
    document.body.dataset.navigationRoute = route || "home";
    document.querySelectorAll("[data-public-nav]").forEach((link) => {
      const key = link.dataset.publicNav;
      const active = key === "home" ? route === "home" : key === "search" ? route === "search" || route === "category" : key === "mine" ? ["mine", "detail"].includes(route) : key === route;
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    const nav = document.querySelector("#main-nav");
    if (nav) nav.dataset.context = publicRoutes.has(route) ? "public" : "workspace";
    const workspace = document.querySelector("#nav-workspace-group");
    if (workspace && !publicRoutes.has(route)) workspace.removeAttribute("open");
    const labels = { home: "首頁", search: "搜尋作品", category: "作品分類", book: "作品詳情", shelf: "我的書架", mine: "作者工作區 · 我的作品", detail: "作者工作區 · 章節管理", settings: "個人設定", requests: "申請紀錄", admin: "管理工作區", notifications: "通知", rankings: "排行榜", audiobooks: "有聲書", author: "作者介紹", policy: "網站資訊", privacy: "隱私政策" };
    const locationLabel = document.querySelector("#mobile-location");
    if (locationLabel) locationLabel.textContent = labels[route] || "工作區";
    const parent = document.querySelector("#mobile-parent");
    if (parent) {
      const authorParent = route === "detail" || route === "requests" && document.querySelector("#nav-mine")?.hidden === false;
      parent.href = authorParent ? "#/mine" : "#/home";
      parent.textContent = authorParent ? "返回我的作品" : "回到首頁";
      parent.hidden = route === "home";
    }
    document.querySelectorAll("#nav-mine, #nav-requests, #nav-admin").forEach((link) => {
      const active = link.id === "nav-mine" ? ["mine", "detail"].includes(route) : link.hash === `#/${route}`;
      if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    });
  }

  function bind() {
    if (bound) return;
    bound = true;
    const toggle = document.querySelector("#nav-toggle");
    const nav = document.querySelector("#main-nav");
    toggle?.addEventListener("click", () => {
      const open = !nav.classList.contains("nav-open");
      nav.classList.toggle("nav-open", open);
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "關閉導覽選單" : "開啟導覽選單");
      if (open) (nav.querySelector("a:not([hidden])") || nav).focus?.();
    });
    nav?.addEventListener("click", (event) => {
      const link = event.target.closest("a");
      // Notification popover is rendered inside the authenticated workspace
      // group. Keep that group visible while the popover is open; otherwise
      // closing the mobile nav would hide the popover's ancestor as well.
      if (link && !link.classList.contains("notification-nav")) close();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        const wasOpen = nav?.classList.contains("nav-open");
        close();
        document.querySelector("#nav-workspace-group")?.removeAttribute("open");
        if (wasOpen) toggle?.focus();
      }
    });
    document.addEventListener("click", (event) => {
      if (!event.target.closest("#topbar")) close();
    });
    window.addEventListener("hashchange", () => { close(); sync(); });
    window.matchMedia?.("(max-width: 760px)").addEventListener?.("change", () => { close(); sync(); });
    sync();
  }

  window.StoryLingoPublicNav = { close, sync };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind, { once: true });
  else bind();
})();
