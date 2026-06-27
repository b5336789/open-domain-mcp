import { expect, test } from "@playwright/test";
import { installApiMocks } from "./helpers/mockApi";

const ARTICLES = [
  {
    id: "article-1",
    title: "Deployment Approval",
    topic: "deployments",
    business_relevance: 0.91,
    cross_validated: true,
    sources: ["docs/deploy.md", "runbooks/release.md"],
    body: "Deployments require release manager approval.",
  },
  {
    id: "article-2",
    title: "Rollback Procedure",
    topic: "rollbacks",
    business_relevance: 0.68,
    cross_validated: false,
    sources: ["runbooks/rollback.md"],
    body: "Rollback within 15 minutes when health checks fail.",
  },
];

const TASK_RESPONSE = {
  id: "task-article-1",
  type: "synthesize",
  title: "Synthesize articles",
  collection: "default",
  status: "queued",
  total: 0,
  done: 0,
  failures: [],
  error: null,
  result: null,
};

test.describe("knowledge review", () => {
  test.beforeEach(async ({ page }) => {
    await installApiMocks(page, {
      "GET /api/articles": ARTICLES,
      "POST /api/tasks": TASK_RESPONSE,
    });
  });

  test("renders article curation and queues synthesis", async ({ page }) => {
    let taskPayload: unknown = null;
    page.on("request", async (request) => {
      if (
        request.method() === "POST" &&
        new URL(request.url()).pathname === "/api/tasks"
      ) {
        taskPayload = request.postDataJSON();
      }
    });

    await page.goto("/#/review");

    await expect(
      page.getByRole("heading", { name: "Knowledge Review" }),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "Article Curation" })).toBeVisible();
    await expect(page.getByText("Deployment Approval")).toBeVisible();
    await expect(page.getByText("relevance 91%")).toBeVisible();
    await expect(page.getByText("cross-validated")).toBeVisible();
    await expect(page.getByText("2 sources")).toBeVisible();

    await page.getByRole("button", { name: "Synthesize articles" }).click();

    await expect(page.getByText(/Synthesis queued/)).toBeVisible();
    await expect.poll(() => taskPayload).toEqual({
      type: "synthesize",
      params: {},
    });
  });
});
