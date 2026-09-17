import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
    include: ["frontend/components/__tests__/**/*.test.js", "frontend/services/__tests__/**/*.test.js"],
    setupFiles: ["./frontend/components/__tests__/setup.js"],
  },
});
