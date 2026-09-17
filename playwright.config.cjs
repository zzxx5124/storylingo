const { defineConfig, devices } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./frontend/e2e",
  timeout: 30_000,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:8000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 5"] } },
  ],
  webServer: process.env.PLAYWRIGHT_BASE_URL ? undefined : {
    command: "python run.py",
    url: "http://127.0.0.1:8000/api/health",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
