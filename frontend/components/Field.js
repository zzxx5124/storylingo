"use strict";
/* 共用表單元件：Input / Select / Textarea（FE-006）。
 * 提供 label、help text、error message、required 標記。
 * 回傳 HTML 字串；attrs 會直接寫入 DOM 屬性。
 */

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function wrap({ id, label, help, error, required, children }) {
  const labelHtml = label ? `<label for="${esc(id)}">${esc(label)}${required ? ' <span class="required-mark" aria-hidden="true">*</span>' : ""}</label>` : "";
  const helpHtml = help ? `<span class="field-help" id="${esc(id)}-help">${esc(help)}</span>` : "";
  const errorHtml = error ? `<span class="field-error" role="alert" id="${esc(id)}-error">${esc(error)}</span>` : "";
  return `<div class="field ui-field${error ? " field-invalid" : ""}">${labelHtml}${children}${helpHtml}${errorHtml}</div>`;
}

function describedBy(id, help, error) {
  return [help ? `${id}-help` : "", error ? `${id}-error` : ""].filter(Boolean).join(" ");
}

function classString(className = "") {
  return ["field-input", className].filter(Boolean).join(" ");
}

export const input = ({ id = "", name = "", type = "text", value = "", placeholder = "", label = "", help = "", error = "", required = false, disabled = false, maxlength, autocomplete, className = "" } = {}) => {
  const attrs = [
    `class="${classString(className)}"`,
    `id="${esc(id)}"`,
    `name="${esc(name || id)}"`,
    `type="${esc(type)}"`,
    `value="${esc(value)}"`,
  ];
  if (placeholder) attrs.push(`placeholder="${esc(placeholder)}"`);
  if (disabled) attrs.push("disabled");
  if (maxlength) attrs.push(`maxlength="${maxlength}"`);
  if (autocomplete) attrs.push(`autocomplete="${esc(autocomplete)}"`);
  if (required) attrs.push("required");
  const described = describedBy(id, help, error);
  if (described) attrs.push(`aria-describedby="${esc(described)}"`);
  if (error) attrs.push(`aria-invalid="true"`);
  return wrap({ id, label, help, error, required, children: `<input ${attrs.join(" ")}>` });
};

export const select = ({ id = "", name = "", value = "", options = [], label = "", help = "", error = "", required = false, disabled = false, className = "", placeholder = "" } = {}) => {
  const parts = [`class="${classString(className)}"`, `id="${esc(id)}"`, `name="${esc(name || id)}"`];
  if (disabled) parts.push("disabled");
  if (required) parts.push("required");
  const described = describedBy(id, help, error);
  if (described) parts.push(`aria-describedby="${esc(described)}"`);
  if (error) parts.push(`aria-invalid="true"`);
  const builtOptions = placeholder
    ? `<option value="">${esc(placeholder)}</option>` +
      options.map((opt) => `<option value="${esc(opt.value)}"${String(opt.value) === String(value) ? " selected" : ""}>${esc(opt.label)}</option>`).join("")
    : options.map((opt) => `<option value="${esc(opt.value)}"${String(opt.value) === String(value) ? " selected" : ""}>${esc(opt.label)}</option>`).join("");
  return wrap({ id, label, help, error, required, children: `<select ${parts.join(" ")}>${builtOptions}</select>` });
};

export const textarea = ({ id = "", name = "", value = "", placeholder = "", rows = 4, label = "", help = "", error = "", required = false, disabled = false, maxlength, className = "" } = {}) => {
  const attrs = [
    `class="${classString(className)}"`,
    `id="${esc(id)}"`,
    `name="${esc(name || id)}"`,
    `rows="${rows}"`,
  ];
  if (placeholder) attrs.push(`placeholder="${esc(placeholder)}"`);
  if (disabled) attrs.push("disabled");
  if (maxlength) attrs.push(`maxlength="${maxlength}"`);
  if (required) attrs.push("required");
  const described = describedBy(id, help, error);
  if (described) attrs.push(`aria-describedby="${esc(described)}"`);
  if (error) attrs.push(`aria-invalid="true"`);
  return wrap({ id, label, help, error, required, children: `<textarea ${attrs.join(" ")}>${esc(value)}</textarea>` });
};

export default { input, select, textarea };
