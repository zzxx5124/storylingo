import "@testing-library/jest-dom/vitest";

import { afterEach } from "vitest";

afterEach(() => {
  document.querySelectorAll(".modal-overlay, #toast-container").forEach((node) => node.remove());
  document.body.classList.remove("modal-open");
});