import { expect, test } from "@playwright/test";
import { installApiMocks } from "./helpers/mockApi";

const TASK_RESPONSE = {
  id: "task-1",
  type: "ingest",
  title: "Ingest /repo/docs",
  collection: "default",
  status: "queued",
  total: 0,
  done: 0,
  failures: [],
  error: null,
  result: null,
};

test.describe("source intake", () => {
  test.beforeEach(async ({ page }) => {
    await installApiMocks(page, {
      "POST /api/tasks": TASK_RESPONSE,
      "DELETE /api/sources": { deleted: 42, source: "docs/deploy.md" },
    });
  });

  test("renders sources and queues path ingestion in the background", async ({
    page,
  }) => {
    await page.goto("/#/intake");

    await expect(
      page.getByRole("heading", { name: "Source Intake" }),
    ).toBeVisible();
    await expect(page.getByText("docs/deploy.md")).toBeVisible();

    await page.getByPlaceholder("/path/to/code-or-docs").fill("/repo/docs");
    await page.getByRole("button", { name: "Run in background" }).click();

    await expect(page.getByText(/Queued in Task Center/)).toBeVisible();
  });

  test("confirms and deletes a source from the registry", async ({ page }) => {
    let deletePayload: unknown = null;
    page.on("request", async (request) => {
      if (
        request.method() === "DELETE" &&
        new URL(request.url()).pathname === "/api/sources"
      ) {
        deletePayload = request.postDataJSON();
      }
    });

    await page.goto("/#/intake");

    const registry = page.getByRole("region", { name: "Source registry" });

    await registry.getByRole("button", { name: "Delete docs/deploy.md" }).click();
    await expect(page.getByText("Delete source", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Delete", exact: true }).click();

    await expect(page.getByText("Removed docs/deploy.md")).toBeVisible();
    await expect(registry.getByText("docs/deploy.md")).toHaveCount(0);
    await expect.poll(() => deletePayload).toEqual({ source: "docs/deploy.md" });
  });
});
