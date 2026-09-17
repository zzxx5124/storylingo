"use strict";
/* 共用 Toast 通知元件（FE-006）。
 * toast(message, { type: success|error|info|warning, duration })
 * 也可整批 toast.show / toast.dismissAll。
 */

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

const ICONS = {
  success: "✓",
  error: "✕",
  info: "ℹ",
  warning: "!",
};

export function toast(message, { type = "info", duration = 3000 } = {}) {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    container.className = "toast-container";
    container.setAttribute("aria-live", "polite");
    document.body.appendChild(container);
  }
  const item = document.createElement("div");
  item.className = `toast toast-${type}`;
  item.setAttribute("role", type === "error" ? "alert" : "status");
  item.innerHTML = `<span class="toast-icon" aria-hidden="true">${ICONS[type] || "ℹ"}</span><span class="toast-message">${esc(message)}</span><button type="button" class="btn btn-ghost btn-icon toast-close" aria-label="關閉">✕</button>`;
  container.appendChild(item);

  const dismiss = () => {
    item.classList.add("toast-leaving");
    setTimeout(() => item.remove(), 200);
  };
  item.querySelector(".toast-close").addEventListener("click", () => {
    dismiss();
    if (item._timer) clearTimeout(item._timer);
  });
  if (duration > 0) {
    item._timer = setTimeout(dismiss, duration);
  }
  return dismiss;
}

toast.show = (message, options) => toast(message, options);
toast.success = (message, options = {}) => toast(message, { type: "success", ...options });
toast.error = (message, options = {}) => toast(message, { type: "error", duration: 5000, ...options });
toast.info = (message, options = {}) => toast(message, { type: "info", ...options });
toast.warning = (message, options = {}) => toast(message, { type: "warning", ...options });
toast.dismissAll = () => {
  document.querySelectorAll("#toast-container .toast").forEach((t) => t.remove());
};

export default toast;