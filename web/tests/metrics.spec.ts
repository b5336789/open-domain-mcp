import { expect, test } from "@playwright/test";
import { installApiMocks } from "./helpers/mockApi";

const METRICS = {
  product: {
    published_mcps: 7,
    knowledge_objects: 4321,
    indexed_sources: 19,
  },
  agent: {
    total_events: 512,
    grounding_hit_rate: 0.875,
    avg_hits: 4.25,
    avg_score: 0.689,
    retrieval_precision: 0.5,
  },
};

test.describe("metrics", () => {
  test.beforeEach(async ({ page }) => {
    await installApiMocks(page, { "GET /api/metrics": METRICS });
  });

  test("renders product and agent metric cards with mocked numbers", async ({
    page,
  }) => {
    await page.goto("/#/metrics");

    await expect(
      page.getByRole("heading", { name: "Metrics", exact: true }),
    ).toBeVisible();

    // Product metrics.
    await expect(page.getByText("Published MCPs")).toBeVisible();
    await expect(page.getByText("7", { exact: true })).toBeVisible();
    await expect(page.getByText("Knowledge objects")).toBeVisible();
    await expect(page.getByText("4,321")).toBeVisible();
    await expect(page.getByText("Indexed sources")).toBeVisible();
    await expect(page.getByText("19", { exact: true })).toBeVisible();

    // Agent metrics.
    await expect(page.getByText("Total events")).toBeVisible();
    await expect(page.getByText("512")).toBeVisible();
    await expect(page.getByText("Grounding hit rate")).toBeVisible();
    await expect(page.getByText("87.5%")).toBeVisible();
    await expect(page.getByText("Retrieval precision")).toBeVisible();
    await expect(page.getByText("50.0%")).toBeVisible();
  });
});
