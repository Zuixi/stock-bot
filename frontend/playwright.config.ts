import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000" },
  reporter: [["list"]],
});
