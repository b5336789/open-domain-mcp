import { defineConfig, devices } from "@playwright/test";

// E2E config for the OpenDomainMCP SPA.
//
// The tests are fully self-contained: every `/api/*` request is intercepted
// with Playwright route mocks (see tests/helpers/mockApi.ts), so the suite runs
// against the production build served by `vite preview` WITHOUT a live FastAPI
// backend (which would otherwise require MariaDB + provider API keys).
//
// The build is emitted to ../src/opendomainmcp/api/static (see vite.config.ts),
// and `vite preview` serves that directory. `reuseExistingServer` lets a
// already-running preview be reused for fast local iteration.

const PORT = 4173;
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "tests",
  // A spec mocks at most a handful of endpoints; give the build room to run.
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "dot" : "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run build && npm run preview -- --port 4173",
    url: BASE_URL,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
