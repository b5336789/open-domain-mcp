import { expect, test } from "@playwright/test";
import { installApiMocks } from "./helpers/mockApi";

function facetResult(id: string, text: string, knowledgeType: string) {
  return {
    id,
    text,
    score: 0.77,
    metadata: { knowledge_type: knowledgeType, source: "docs/deploy.md" },
  };
}

const ADVISE_RESULT = {
  action: "Roll out a new billing webhook to production",
  workflow: [facetResult("w1", "Stage the webhook before promoting it.", "Workflow")],
  risks: [facetResult("r1", "Duplicate events can double-charge customers.", "Constraint")],
  permissions: [facetResult("p1", "Requires the billing:write scope.", "Permission")],
  dependencies: [facetResult("d1", "Depends on the payments queue being healthy.", "Architecture")],
  constraints: [facetResult("c1", "Webhooks must respond within 5 seconds.", "Constraint")],
  graph_workflow: null,
  summary: {
    counts: { workflow: 1, risks: 1, permissions: 1, dependencies: 1, constraints: 1 },
    knowledge_types: ["Workflow", "Permission", "Constraint"],
  },
};

const FACET_LABELS = [
  "Workflow",
  "Risks",
  "Permissions",
  "Dependencies",
  "Constraints",
];

test.describe("advisor", () => {
  test.beforeEach(async ({ page }) => {
    await installApiMocks(page, { "POST /api/advise": ADVISE_RESULT });
  });

  test("renders the five facet sections from a mocked advise result", async ({
    page,
  }) => {
    await page.goto("/#/advisor");

    await expect(
      page.getByRole("heading", { name: "Pre-Execution Advisor" }),
    ).toBeVisible();

    await page
      .getByPlaceholder("e.g. Roll out a new billing webhook to production")
      .fill("Roll out a new billing webhook to production");

    await page.getByRole("button", { name: "Advise" }).click();

    // Each facet renders as an uppercase section heading.
    for (const label of FACET_LABELS) {
      await expect(
        page.getByRole("heading", { name: label, exact: true }),
      ).toBeVisible();
    }

    // Mocked result rows render their text.
    await expect(
      page.getByText("Stage the webhook before promoting it."),
    ).toBeVisible();
    await expect(page.getByText("Requires the billing:write scope.")).toBeVisible();
    await expect(
      page.getByText("Webhooks must respond within 5 seconds."),
    ).toBeVisible();
  });
});
