import { expect, test } from "@playwright/test";
import { installApiMocks } from "./helpers/mockApi";

const NAV_LABELS = [
  "Command Center",
  "Source Intake",
  "Explore",
  "Ask",
  "Browse / Edit",
  "Articles",
  "Review",
  "Quality Lab",
  "Graph",
  "Advisor",
  "MCP Publish",
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
    for (const heading of ["Knowledge", "Quality", "Publish"]) {
      await expect(
        sidebar.getByText(heading, { exact: true }),
      ).toBeVisible();
    }
  });

  test("renders command center readiness", async ({ page }) => {
    await page.goto("/");

    await expect(
      page.getByRole("heading", { name: "Command Center" }),
    ).toBeVisible();
    await expect(page.getByText("needs review")).toBeVisible();
    await expect(page.getByText("72", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Review pending knowledge before publishing MCP views."),
    ).toBeVisible();
  });

  test("shows source, review, job, and graph health summaries", async ({
    page,
  }) => {
    await page.goto("/");

    const main = page.locator("main");
    await expect(main.getByText("Sources", { exact: true })).toBeVisible();
    await expect(main.getByText("2", { exact: true })).toBeVisible();
    await expect(main.getByText("60 chunks")).toBeVisible();
    await expect(main.getByText("Approved", { exact: true })).toBeVisible();
    await expect(main.getByText("80%")).toBeVisible();
    await expect(main.getByText("10 pending")).toBeVisible();
    await expect(main.getByText("Jobs", { exact: true })).toBeVisible();
    await expect(main.getByText("0 active")).toBeVisible();
    await expect(main.getByText("Graph", { exact: true })).toBeVisible();
    await expect(main.getByText("available")).toBeVisible();
  });
});

test.describe("task center smoke", () => {
  test("shows job failure evidence, recovery state, retry, and cancel", async ({
    page,
  }) => {
    await installApiMocks(page, {
      "GET /api/tasks": {
        tasks: [
          {
            id: "task-failed",
            type: "ingest",
            title: "Ingest /repo/docs",
            collection: "default",
            status: "error",
            total: 10,
            done: 4,
            failures: [],
            error: null,
            result: null,
            attempts: 1,
            recovered_at: null,
            recovery_count: 0,
            last_transition: "running_to_error",
            error_type: "ValueError",
            error_message: "source missing",
          },
          {
            id: "task-recovered",
            type: "synthesize",
            title: "Synthesize articles",
            collection: "default",
            status: "queued",
            total: 0,
            done: 0,
            failures: [],
            error: null,
            result: null,
            attempts: 1,
            recovered_at: 1814052000,
            recovery_count: 1,
            last_transition: "recovered_running_to_queued",
          },
          {
            id: "task-running",
            type: "extract",
            title: "Re-extract source.md",
            collection: "default",
            status: "running",
            total: 5,
            done: 2,
            failures: [],
            error: null,
            result: null,
          },
        ],
      },
      "POST /api/tasks/task-failed/retry": {
        id: "task-retry",
        type: "ingest",
        title: "Ingest /repo/docs",
        collection: "default",
        status: "queued",
        total: 0,
        done: 0,
        failures: [],
        error: null,
        result: { retry_of: "task-failed" },
      },
    });

    await page.goto("/");
    await page.getByRole("button", { name: /Tasks/ }).click();
    await expect(page.getByText("Task Center", { exact: true })).toBeVisible();

    const failedCard = page.getByRole("group", { name: "Ingest /repo/docs" });
    await expect(failedCard.getByText("ValueError")).toBeVisible();
    await expect(failedCard.getByText("source missing")).toBeVisible();

    const recoveredCard = page.getByRole("group", {
      name: "Synthesize articles",
    });
    await expect(recoveredCard.getByText("Recovered 1")).toBeVisible();

    const runningCard = page.getByRole("group", {
      name: "Re-extract source.md",
    });
    await expect(
      runningCard.getByRole("button", { name: "Cancel" }),
    ).toBeVisible();

    const retryRequest = page.waitForRequest((request) => {
      return (
        request.method() === "POST" &&
        new URL(request.url()).pathname === "/api/tasks/task-failed/retry"
      );
    });
    await failedCard.getByRole("button", { name: "Retry" }).click();
    await retryRequest;
  });
});
