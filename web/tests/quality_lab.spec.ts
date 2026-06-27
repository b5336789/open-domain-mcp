import { expect, test } from "@playwright/test";
import { installApiMocks } from "./helpers/mockApi";

const QUALITY_EVIDENCE = {
  collection: "default",
  status: "needs_review",
  score: 72,
  next_action: "Review pending knowledge before publishing MCP views.",
  evidence: [
    {
      id: "coverage",
      gate: "Coverage",
      status: "ready",
      score: 100,
      summary: "60 indexed knowledge objects across 2 sources.",
      details: ["2 sources", "60 chunks", "0 stale", "0 failed"],
      action: "Coverage is sufficient.",
    },
    {
      id: "review",
      gate: "Review",
      status: "needs_review",
      score: 80,
      summary: "48 of 60 knowledge objects are approved.",
      details: ["10 pending", "2 rejected", "0 unreviewed"],
      action: "Review pending knowledge objects.",
    },
    {
      id: "articles",
      gate: "Articles",
      status: "needs_review",
      score: 76,
      summary: "4 synthesized articles, 2 cross-validated.",
      details: ["average relevance 76%", "2 needs curation"],
      action: "Curate synthesized articles.",
    },
    {
      id: "retrieval",
      gate: "Retrieval",
      status: "ready",
      score: 82,
      summary: "250 retrieval events with 82% grounding hit rate.",
      details: ["average score 73%", "precision 64%"],
      action: "Keep validating with representative scenarios.",
    },
    {
      id: "graph",
      gate: "Graph",
      status: "ready",
      score: 100,
      summary: "2 entities and 1 workflows indexed.",
      details: ["2 entities", "1 workflows"],
      action: "Graph evidence is ready.",
    },
    {
      id: "jobs",
      gate: "Jobs",
      status: "ready",
      score: 100,
      summary: "No active or failed background jobs.",
      details: ["0 queued", "0 running", "0 failed"],
      action: "Job gate is clear.",
    },
  ],
};

test.describe("quality lab", () => {
  test.beforeEach(async ({ page }) => {
    await installApiMocks(page, {
      "GET /api/quality/evidence": QUALITY_EVIDENCE,
    });
  });

  test("renders readiness evidence and recommended action", async ({ page }) => {
    await page.goto("/#/quality");

    await expect(page.getByRole("heading", { name: "Quality Lab" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "default" })).toBeVisible();
    await expect(page.getByText("needs review").first()).toBeVisible();
    await expect(page.getByText("72", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Review pending knowledge before publishing MCP views."),
    ).toBeVisible();

    for (const gate of [
      "Coverage",
      "Review",
      "Articles",
      "Retrieval",
      "Graph",
      "Jobs",
    ]) {
      await expect(page.getByRole("heading", { name: gate })).toBeVisible();
    }
    await expect(page.getByText("4 synthesized articles, 2 cross-validated.")).toBeVisible();
    const main = page.getByRole("main");
    for (const link of [
      "Source Intake",
      "Review Knowledge",
      "Curate Articles",
      "Inspect Graph",
      "Run Advisor",
      "Run Simulator",
      "Detailed Metrics",
    ]) {
      await expect(main.getByRole("link", { name: link })).toBeVisible();
    }
  });
});
