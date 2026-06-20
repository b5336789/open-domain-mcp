import { expect, test } from "@playwright/test";
import { installApiMocks } from "./helpers/mockApi";

const ENTITIES = {
  items: [
    { name: "Deployment", normalized_name: "deployment", type: "Process" },
    { name: "RBAC", normalized_name: "rbac", type: "Permission" },
  ],
};

const ENTITY_DETAIL = {
  entity: {
    name: "Deployment",
    normalized_name: "deployment",
    type: "Process",
    confidence: 0.93,
    chunk_ids: ["chunk-1", "chunk-2"],
  },
  neighbors: [
    {
      entity: {
        name: "Rollback procedure",
        normalized_name: "rollback-procedure",
        type: "Process",
      },
      relation_type: "mitigated_by",
      direction: "out",
    },
    {
      entity: { name: "RBAC", normalized_name: "rbac", type: "Permission" },
      relation_type: "requires",
      direction: "in",
    },
  ],
};

test.describe("graph", () => {
  test.beforeEach(async ({ page }) => {
    await installApiMocks(page, {
      "GET /api/graph/entities": ENTITIES,
      // Prefix match covers /api/graph/entity/Deployment and any name.
      "GET /api/graph/entity/*": ENTITY_DETAIL,
    });
  });

  test("searches an entity, opens it, and renders its neighbors", async ({
    page,
  }) => {
    await page.goto("/#/graph");

    await expect(
      page.getByRole("heading", { name: "Knowledge Graph" }),
    ).toBeVisible();

    // Narrow the entity list, then pick the entity.
    await page.getByPlaceholder("e.g. deployment, RBAC, billing").fill("dep");

    const entityButton = page.getByRole("button", { name: /Deployment/ });
    await expect(entityButton).toBeVisible();
    await entityButton.click();

    // Entity detail header.
    await expect(
      page.getByRole("heading", { name: "Deployment" }),
    ).toBeVisible();
    await expect(page.getByText("conf 0.93")).toBeVisible();

    // Neighbor columns + relations.
    await expect(page.getByText("Outgoing")).toBeVisible();
    await expect(page.getByText("Incoming")).toBeVisible();
    await expect(page.getByText("mitigated_by")).toBeVisible();
    await expect(page.getByText("requires")).toBeVisible();
    await expect(page.getByText("Rollback procedure")).toBeVisible();
  });
});
