import { expect, test } from "@playwright/test";
import {
  DEFAULT_SETTINGS,
  DEFAULT_VIEWS,
  installApiMocks,
} from "./helpers/mockApi";

// One endpoint starts unpublished so we can flip it.
const ENDPOINTS = [
  {
    view: "product",
    title: "Product View",
    path: "/mcp/product",
    published: false,
    url: "http://localhost:8000/mcp/product",
  },
];

const PUBLISHED = {
  view: "product",
  title: "Product View",
  path: "/mcp/product",
  published: true,
  url: "http://localhost:8000/mcp/product",
};

test.describe("mcp builder", () => {
  test.beforeEach(async ({ page }) => {
    await installApiMocks(page, {
      "GET /api/views": DEFAULT_VIEWS,
      "GET /api/settings": DEFAULT_SETTINGS,
      "GET /api/mcp/endpoints": ENDPOINTS,
      "POST /api/mcp/endpoints": PUBLISHED,
    });
  });

  test("publishes an endpoint and the row flips to published", async ({
    page,
  }) => {
    await page.goto("/#/mcp");

    await expect(
      page.getByRole("heading", { name: "MCP Builder" }),
    ).toBeVisible();

    // The endpoint row is uniquely identified by its endpoint URL (the view
    // cards lower on the page do not render the URL). Scoping to it avoids the
    // unrelated "Publish" buttons on the view cards.
    const row = page
      .locator("div.flex.flex-wrap")
      .filter({ hasText: "http://localhost:8000/mcp/product" })
      .first();

    await expect(row.getByText("unpublished")).toBeVisible();

    await row.getByRole("button", { name: "Publish" }).click();

    // After the publish POST resolves, the badge + action button flip.
    await expect(row.getByText("published", { exact: true })).toBeVisible();
    await expect(
      row.getByRole("button", { name: "Unpublish" }),
    ).toBeVisible();
  });
});
