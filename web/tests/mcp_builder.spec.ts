import { expect, test } from "@playwright/test";
import {
  DEFAULT_SETTINGS,
  DEFAULT_VIEWS,
  DEFAULT_QUALITY_EVIDENCE,
  installApiMocks,
} from "./helpers/mockApi";

const DECISION = {
  id: "decision-1",
  collection: "default",
  view: "product",
  action: "publish",
  status: "published",
  readiness_status: "needs_review",
  readiness_score: 72,
  override_reason: "Internal pilot only.",
  endpoint_url: "http://localhost:8000/mcp/product",
  created_at: 1814052000,
  gates: [
    {
      id: "review",
      gate: "Review",
      status: "needs_review",
      score: 80,
      summary: "48 of 60 knowledge objects are approved.",
    },
  ],
};

const ENDPOINTS = [
  {
    view: "product",
    title: "Product View",
    path: "/mcp/product",
    published: false,
    status: "unpublished",
    url: "http://localhost:8000/mcp/product",
    latest_decision: null,
    history: [],
  },
];

const PUBLISHED = {
  view: "product",
  title: "Product View",
  path: "/mcp/product",
  published: true,
  status: "published",
  url: "http://localhost:8000/mcp/product",
  latest_decision: DECISION,
  history: [DECISION],
};

test.describe("mcp builder", () => {
  test.beforeEach(async ({ page }) => {
    await installApiMocks(page, {
      "GET /api/views": DEFAULT_VIEWS,
      "GET /api/settings": DEFAULT_SETTINGS,
      "GET /api/mcp/endpoints": ENDPOINTS,
      "POST /api/mcp/endpoints": PUBLISHED,
      "GET /api/quality/evidence": DEFAULT_QUALITY_EVIDENCE,
    });
  });

  test("publishes with override evidence and shows decision history", async ({
    page,
  }) => {
    await page.goto("/#/mcp");

    await expect(
      page.getByRole("heading", { name: "MCP Publish" }),
    ).toBeVisible();
    await expect(page.getByText("Review pending knowledge before publishing MCP views.")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Review" })).toBeVisible();

    // The endpoint row is uniquely identified by its endpoint URL (the view
    // cards lower on the page do not render the URL). Scoping to it avoids the
    // unrelated "Publish" buttons on the view cards.
    const row = page
      .locator("div.flex.flex-wrap")
      .filter({ hasText: "http://localhost:8000/mcp/product" })
      .first();

    await expect(row.getByText("unpublished")).toBeVisible();

    await row.getByRole("button", { name: "Publish" }).click();

    await expect(page.getByRole("dialog", { name: "Publish override" })).toBeVisible();
    await page.getByLabel("Override reason").fill("Internal pilot only.");
    const publishRequest = page.waitForRequest((request) => {
      return request.method() === "POST" && request.url().endsWith("/api/mcp/endpoints");
    });
    await page.getByRole("button", { name: "Publish with override" }).click();
    expect((await publishRequest).postDataJSON()).toEqual({
      view: "product",
      override_reason: "Internal pilot only.",
    });

    // After the publish POST resolves, the badge + action button flip.
    await expect(row.getByText("published", { exact: true })).toBeVisible();
    await expect(row.getByText("Latest decision: publish")).toBeVisible();
    await expect(row.getByText("Internal pilot only.")).toBeVisible();
    await expect(
      row.getByRole("button", { name: "Unpublish" }),
    ).toBeVisible();
  });
});
