"use strict";

// A release mismatch is visible and user-controlled.  It never clears
// localStorage/sessionStorage and never reloads while a dirty form is marked.
(() => {
  const CHECK_INTERVAL_MS = 60 * 1000;
  let currentRelease = null;
  let checking = false;

  function hasUnsavedWork() {
    return Boolean(document.querySelector("[data-unsaved='true'], [data-dirty='true']"));
  }

  function showUpdate(releaseId) {
    let banner = document.getElementById("release-update-banner");
    if (!banner) {
      banner = document.createElement("aside");
      banner.id = "release-update-banner";
      banner.className = "release-update-banner";
      banner.setAttribute("role", "status");
      banner.setAttribute("aria-live", "polite");
      banner.innerHTML = '<span class="release-update-message">StoryLingo 有新版本可用。</span><button type="button" class="btn btn-accent release-update-button">重新載入</button>';
      document.body.appendChild(banner);
      banner.querySelector("button")?.addEventListener("click", () => {
        if (hasUnsavedWork() && !window.confirm("目前頁面可能有尚未儲存的內容，確定要重新載入嗎？")) return;
        window.location.reload();
      });
    }
    banner.dataset.release = releaseId;
    banner.hidden = false;
  }

  async function checkRelease() {
    if (checking || document.visibilityState === "hidden") return;
    checking = true;
    try {
      const response = await fetch("/api/health/live", {
        cache: "no-store",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const payload = await response.json();
      const releaseId = String(payload.releaseId || "").trim();
      if (!releaseId) return;
      if (currentRelease && currentRelease !== releaseId) showUpdate(releaseId);
      currentRelease = releaseId;
    } catch (_) {
      // Temporary offline/API failure must not disrupt the current session.
    } finally {
      checking = false;
    }
  }

  function start() {
    void checkRelease();
    window.setInterval(checkRelease, CHECK_INTERVAL_MS);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") void checkRelease();
    });
  }

  // Kept small and side-effect free so browser tests and diagnostics can
  // trigger the same check without waiting for the one-minute interval.
  window.__storyLingoCheckRelease = checkRelease;

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
