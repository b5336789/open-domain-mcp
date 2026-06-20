import type { Page, Route } from "@playwright/test";

// Shared `/api/*` mock layer for the E2E suite.
//
// `installApiMocks` registers a single route handler on `**/api/**` that
// dispatches by method + pathname. Every endpoint has a sensible default so any
// page renders without a live backend; a spec passes `overrides` to replace the
// JSON for the endpoints it cares about.
//
// Keys are `"<METHOD> <pathname>"` for exact matches, or `"<METHOD> <prefix>*"`
// for prefix matches (e.g. dynamic ids). Values are plain JSON payloads, which
// are returned with status 200, or a `MockResponse` for custom status/body.

export interface MockResponse {
  status?: number;
  json: unknown;
}

export type MockValue = unknown | MockResponse;

export type ApiOverrides = Record<string, MockValue>;

function isMockResponse(value: MockValue): value is MockResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    "json" in (value as Record<string, unknown>)
  );
}

// --- Default payloads ------------------------------------------------------

export const DEFAULT_STATS = {
  collection: "default",
  count: 1234,
  embedder: "all-MiniLM-L6-v2",
  dim: 384,
  data_dir: "/data/odm",
  extract_knowledge: true,
};

export const DEFAULT_COLLECTIONS = {
  active: "default",
  collections: [
    { name: "default", count: 1234 },
    { name: "billing", count: 56 },
  ],
};

export const DEFAULT_SOURCES = {
  sources: [
    {
      source: "docs/deploy.md",
      chunks: 42,
      kinds: ["markdown"],
      review: { approved: 30, pending: 10, rejected: 2, unset: 0 },
    },
    {
      source: "runbooks/rollback.md",
      chunks: 18,
      kinds: ["markdown"],
      review: { approved: 18, pending: 0, rejected: 0, unset: 0 },
    },
  ],
};

export const DEFAULT_METRICS = {
  product: {
    published_mcps: 3,
    knowledge_objects: 1234,
    indexed_sources: 12,
  },
  agent: {
    total_events: 250,
    grounding_hit_rate: 0.82,
    avg_hits: 4.5,
    avg_score: 0.731,
    retrieval_precision: 0.64,
  },
};

export const DEFAULT_VIEWS = {
  product: {
    title: "Product View",
    purpose: "Product knowledge for PMs.",
    tools: [
      {
        name: "search_features",
        description: "Search product features.",
        filters: { knowledge_type: "Feature" },
        default_top_k: 5,
      },
    ],
  },
  ops: {
    title: "Operations View",
    purpose: "Runbooks and operational guidance.",
    tools: [
      {
        name: "search_runbooks",
        description: "Search runbooks.",
        filters: { knowledge_type: "Runbook" },
        default_top_k: 5,
      },
    ],
  },
};

export const DEFAULT_SETTINGS = {
  editable: {
    search_mode: "hybrid",
    rerank_enabled: false,
    retrieve_approved_only: true,
  },
  collection: "default",
  embedder_backend: "sentence-transformers",
  data_dir: "/data/odm",
};

export const DEFAULT_MCP_ENDPOINTS = [
  {
    view: "product",
    title: "Product View",
    path: "/mcp/product",
    published: false,
    url: "http://localhost:8000/mcp/product",
  },
  {
    view: "ops",
    title: "Operations View",
    path: "/mcp/ops",
    published: true,
    url: "http://localhost:8000/mcp/ops",
  },
];

export const DEFAULT_GRAPH_ENTITIES = {
  items: [
    { name: "Deployment", normalized_name: "deployment", type: "Process" },
    { name: "RBAC", normalized_name: "rbac", type: "Permission" },
  ],
};

export const DEFAULT_GRAPH_WORKFLOWS = {
  items: [{ name: "Rollback procedure" }],
};

function buildDefaults(): Record<string, MockValue> {
  return {
    "GET /api/stats": DEFAULT_STATS,
    "GET /api/collections": DEFAULT_COLLECTIONS,
    "GET /api/sources": DEFAULT_SOURCES,
    "GET /api/metrics": DEFAULT_METRICS,
    "GET /api/views": DEFAULT_VIEWS,
    "GET /api/settings": DEFAULT_SETTINGS,
    "GET /api/mcp/endpoints": DEFAULT_MCP_ENDPOINTS,
    "GET /api/graph/entities": DEFAULT_GRAPH_ENTITIES,
    "GET /api/graph/workflows": DEFAULT_GRAPH_WORKFLOWS,
    "GET /api/items": [],
  };
}

function findMock(
  table: Record<string, MockValue>,
  method: string,
  pathname: string,
): MockValue | undefined {
  const exact = `${method} ${pathname}`;
  if (exact in table) return table[exact];

  // Fall back to registered prefix patterns ("METHOD /prefix*").
  for (const key of Object.keys(table)) {
    if (!key.endsWith("*")) continue;
    const [keyMethod, keyPath] = key.split(" ");
    if (keyMethod !== method) continue;
    const prefix = keyPath.slice(0, -1);
    if (pathname.startsWith(prefix)) return table[key];
  }
  return undefined;
}

export async function installApiMocks(
  page: Page,
  overrides: ApiOverrides = {},
): Promise<void> {
  const table = { ...buildDefaults(), ...overrides };

  await page.route("**/api/**", async (route: Route) => {
    const request = route.request();
    const method = request.method();
    const pathname = new URL(request.url()).pathname;

    const mock = findMock(table, method, pathname);

    if (mock === undefined) {
      // Unmocked endpoint: respond with empty 200 so the SPA never hangs on a
      // real network call. Specs should mock anything they assert on.
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "{}",
      });
      return;
    }

    const response: MockResponse = isMockResponse(mock)
      ? mock
      : { status: 200, json: mock };

    await route.fulfill({
      status: response.status ?? 200,
      contentType: "application/json",
      body: JSON.stringify(response.json),
    });
  });
}
