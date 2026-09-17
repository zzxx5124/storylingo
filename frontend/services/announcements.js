"use strict";

/* app.js and platform.js share one document.  Keep announcement delivery in
 * one bootstrap-scoped coordinator so route changes cannot duplicate popups. */
(function installAnnouncements(global) {
  const MAX_PER_ENTRY = 3;
  const MAX_MARKERS = 100;
  const LOCAL_KEY = "storylingo_announcement_ack_v1";
  const SESSION_KEY = "storylingo_announcement_session_v1";
  const state = {
    started: false, shown: 0, queue: [], active: null, previousFocus: null,
    generation: 0, suppressed: new Set(), authenticated: false,
  };

  function getStorage(name) { try { return global[name]; } catch (_) { return null; } }
  function markers(name) {
    const store = getStorage(name);
    if (!store) return {};
    try {
      const value = JSON.parse(store.getItem(name === "localStorage" ? LOCAL_KEY : SESSION_KEY) || "{}");
      return value && typeof value === "object" && !Array.isArray(value) ? value : {};
    } catch (_) { return {}; }
  }
  function hasMarker(name, item) { return Object.prototype.hasOwnProperty.call(markers(name), `${item.id}:${item.displayVersion}`); }
  function addMarker(name, item) {
    const store = getStorage(name);
    if (!store) return;
    try {
      const current = markers(name);
      current[`${item.id}:${item.displayVersion}`] = Date.now();
      const bounded = {};
      Object.keys(current).slice(-MAX_MARKERS).forEach((key) => { bounded[key] = current[key]; });
      store.setItem(name === "localStorage" ? LOCAL_KEY : SESSION_KEY, JSON.stringify(bounded));
    } catch (_) { /* storage-disabled browsers must retain usable navigation */ }
  }
  function isSafeTarget(target) {
    if (typeof target !== "string" || !target || target.length > 500) return false;
    if (/^(javascript|data|vbscript):/i.test(target)) return false;
    if (target.startsWith("#/")) return !target.includes("//") && !target.includes("\\") && !/[\u0000-\u001f]/.test(target);
    try {
      const parsed = new URL(target, global.location?.href || "https://storylingo.invalid/");
      return parsed.protocol === "https:" && !!parsed.hostname && !parsed.username && !parsed.password;
    } catch (_) { return false; }
  }
  function hidden(element) { return !element || element.hidden || element.getAttribute("aria-hidden") === "true"; }
  function blocked() {
    const modal = [...document.querySelectorAll("[role=dialog], .modal-overlay")]
      .some((element) => !hidden(element) && !element.classList.contains("announcement-modal-overlay"));
    const audio = [...document.querySelectorAll("audio")].some((element) => !element.paused && !element.ended);
    return modal || audio;
  }
  function text(parent, tag, value, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value == null ? "" : String(value);
    parent.appendChild(node);
    return node;
  }
  function focusable(root) { return [...root.querySelectorAll("button, a[href], [tabindex]:not([tabindex='-1'])")].filter((node) => !node.disabled && !hidden(node)); }
  function acknowledge(item) {
    if (item.displayMode === "once_per_version" && state.authenticated) {
      return global.NovelApi.request(`/api/announcements/${encodeURIComponent(item.id)}/acknowledge`, { method: "POST", body: { displayVersion: item.displayVersion } }).catch(() => null);
    }
    addMarker(item.displayMode === "once_per_session" ? "sessionStorage" : "localStorage", item);
    return Promise.resolve(null);
  }
  function finish(item, target = "") {
    const overlay = state.active;
    if (!overlay) return;
    document.removeEventListener("keydown", overlay._keyHandler);
    overlay.remove(); state.active = null; document.body.classList.remove("modal-open");
    if (state.previousFocus && typeof state.previousFocus.focus === "function") state.previousFocus.focus();
    state.previousFocus = null; state.suppressed.add(`${item.id}:${item.displayVersion}`);
    void acknowledge(item);
    if (target) {
      if (target.startsWith("#/")) global.location.hash = target.slice(1);
      else if (typeof global.location.assign === "function") global.location.assign(target);
    }
    state.shown += 1;
    setTimeout(attemptShow, 0);
  }
  function show(item) {
    if (state.active || state.shown >= MAX_PER_ENTRY || blocked()) return false;
    const titleId = `announcement-title-${item.id}`;
    const overlay = document.createElement("div"); overlay.className = "announcement-modal-overlay"; overlay.setAttribute("role", "presentation");
    const dialog = document.createElement("div"); dialog.className = "announcement-modal modal"; dialog.setAttribute("role", "dialog"); dialog.setAttribute("aria-modal", "true"); dialog.setAttribute("aria-labelledby", titleId);
    const header = document.createElement("div"); header.className = "announcement-modal-header";
    const heading = text(header, "h2", item.title, "announcement-modal-title"); heading.id = titleId;
    const close = document.createElement("button"); close.type = "button"; close.className = "btn btn-ghost announcement-modal-close"; close.setAttribute("aria-label", "關閉公告"); close.textContent = "×"; header.appendChild(close);
    const body = document.createElement("div"); body.className = "announcement-modal-body"; text(body, "p", item.bodyText, "announcement-modal-copy");
    const footer = document.createElement("div"); footer.className = "announcement-modal-footer";
    const dismiss = document.createElement("button"); dismiss.type = "button"; dismiss.className = "btn btn-ghost"; dismiss.textContent = "知道了"; footer.appendChild(dismiss);
    if (item.ctaLabel && isSafeTarget(item.ctaTarget)) {
      const cta = document.createElement("a"); cta.className = "btn btn-accent"; cta.textContent = item.ctaLabel; cta.href = item.ctaTarget;
      if (!item.ctaTarget.startsWith("#/")) { cta.target = "_blank"; cta.rel = "noopener noreferrer"; }
      cta.addEventListener("click", (event) => { event.preventDefault(); finish(item, item.ctaTarget); }); footer.appendChild(cta);
    }
    dialog.append(header, body, footer); overlay.appendChild(dialog); document.body.appendChild(overlay);
    state.active = overlay; state.previousFocus = document.activeElement; document.body.classList.add("modal-open");
    const closeIt = () => finish(item);
    close.addEventListener("click", closeIt); dismiss.addEventListener("click", closeIt); overlay.addEventListener("click", (event) => { if (event.target === overlay) closeIt(); });
    const onKey = (event) => {
      if (event.key === "Escape") { event.preventDefault(); closeIt(); return; }
      if (event.key !== "Tab") return;
      const items = focusable(dialog); if (!items.length) return;
      if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items[items.length - 1].focus(); }
      else if (!event.shiftKey && document.activeElement === items[items.length - 1]) { event.preventDefault(); items[0].focus(); }
    };
    overlay._keyHandler = onKey; document.addEventListener("keydown", onKey); (focusable(dialog)[0] || close).focus();
    return true;
  }
  function attemptShow() {
    if (!state.started || state.active || state.shown >= MAX_PER_ENTRY) return;
    const item = state.queue.find((candidate) => !state.suppressed.has(`${candidate.id}:${candidate.displayVersion}`));
    if (!item) return;
    if (blocked()) { setTimeout(attemptShow, 250); return; }
    show(item);
  }
  function principalChanged() {
    state.generation += 1;
    state.queue = [];
    state.suppressed = new Set();
    if (state.active) {
      document.removeEventListener("keydown", state.active._keyHandler);
      state.active.remove();
      state.active = null;
      if (state.previousFocus && typeof state.previousFocus.focus === "function") state.previousFocus.focus();
      state.previousFocus = null;
      document.body.classList.remove("modal-open");
    }
    if (state.started) setTimeout(load, 0);
  }
  async function load() {
    if (!state.started) return;
    const generation = ++state.generation;
    try {
      const me = await global.NovelApi.request("/api/auth/me");
      if (generation !== state.generation) return;
      state.authenticated = !!me?.authed;
      const result = await global.NovelApi.request("/api/announcements/active");
      if (generation !== state.generation) return;
      state.queue = (result?.items || []).filter((item) => {
        if (item.displayMode === "once_per_session" && hasMarker("sessionStorage", item)) return false;
        if (item.displayMode === "once_per_version" && !state.authenticated && hasMarker("localStorage", item)) return false;
        return true;
      });
      attemptShow();
    } catch (_) { /* popup delivery is intentionally non-blocking */ }
  }
  function start() { if (state.started) return; state.started = true; setTimeout(load, 0); }
  function resetForTests() { if (state.active) state.active.remove(); Object.assign(state, { started: false, shown: 0, queue: [], active: null, previousFocus: null, generation: 0, suppressed: new Set(), authenticated: false }); }
  const api = { start, resetForTests, _state: state, _isSafeTarget: isSafeTarget, _show: show };
  global.StoryLingoAnnouncements = api;
  document.addEventListener("storylingo:auth-changed", principalChanged);
  if (global.StoryLingoBootstrap?.onViewVisible) global.StoryLingoBootstrap.onViewVisible(start);
  else document.addEventListener("storylingo:view-visible", start, { once: true });
})(window);
