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

const VALIDATION_RUN = {
  id: "run-1",
  scenario_id: "scenario-1",
  collection: "default",
  view: "product",
  query: "How do I roll back?",
  status: "passed",
  grounding_hits: 3,
  avg_score: 0.812,
  tool_results: 2,
  knowledge_types: ["Runbook"],
  error: "",
  created_at: 1814052100,
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
    validation: {
      collection: "default",
      view: "product",
      status: "passed",
      scenario_count: 1,
      latest_run_count: 1,
      passed: 1,
      failed: 0,
      pass_rate: 1,
      latest_run: VALIDATION_RUN,
    },
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
  validation: {
    collection: "default",
    view: "product",
    status: "passed",
    scenario_count: 1,
    latest_run_count: 1,
    passed: 1,
    failed: 0,
    pass_rate: 1,
    latest_run: VALIDATION_RUN,
  },
};

test.describe("mcp builder", () => {
  test.beforeEach(async ({ page }) => {
    await installApiMocks(page, {
      "GET /api/views": DEFAULT_VIEWS,
      "GET /api/settings": DEFAULT_SETTINGS,
      "GET /api/mcp/endpoints": ENDPOINTS,
      "POST /api/mcp/endpoints": PUBLISHED,
      "PATCH /api/settings": { updated: ["retrieve_approved_only"] },
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
    await expect(row.getByText("Validation passed")).toBeVisible();
    await expect(row.getByText(/Latest run/)).toBeVisible();

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

  test("refreshes publish readiness after saving retrieval policy", async ({
    page,
  }) => {
    const saveEvents: string[] = [];
    page.on("response", (response) => {
      const request = response.request();
      if (
        request.method() === "PATCH" &&
        new URL(request.url()).pathname === "/api/settings" &&
        response.status() === 200
      ) {
        saveEvents.push("PATCH /api/settings 200");
      }
    });
    page.on("request", (request) => {
      if (
        request.method() === "GET" &&
        new URL(request.url()).pathname === "/api/quality/evidence"
      ) {
        saveEvents.push("GET /api/quality/evidence");
      }
    });

    await page.goto("/#/mcp");

    await expect(
      page.getByText("Published MCP views use approved-only hybrid retrieval."),
    ).toBeVisible();
    saveEvents.length = 0;

    await page.getByRole("button", { name: "Save policy" }).click();

    await expect.poll(() => saveEvents.join(" > ")).toBe(
      "PATCH /api/settings 200 > GET /api/quality/evidence",
    );
  });
});
