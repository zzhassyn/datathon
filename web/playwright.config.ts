import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  use: {
    baseURL: process.env.CG_E2E_BASE_URL || "http://127.0.0.1:8127",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "chrome-desktop", use: { ...devices["Desktop Chrome"], channel: "chrome" } },
    { name: "chrome-mobile", use: { ...devices["Pixel 7"], channel: "chrome" } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
    { name: "iphone", use: { ...devices["iPhone 13"] } },
  ],
});
