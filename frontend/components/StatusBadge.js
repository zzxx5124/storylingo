"use strict";

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

const TONES = new Set(["neutral", "info", "success", "warning", "danger", "pending", "stale"]);

export function statusBadge({ label = "狀態未知", tone = "neutral", icon = "", className = "" } = {}) {
  const safeTone = TONES.has(tone) ? tone : "neutral";
  const iconHtml = icon ? `<span class="status-badge-icon" aria-hidden="true">${esc(icon)}</span>` : "";
  return `<span class="status-badge status-badge-${safeTone} ui-status-badge ${esc(className)}" data-status-tone="${safeTone}">${iconHtml}<span class="status-badge-label">${esc(label)}</span></span>`;
}

export default statusBadge;
