// Typed helpers around the FastAPI backend.

export interface Stats {
  collection: string;
  count: number;
  embedder: string;
  dim: number;
  data_dir: string;
  extract_knowledge: boolean;
}

export interface SearchResult {
  id: string;
  text: string;
  score: number;
  metadata: Record<string, string>;
}

export interface SearchFilters {
  kind?: string | null;
  language?: string | null;
  source_contains?: string | null;
}

export interface Item {
  id: string;
  text: string;
  metadata: Record<string, string>;
}

export interface Citation {
  n: number;
  id: string;
  source: string | null;
  symbol: string | null;
  score: number;
}

export interface Answer {
  answer: string;
  citations: Citation[];
}

export interface SettingsView {
  editable: Record<string, string | number | boolean>;
  collection: string;
  embedder_backend: string;
  data_dir: string;
}

export interface Collection {
  name: string;
  count: number;
}

export interface ViewTool {
  name: string;
  description: string;
  filters: Record<string, string>;
  default_top_k: number;
}

export interface ViewSpec {
  title: string;
  purpose: string;
  tools: ViewTool[];
}

export type ViewsMap = Record<string, ViewSpec>;

export interface NewItem {
  text: string;
  source?: string;
  knowledge_type?: string;
  audience?: string[];
  tags?: string[];
  summary?: string;
}

export interface SimulateResult {
  view: string;
  tools: { tool: string; results: SearchResult[] }[];
  grounding: { hits: number; avg_score: number; knowledge_types: string[] };
}

// --- Knowledge graph -----------------------------------------------------
export interface GraphEntity {
  name: string;
  normalized_name: string;
  type: string;
  confidence?: number;
  aliases?: string[];
  chunk_ids?: string[];
}

export interface GraphNeighbor {
  entity: GraphEntity;
  relation_type: string;
  direction: "in" | "out";
}

export interface GraphNeighbors {
  entity: GraphEntity | null;
  neighbors: GraphNeighbor[];
}

export interface EntityRef {
  name: string;
  normalized_name: string;
  type: string;
}

export interface WorkflowStep {
  order: number;
  text: string;
  precondition: string;
  chunk_id: string;
}

export interface GraphWorkflow {
  workflow_name: string;
  prerequisites: string[];
  steps: WorkflowStep[];
}

export interface WorkflowRef {
  name: string;
}

// --- Metrics -------------------------------------------------------------
export interface MetricsView {
  product: {
    published_mcps: number;
    knowledge_objects: number;
    indexed_sources: number;
  };
  agent: {
    total_events: number;
    grounding_hit_rate: number;
    avg_hits: number;
    avg_score: number;
    retrieval_precision: number;
  };
}

// --- Pre-Execution Advisor ----------------------------------------------
export interface AdviseResult {
  action: string;
  workflow: SearchResult[];
  risks: SearchResult[];
  permissions: SearchResult[];
  dependencies: SearchResult[];
  constraints: SearchResult[];
  graph_workflow: GraphWorkflow | null;
  summary: { counts: Record<string, number>; knowledge_types: string[] };
}

// --- Source registry -----------------------------------------------------
export interface SourceInfo {
  source: string;
  chunks: number;
  kinds: string[];
  review: { approved: number; pending: number; rejected: number; unset: number };
}

// --- Dynamic MCP endpoints ----------------------------------------------
export interface McpEndpoint {
  view: string;
  title: string;
  path: string;
  published: boolean;
  url: string;
}

// Knowledge classification vocabulary, kept in sync with the backend.
export const KNOWLEDGE_TYPES = [
  "Feature", "Workflow", "API", "Permission", "Constraint", "Error",
  "Troubleshooting", "Architecture", "Code", "Glossary", "Runbook", "FAQ",
];

export const AUDIENCES = [
  "product_manager", "solutions_architect", "operations", "engineering", "support",
];

// --- Active collection (knowledge base) ----------------------------------
let activeCollection: string | null = localStorage.getItem("odm_collection");

export function getActiveCollection(): string | null {
  return activeCollection;
}

export function setActiveCollection(name: string | null): void {
  activeCollection = name;
  if (name) localStorage.setItem("odm_collection", name);
  else localStorage.removeItem("odm_collection");
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const h = { ...extra };
  if (activeCollection) h["X-Collection"] = activeCollection;
  return h;
}

function withCollection(url: string): string {
  if (!activeCollection) return url;
  return url + (url.includes("?") ? "&" : "?") + "collection=" + encodeURIComponent(activeCollection);
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  stats: () => fetch("/api/stats", { headers: headers() }).then(json<Stats>),

  search: (query: string, top_k: number, filters: SearchFilters = {}) =>
    fetch("/api/search", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ query, top_k, ...filters }),
    }).then(json<SearchResult[]>),

  ask: (query: string, top_k = 6) =>
    fetch("/api/ask", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ query, top_k }),
    }).then(json<Answer>),

  items: (
    limit: number,
    offset: number,
    kind: string | null,
    filters: { review_status?: string | null; knowledge_type?: string | null } = {}
  ) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (kind) params.set("kind", kind);
    if (filters.review_status) params.set("review_status", filters.review_status);
    if (filters.knowledge_type) params.set("knowledge_type", filters.knowledge_type);
    return fetch(`/api/items?${params}`, { headers: headers() }).then(json<Item[]>);
  },

  updateItem: (id: string, metadata: Record<string, string>) =>
    fetch(`/api/items/${id}`, {
      method: "PATCH",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ metadata }),
    }).then(json<Item>),

  addItem: (item: NewItem) =>
    fetch("/api/items", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify(item),
    }).then(json<Item>),

  approveItem: (id: string) =>
    fetch(`/api/items/${id}/approve`, { method: "POST", headers: headers() }).then(json<Item>),

  rejectItem: (id: string) =>
    fetch(`/api/items/${id}/reject`, { method: "POST", headers: headers() }).then(json<Item>),

  views: () => fetch("/api/views", { headers: headers() }).then(json<ViewsMap>),

  simulate: (view: string, query: string, top_k = 5) =>
    fetch("/api/simulate", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ view, query, top_k }),
    }).then(json<SimulateResult>),

  deleteItem: (id: string) =>
    fetch(`/api/items/${id}`, { method: "DELETE", headers: headers() }).then(
      json<{ deleted: string }>
    ),

  upload: (files: FileList) => {
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    return fetch("/api/upload", { method: "POST", headers: headers(), body: form }).then(
      json<{ path: string; files: string[] }>
    );
  },

  getSettings: () => fetch("/api/settings", { headers: headers() }).then(json<SettingsView>),

  patchSettings: (values: Record<string, string | number | boolean>) =>
    fetch("/api/settings", {
      method: "PATCH",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ values }),
    }).then(json<{ updated: string[] }>),

  collections: () =>
    fetch("/api/collections", { headers: headers() }).then(
      json<{ active: string; collections: Collection[] }>
    ),

  createCollection: (name: string) =>
    fetch("/api/collections", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ name }),
    }).then(json<{ created: string }>),

  deleteCollection: (name: string) =>
    fetch(`/api/collections/${encodeURIComponent(name)}`, {
      method: "DELETE",
      headers: headers(),
    }).then(json<{ deleted: string }>),

  // -- knowledge graph ----------------------------------------------------
  graphEntities: (q?: string, type?: string, limit = 100) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (q) params.set("q", q);
    if (type) params.set("type", type);
    return fetch(withCollection(`/api/graph/entities?${params}`), { headers: headers() }).then(
      json<{ items: EntityRef[] }>
    );
  },

  graphEntity: (name: string) =>
    fetch(withCollection(`/api/graph/entity/${encodeURIComponent(name)}`), {
      headers: headers(),
    }).then(json<GraphNeighbors>),

  graphWorkflows: (q?: string, limit = 100) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (q) params.set("q", q);
    return fetch(withCollection(`/api/graph/workflows?${params}`), { headers: headers() }).then(
      json<{ items: WorkflowRef[] }>
    );
  },

  graphWorkflow: (name: string) =>
    fetch(withCollection(`/api/graph/workflow/${encodeURIComponent(name)}`), {
      headers: headers(),
    }).then(json<GraphWorkflow>),

  // -- metrics ------------------------------------------------------------
  metrics: () => fetch(withCollection("/api/metrics"), { headers: headers() }).then(json<MetricsView>),

  // -- pre-execution advisor ---------------------------------------------
  advise: (action: string, top_k = 5) =>
    fetch("/api/advise", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ action, top_k }),
    }).then(json<AdviseResult>),

  // -- source registry ----------------------------------------------------
  sources: () =>
    fetch(withCollection("/api/sources"), { headers: headers() }).then(
      json<{ sources: SourceInfo[] }>
    ),

  deleteSource: (source: string) =>
    fetch(withCollection("/api/sources"), {
      method: "DELETE",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ source }),
    }).then(json<{ deleted: number; source: string }>),

  // -- dynamic MCP endpoints ---------------------------------------------
  mcpEndpoints: () =>
    fetch("/api/mcp/endpoints", { headers: headers() }).then(json<McpEndpoint[]>),

  publishMcp: (view: string) =>
    fetch("/api/mcp/endpoints", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ view }),
    }).then(json<McpEndpoint>),

  unpublishMcp: (view: string) =>
    fetch(`/api/mcp/endpoints/${encodeURIComponent(view)}`, {
      method: "DELETE",
      headers: headers(),
    }).then(json<{ unpublished: string }>),
};

// Stream a cited answer via Server-Sent Events. Answer text arrives as "delta"
// events; a final "citations" event carries the sources. Returns an
// unsubscribe fn.
export function askStream(
  query: string,
  top_k: number,
  onDelta: (text: string) => void,
  onCitations: (citations: Citation[]) => void,
  onError: (detail: string) => void,
  onDone: () => void
): () => void {
  const url = withCollection(
    `/api/ask/stream?query=${encodeURIComponent(query)}&top_k=${top_k}`
  );
  const source = new EventSource(url);
  source.addEventListener("delta", (ev) => {
    try {
      onDelta((JSON.parse((ev as MessageEvent).data) as { text: string }).text);
    } catch {
      /* ignore */
    }
  });
  source.addEventListener("citations", (ev) => {
    try {
      onCitations(
        (JSON.parse((ev as MessageEvent).data) as { citations: Citation[] }).citations
      );
    } catch {
      /* ignore */
    }
    source.close();
    onDone();
  });
  source.addEventListener("error", (ev) => {
    try {
      onError((JSON.parse((ev as MessageEvent).data) as { detail: string }).detail);
    } catch {
      /* connection error with no payload */
    }
    source.close();
    onDone();
  });
  return () => source.close();
}

// Stream ingest progress via Server-Sent Events. Returns an unsubscribe fn.
export function ingestStream(
  path: string,
  onEvent: (e: Record<string, unknown>) => void,
  onDone: () => void,
  sync = false
): () => void {
  const url = withCollection(
    `/api/ingest/stream?path=${encodeURIComponent(path)}&sync=${sync}`
  );
  const source = new EventSource(url);
  const handler = (ev: MessageEvent) => {
    try {
      onEvent(JSON.parse(ev.data));
    } catch {
      /* ignore keep-alive */
    }
  };
  // The backend names events by stage; listen to the ones we render.
  ["load", "split", "extract", "embed", "store", "prune", "skip", "error", "done", "report"].forEach(
    (name) => source.addEventListener(name, handler as EventListener)
  );
  source.addEventListener("report", () => {
    source.close();
    onDone();
  });
  source.onerror = () => {
    source.close();
    onDone();
  };
  return () => source.close();
}
