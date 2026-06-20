import { expect, test } from "@playwright/test";
import { installApiMocks } from "./helpers/mockApi";

const NAV_LABELS = [
  "Dashboard",
  "Ingest",
  "Explore",
  "Ask",
  "Browse / Edit",
  "Review",
  "Graph",
  "Advisor",
  "MCP Builder",
  "Simulator",
  "Metrics",
  "Settings",
];

test.describe("smoke", () => {
  test.beforeEach(async ({ page }) => {
    await installApiMocks(page);
  });

  test("renders every sidebar nav link", async ({ page }) => {
    await page.goto("/");

    const sidebar = page.locator("aside");
    for (const label of NAV_LABELS) {
      await expect(
        sidebar.getByRole("link", { name: label, exact: true }),
      ).toBeVisible();
    }
  });

  test("dashboard shows stats and the sources section", async ({ page }) => {
    await page.goto("/");

    // Stat cards from /api/stats.
    await expect(page.getByText("Indexed chunks")).toBeVisible();
    await expect(page.getByText("1,234")).toBeVisible();
    await expect(page.getByText("all-MiniLM-L6-v2")).toBeVisible();

    // Sources section from /api/sources.
    await expect(
      page.getByRole("heading", { name: /Sources/ }),
    ).toBeVisible();
    await expect(page.getByText("docs/deploy.md")).toBeVisible();
    await expect(page.getByText("runbooks/rollback.md")).toBeVisible();
  });
});
