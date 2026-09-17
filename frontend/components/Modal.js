"use strict";

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function focusable(container) {
  return Array.from(container.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])"))
    .filter((node) => !node.disabled && !node.hidden && node.getAttribute("aria-hidden") !== "true");
}

export function modal({ title = "對話框", content = "", footer = "", size = "normal", onClose = null } = {}) {
  const existing = document.querySelector(".modal-overlay");
  if (existing && existing._modalApi) existing._modalApi.close(true);
  else if (existing) existing.remove();

  const overlay = document.createElement("div");
  const titleId = `modal-title-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  overlay.className = "modal-overlay ui-dialog-overlay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-labelledby", titleId);
  overlay.tabIndex = -1;
  overlay.innerHTML = `
    <div class="modal ui-dialog ${size ? `modal-${esc(size)}` : ""}" role="document">
      <header class="modal-header">
        <h2 class="modal-title" id="${titleId}">${esc(title || "對話框")}</h2>
        <button type="button" class="btn btn-ghost btn-icon ui-button modal-close" aria-label="關閉">✕</button>
      </header>
      <div class="modal-body">${content}</div>
      ${footer ? `<footer class="modal-footer">${footer}</footer>` : ""}
    </div>`;

  const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  let closed = false;
  let closeTimer = null;
  let onCloseCalled = false;

  const finishClose = () => {
    if (!overlay.isConnected) return;
    overlay.dispatchEvent(new Event("modal:close", { bubbles: false }));
    overlay.remove();
    document.body.classList.remove("modal-open");
    if (previousFocus && document.contains(previousFocus)) previousFocus.focus();
    if (!onCloseCalled && onClose) { onCloseCalled = true; onClose(); }
  };
  const close = (immediate = false) => {
    if (closed) return;
    closed = true;
    overlay.classList.add("modal-closing");
    if (immediate) finishClose();
    else closeTimer = setTimeout(finishClose, 160);
  };
  const keyHandler = (event) => {
    if (event.key === "Escape") { event.preventDefault(); close(); return; }
    if (event.key !== "Tab") return;
    const items = focusable(overlay);
    if (!items.length) { event.preventDefault(); overlay.focus(); return; }
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };

  overlay._modalApi = { close };
  overlay._prevFocus = previousFocus;
  overlay._titleId = titleId;
  overlay.addEventListener("mousedown", (event) => { if (event.target === overlay) close(); });
  overlay.querySelector(".modal-close")?.addEventListener("click", () => close());
  overlay.addEventListener("modal:close", () => {
    document.removeEventListener("keydown", keyHandler);
    if (closeTimer) clearTimeout(closeTimer);
  }, { once: true });
  document.body.appendChild(overlay);
  document.body.classList.add("modal-open");
  document.addEventListener("keydown", keyHandler);

  setTimeout(() => {
    if (!closed && overlay.isConnected) (focusable(overlay)[0] || overlay).focus();
  }, 10);

  return { element: overlay, close };
}

export function closeModal() {
  const overlay = document.querySelector(".modal-overlay");
  if (overlay?._modalApi) overlay._modalApi.close(true);
  else if (overlay) overlay.remove();
  document.body.classList.remove("modal-open");
}

export default modal;
