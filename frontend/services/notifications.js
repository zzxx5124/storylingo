"use strict";

/* Shared shell coordinator for the private notification inbox.  It owns the
 * badge/recent popover state; platform.js only renders the full paginated
 * center.  Announcement state deliberately remains in its own coordinator. */
(function installNotifications(global) {
  const state = {
    started: false, principalId: null, generation: 0, unread: null,
    recent: [], loading: false, error: null, popover: null,
  };

  function safeRoute(value) {
    return typeof value === "string" && value.startsWith("#/") && value.length <= 500
      && !value.includes("//") && !value.includes("\\") && !/[\u0000-\u001f]/.test(value);
  }
  function request(path, options) {
    return global.NovelApi.request(path, options);
  }
  function badge() {
    const node = document.querySelector("#notification-badge");
    if (!node) return;
    const count = state.unread;
    node.hidden = state.principalId == null || count == null || count <= 0;
    node.textContent = count > 99 ? "99+" : String(count || 0);
    node.setAttribute("aria-label", count == null ? "未讀通知數量目前無法載入" : `未讀通知 ${count} 則`);
  }
  function closePopover() {
    if (state.popover) state.popover.remove();
    state.popover = null;
  }
  function captureNavigation() {
    return { hash: global.location.hash, generation: state.generation };
  }
  function navigateToTarget(route, snapshot = captureNavigation()) {
    if (!safeRoute(route) || !snapshot
      || snapshot.hash !== global.location.hash
      || snapshot.generation !== state.generation) return false;
    global.location.hash = route.slice(1);
    return true;
  }
  function text(parent, tag, value, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value == null ? "" : String(value);
    parent.appendChild(node);
    return node;
  }
  function displayError(parent) {
    text(parent, "p", "通知目前無法載入，請稍後再試。", "notification-popover-error");
  }
  function renderPopover(items) {
    const panel = state.popover;
    if (!panel) return;
    panel.replaceChildren();
    const heading = text(panel, "h2", "最近通知", "notification-popover-title");
    heading.id = "notification-popover-title";
    panel.setAttribute("aria-labelledby", heading.id);
    if (!items.length) {
      text(panel, "p", "目前沒有通知。", "notification-popover-empty");
      const link = text(panel, "a", "查看通知中心", "notification-popover-all");
      link.href = "#/notifications";
      return;
    }
    const list = document.createElement("ul");
    list.className = "notification-popover-list";
    items.slice(0, 8).forEach((item) => {
      const li = document.createElement("li");
      li.className = item.readAt || item.read_at ? "read" : "unread";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "notification-popover-item";
      button.setAttribute("aria-label", `${item.title || "通知"}${li.classList.contains("unread") ? "，未讀" : ""}`);
      text(button, "strong", item.title || "通知");
      if (item.body) text(button, "span", item.body);
      text(button, "small", item.createdAt || item.created_at || "");
      button.addEventListener("click", async () => {
        button.disabled = true;
        const navigation = captureNavigation();
        try {
          if (!(item.readAt || item.read_at)) await markRead(item.id, true);
          closePopover();
          const route = item.targetRoute || item.link || "";
          navigateToTarget(route, navigation);
        } catch (_) {
          button.disabled = false;
          text(panel, "p", "此通知目前無法開啟，請稍後再試。", "notification-popover-error");
        }
      });
      li.appendChild(button); list.appendChild(li);
    });
    panel.appendChild(list);
    const link = text(panel, "a", "查看全部通知", "notification-popover-all");
    link.href = "#/notifications";
  }
  async function ensurePrincipal(expectedGeneration) {
    const me = await request("/api/auth/me");
    if (expectedGeneration !== state.generation) return null;
    const id = me?.authed && me.user ? me.user.id : null;
    if (state.principalId !== null && state.principalId !== id) {
      state.generation += 1;
      state.recent = []; state.unread = null; state.error = null;
      closePopover(); badge();
    }
    state.principalId = id;
    badge();
    return id;
  }
  async function refresh(options = {}) {
    const withRecent = !!options.recent;
    let generation = state.generation;
    state.loading = true; state.error = null;
    try {
      const principal = await ensurePrincipal(generation);
      generation = state.generation;
      if (principal == null) { state.unread = null; state.recent = []; return null; }
      const count = await request("/api/notifications/unread-count");
      if (generation !== state.generation) return null;
      state.unread = Number.isFinite(Number(count?.count)) ? Number(count.count) : null;
      badge();
      if (withRecent) {
        const recent = await request("/api/notifications?page=1&page_size=8&filter=all");
        if (generation !== state.generation) return null;
        state.recent = recent?.items || [];
      }
      return { unread: state.unread, recent: state.recent };
    } catch (error) {
      if (generation === state.generation) { state.error = error; state.unread = null; badge(); }
      return null;
    } finally {
      if (generation === state.generation) state.loading = false;
    }
  }
  async function listCenter(params = {}) {
    let generation = state.generation;
    const principal = await ensurePrincipal(generation);
    generation = state.generation;
    if (principal == null) throw new Error("請先登入");
    const query = new URLSearchParams({
      page: String(params.page || 1), page_size: String(params.pageSize || params.page_size || 20),
      filter: params.filter || "all", category: params.category || "",
    });
    const result = await request(`/api/notifications?${query.toString()}`);
    if (generation !== state.generation) throw new Error("通知畫面已更新");
    state.unread = Number.isFinite(Number(result?.unread)) ? Number(result.unread) : state.unread;
    badge();
    return result;
  }
  async function markRead(id, knownUnread = false) {
    await request(`/api/notifications/${encodeURIComponent(id)}/read`, { method: "POST", body: {} });
    const item = state.recent.find((row) => String(row.id) === String(id));
    const wasUnread = knownUnread || (item && !(item.readAt || item.read_at));
    if (item && !(item.readAt || item.read_at)) {
      item.readAt = new Date().toISOString(); item.read_at = item.readAt;
    }
    if (wasUnread) state.unread = Math.max(0, Number(state.unread || 0) - 1);
    badge();
    return true;
  }
  async function markAllRead() {
    await request("/api/notifications/read-all", { method: "POST", body: {} });
    state.recent.forEach((item) => { item.readAt = item.readAt || new Date().toISOString(); item.read_at = item.readAt; });
    state.unread = 0; badge();
    return true;
  }
  async function openPopover(event) {
    event?.preventDefault();
    if (state.popover) { closePopover(); return; }
    const anchor = document.querySelector("#nav-notifications");
    if (!anchor || state.principalId == null) return;
    const panel = document.createElement("section");
    panel.className = "notification-popover"; panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-live", "polite");
    state.popover = panel; anchor.parentElement?.appendChild(panel);
    text(panel, "p", "載入中…", "notification-popover-loading");
    const result = await refresh({ recent: true });
    if (state.popover !== panel) return;
    if (!result && state.error) { panel.replaceChildren(); displayError(panel); return; }
    renderPopover(state.recent);
  }
  function principalChanged() {
    state.generation += 1; state.principalId = null; state.unread = null;
    state.recent = []; state.error = null; closePopover(); badge();
    if (state.started) setTimeout(() => refresh(), 0);
  }
  function bindHeader() {
    const node = document.querySelector("#nav-notifications");
    if (!node || node.dataset.notificationBound) return;
    node.dataset.notificationBound = "1";
    node.addEventListener("click", openPopover);
    document.addEventListener("click", (event) => {
      if (state.popover && !event.target.closest("#nav-notifications") && !event.target.closest(".notification-popover")) closePopover();
    });
  }
  function start() {
    if (state.started) return;
    state.started = true; bindHeader(); setTimeout(() => refresh(), 0);
    global.addEventListener("focus", () => { if (!document.hidden) refresh(); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  }
  function resetForTests() {
    closePopover(); Object.assign(state, { started: false, principalId: null, generation: 0, unread: null, recent: [], loading: false, error: null }); badge();
  }
  global.StoryLingoNotifications = {
    start, refresh, listCenter, markRead, markAllRead, closePopover, resetForTests,
    captureNavigation, navigateToTarget,
    _state: state, _safeRoute: safeRoute,
  };
  document.addEventListener("storylingo:auth-changed", principalChanged);
  if (global.StoryLingoBootstrap?.onViewVisible) global.StoryLingoBootstrap.onViewVisible(start);
  else document.addEventListener("storylingo:view-visible", start, { once: true });
})(window);
