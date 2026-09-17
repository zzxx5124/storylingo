"use strict";

import { button } from "./Button.js";

/**
 * Icon-only button adapter.  A visible glyph is not an accessible name, so a
 * label is required at the component boundary.
 */
export function iconButton({ label = "", icon = "", variant = "ghost", className = "", ...options } = {}) {
  const accessibleLabel = String(label || "").trim();
  if (!accessibleLabel) throw new TypeError("IconButton 需要 accessible label");
  return button({
    ...options,
    label: icon,
    variant,
    className: ["btn-icon", "ui-icon-button", className].filter(Boolean).join(" "),
    attrs: { ...(options.attrs || {}), "aria-label": accessibleLabel },
  });
}

export default iconButton;
