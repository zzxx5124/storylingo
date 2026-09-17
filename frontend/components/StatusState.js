"use strict";

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

const ALERT_STATES = new Set(["error", "conflict", "permission", "stale"]);

export function statusState({ type = "empty", title = "", message = "", action = "", className = "" } = {}) {
  const safeType = String(type || "empty").toLowerCase();
  const role = ALERT_STATES.has(safeType) ? "alert" : (safeType === "loading" ? "status" : "region");
  const live = role === "region" ? "polite" : role;
  return `<section class="status-state status-state-${esc(safeType)} ui-status-state ${esc(className)}" data-status-state="${esc(safeType)}" role="${role}" aria-live="${live}"><h2 class="status-state-title">${esc(title)}</h2>${message ? `<p class="status-state-message">${esc(message)}</p>` : ""}${action ? `<div class="status-state-action">${action}</div>` : ""}</section>`;
}

export default statusState;
