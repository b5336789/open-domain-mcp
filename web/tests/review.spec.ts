import { expect, test } from "@playwright/test";
import { installApiMocks } from "./helpers/mockApi";

const ITEMS_WITH_EVIDENCE = [
  {
    id: "item-ev-1",
    text: "Deployments require release manager approval.",
    metadata: {
      knowledge_type: "Workflow",
      source: "docs/deploy.md",
      confidence: "0.92",
      evidence_status: "verified",
    },
    evidence: [
      {
        claim: "Deployments require approval",
        quote: "All deployments must be approved by a release manager.",
        source: "docs/deploy.md",
        start_line: 10,
        end_line: 12,
        verified: true,
      },
    ],
  },
];

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

test.describe("evidence panels", () => {
  test("shows evidence_status badge and collapsible evidence entries", async ({ page }) => {
    await installApiMocks(page, {
      "GET /api/articles": ARTICLES,
      "GET /api/items": ITEMS_WITH_EVIDENCE,
    });

    await page.goto("/#/review");

    await expect(page.getByRole("heading", { name: "Knowledge Review" })).toBeVisible();

    // evidence_status badge should be visible (exclude hidden select options)
    await expect(page.locator("span", { hasText: /^verified$/ }).first()).toBeVisible();

    // evidence panel toggle should be present and collapsed by default
    const toggle = page.getByRole("button", { name: /Evidence \(1\)/ });
    await expect(toggle).toBeVisible();

    // expand the panel
    await toggle.click();
    await expect(
      page.getByText("All deployments must be approved by a release manager."),
    ).toBeVisible();
    await expect(page.getByText("docs/deploy.md:10-12")).toBeVisible();
  });
});

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

test.describe("batch review", () => {
  test("select-all, batch approve, and history disclosure", async ({ page }) => {
    let batchPayload: unknown = null;
    let itemsCall = 0;
    await installApiMocks(page, {
      "GET /api/articles": ARTICLES,
      "GET /api/items": ITEMS_WITH_EVIDENCE,
      "POST /api/items/review-batch": {
        updated: ["item-ev-1"],
        missing: [],
        action: "approve",
      },
      "GET /api/items/item-ev-1/history": [
        {
          ts: "2026-07-09T00:00:00+00:00",
          item_id: "item-ev-1",
          action: "approve",
          actor: "local",
          note: "bulk",
          prev_status: "pending",
          new_status: "approved",
        },
      ],
    });
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (request.method() === "POST" && url.pathname === "/api/items/review-batch") {
        batchPayload = request.postDataJSON();
      }
      if (request.method() === "GET" && url.pathname === "/api/items") {
        itemsCall += 1;
      }
    });

    await page.goto("/#/review");
    await expect(page.getByRole("heading", { name: "Knowledge Review" })).toBeVisible();

    // history disclosure lazy-loads and renders an audit row
    await page.getByRole("button", { name: /History/ }).click();
    await expect(page.getByText(/approve by local \(bulk\)/)).toBeVisible();

    // select all on page -> batch bar appears
    await page.getByLabel("Select all on page").check();
    await expect(page.getByText("1 selected")).toBeVisible();

    // batch approve posts the ids and reloads the list
    await page.getByPlaceholder("Optional note…").fill("bulk");
    await page.getByRole("button", { name: "Approve selected" }).click();
    await expect(page.getByText(/Batch approved 1 item/)).toBeVisible();
    await expect.poll(() => batchPayload).toEqual({
      ids: ["item-ev-1"],
      action: "approve",
      note: "bulk",
    });
    await expect.poll(() => itemsCall).toBeGreaterThan(1); // reloaded
  });

  test("risk sort is on by default and unchecking removes priority order", async ({ page }) => {
    const itemUrls: string[] = [];
    await installApiMocks(page, {
      "GET /api/articles": ARTICLES,
      "GET /api/items": ITEMS_WITH_EVIDENCE,
    });
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (request.method() === "GET" && url.pathname === "/api/items") {
        itemUrls.push(url.search);
      }
    });

    await page.goto("/#/review");
    await expect(page.getByRole("heading", { name: "Knowledge Review" })).toBeVisible();

    // Default on: first load must include order=priority without clicking toggle
    await expect
      .poll(() => itemUrls.some((s) => s.includes("order=priority")))
      .toBe(true);

    // Uncheck the toggle: subsequent request must NOT include order=priority
    const countBefore = itemUrls.length;
    await page.getByLabel("Sort by risk").uncheck();
    await expect
      .poll(() => itemUrls.slice(countBefore).some((s) => !s.includes("order=priority")))
      .toBe(true);
  });

  test("single-item approve clears selection and hides batch bar", async ({ page }) => {
    await installApiMocks(page, {
      "GET /api/articles": ARTICLES,
      "GET /api/items": ITEMS_WITH_EVIDENCE,
      "POST /api/items/item-ev-1/approve": ITEMS_WITH_EVIDENCE[0],
    });

    await page.goto("/#/review");
    await expect(page.getByRole("heading", { name: "Knowledge Review" })).toBeVisible();

    // Select the item via checkbox
    await page.getByLabel("Select item-ev-1").check();
    await expect(page.getByText("1 selected")).toBeVisible();

    // Approve via the card button (single-item approve)
    await page.getByRole("button", { name: "Approve" }).first().click();
    // Batch bar should disappear (selection cleared)
    await expect(page.getByText("1 selected")).not.toBeVisible();
  });
});
