"use strict";
/* 共用 Toast（L1-1）：
 * 單一容器 .toast-container（與 components/Toast.js 同 CSS），
 * app.js 與 platform.js 統一委派，棄用 #toast 元素與 platform 的 alert()。
 * 掛在 window.NovelToast。
 */
(function () {
  const TYPE_CLASS = { success: "toast-success", error: "toast-error", info: "toast-info", warning: "toast-warning" };

  function toast(message, type = "info", duration = 2600) {
    const container = document.querySelector(".toast-container");
    const box = container || document.createElement("div");
    if (!container) {
      box.className = "toast-container";
      box.setAttribute("role", "status");
      box.setAttribute("aria-live", "polite");
      document.body.appendChild(box);
    }
    const el = document.createElement("div");
    el.className = `toast ${TYPE_CLASS[type] || "toast-info"}`;
    el.setAttribute("role", type === "error" ? "alert" : "status");
    el.textContent = String(message == null ? "" : message);
    box.appendChild(el);
    const dismiss = () => { el.remove(); };
    const timer = setTimeout(() => { dismiss(); }, duration);
    el.addEventListener("click", () => { clearTimeout(timer); dismiss(); });
    return dismiss;
  }

  toast.success = (m, d) => toast(m, "success", d);
  toast.error = (m, d) => toast(m, "error", d);
  toast.info = (m, d) => toast(m, "info", d);
  toast.dismissAll = () => document.querySelectorAll(".toast-container .toast").forEach((el) => el.remove());
  toast._TYPE_CLASS = TYPE_CLASS;

  window.NovelToast = { toast };
})();