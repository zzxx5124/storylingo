"use strict";

import { modal } from "./Modal.js";
import { textarea } from "./Field.js";
import { button } from "./Button.js";

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

/**
 * Thin confirmation adapter over the canonical Modal.  Business authorization
 * remains the backend's responsibility; this only standardizes the interaction.
 */
export function confirmDialog({ title = "請確認操作", message = "", confirmLabel = "確認", cancelLabel = "取消", danger = false, reasonLabel = "原因", requireReason = false, onConfirm = null, onCancel = null } = {}) {
  const reasonId = `confirm-reason-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const reasonHtml = requireReason ? textarea({ id: reasonId, label: reasonLabel, required: true, rows: 3 }) : "";
  const footer = `${button({ label: cancelLabel, variant: "ghost", className: "confirm-dialog-cancel" })}${button({ label: confirmLabel, variant: danger ? "danger" : "primary", className: "confirm-dialog-confirm", attrs: { "data-confirm-action": "true" } })}`;
  const instance = modal({
    title,
    content: `<div class="confirm-dialog-content">${message ? `<p>${esc(message)}</p>` : ""}${reasonHtml}<p class="confirm-dialog-error" role="alert" hidden></p></div>`,
    footer,
    size: "small",
  });
  const cancel = instance.element.querySelector(".confirm-dialog-cancel");
  const confirm = instance.element.querySelector(".confirm-dialog-confirm");
  cancel?.addEventListener("click", () => { instance.close(); if (onCancel) onCancel(); });
  confirm?.addEventListener("click", () => {
    const reason = requireReason ? instance.element.querySelector(`#${CSS.escape(reasonId)}`)?.value.trim() || "" : "";
    if (requireReason && !reason) {
      const field = instance.element.querySelector(`#${CSS.escape(reasonId)}`);
      const error = instance.element.querySelector(".confirm-dialog-error");
      field?.setAttribute("aria-invalid", "true");
      if (error) { error.textContent = "請填寫原因。"; error.hidden = false; }
      field?.focus();
      return;
    }
    instance.close();
    if (onConfirm) onConfirm(reason);
  });
  return instance;
}

export default confirmDialog;
