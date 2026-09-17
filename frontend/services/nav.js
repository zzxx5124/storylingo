"use strict";
/* V4 Navigation / IA（Phase 12）：
 * - 收斂至 Reading / Creation / Admin 三個 workspace。
 * - 每個角色只有一個主要 workspace 入口（作者不再有重複入口）。
 * - 依角色決定可見導覽項。
 * 掛在 window.NovelNav。
 */
(function () {
  const WORKSPACES = ["reading", "creation", "admin"];

  // 每個角色可見的 workspace 入口（作者僅一個 creation 入口，去重複）
  function roleWorkspaces(role) {
    if (role === "admin") return ["reading", "creation", "admin"];
    if (role === "author") return ["reading", "creation"];
    return ["reading"];
  }

  // 每個 workspace 對應的 nav item
  const NAV_ITEMS = {
    reading: { id: "nav-shelf", label: "書架", href: "#/shelf" },
    creation: { id: "nav-mine", label: "我的作品", href: "#/mine" },
    admin: { id: "nav-admin", label: "後台", href: "#/admin" },
  };

  function visibleNav(role) {
    const ws = roleWorkspaces(role);
    return ws.map((w) => NAV_ITEMS[w]);
  }

  function isAuthorEntry(href) {
    // 唯一作者 workspace 入口為 #/mine（Creation），#/bookshelf 為 legacy 重複入口
    return href === "#/mine";
  }

  window.NovelNav = { WORKSPACES, roleWorkspaces, visibleNav, isAuthorEntry, NAV_ITEMS };
})();
