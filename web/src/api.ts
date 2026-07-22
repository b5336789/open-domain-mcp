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

export interface EvidenceEntry {
  claim: string;
  quote: string;
  source: string | null;
  start_line: number | null;
  end_line: number | null;
  verified: boolean;
}

export interface Item {
  id: string;
  text: string;
  metadata: Record<string, string>;
  evidence?: EvidenceEntry[];
}

export interface AuditRow {
  ts: string;
  item_id: string;
  action: string;
  actor: string;
  note: string;
  prev_status: string;
  new_status: string;
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

export interface ValidationScenario {
  id: string;
  collection: string;
  view: string;
  name: string;
  query: string;
  created_at: number;
  latest_run?: ValidationRun | null;
}

export interface ValidationRun {
  id: string;
  scenario_id: string;
  collection: string;
  view: string;
  query: string;
  status: "passed" | "failed";
  grounding_hits: number;
  avg_score: number;
  tool_results: number;
  knowledge_types: string[];
  error: string;
  created_at: number;
}

export interface ValidationSummary {
  collection: string;
  view: string | null;
  status: "validating" | "passed" | "failed";
  scenario_count: number;
  latest_run_count: number;
  passed: number;
  failed: number;
  pass_rate: number;
  latest_run: ValidationRun | null;
}

export interface ValidationRunResponse {
  scenario: ValidationScenario;
  run: ValidationRun;
  result: SimulateResult | null;
  summary: ValidationSummary;
}

// --- Knowledge graph -----------------------------------------------------
export interface GraphEntity {
  name: string;
  normalized_name: string;
  type: string;
  confidence?: number;
  aliases?: string[];
  chunk_ids?: string[];
  evidence?: EvidenceEntry[];
}

export interface GraphNeighbor {
  entity: GraphEntity;
  relation_type: string;
  direction: "in" | "out";
  edge_evidence?: EvidenceEntry[];
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

export type ReadinessStatus =
  | "blocked"
  | "needs_review"
  | "validating"
  | "ready"
  | "published";

export interface KnowledgeBaseReadiness {
  collection: string;
  status: ReadinessStatus;
  score: number;
  next_action: string;
  blockers: string[];
  warnings: string[];
  stats: {
    count: number;
    embedder: string;
    dim: number;
  };
  source_health: {
    sources: number;
    chunks: number;
    stale: number;
    failed: number;
  };
  review_health: {
    approved: number;
    pending: number;
    rejected: number;
    unset: number;
    approved_ratio: number;
  };
  article_health: {
    articles: number;
    cross_validated: number;
    avg_relevance: number;
  };
  retrieval_health: {
    events: number;
    grounding_hit_rate: number;
    avg_score: number;
    retrieval_precision: number;
  };
  job_health: {
    queued: number;
    running: number;
    done: number;
    error: number;
    cancelled: number;
  };
  graph_health: {
    available: boolean;
    entities: number;
    workflows: number;
  };
}

export interface QualityEvidence {
  id: string;
  gate: string;
  status: ReadinessStatus;
  score: number;
  summary: string;
  details: string[];
  action: string;
}

export interface QualityEvidenceResponse {
  collection: string;
  status: ReadinessStatus;
  score: number;
  next_action: string;
  evidence: QualityEvidence[];
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

// --- Articles ------------------------------------------------------------
export interface Article {
  id: string;
  title: string;
  topic: string;
  business_relevance: number;
  cross_validated: boolean;
  sources: string[];
  body: string;
}

// --- Task center ---------------------------------------------------------
export interface TaskItem {
  id: string;
  type: "ingest" | "synthesize" | "extract" | "export";
  title: string;
  collection: string;
  status: "queued" | "running" | "done" | "error" | "cancelled";
  total: number;
  done: number;
  failures: { name: string; status: string }[];
  error: string | null;
  error_type?: string | null;
  error_message?: string | null;
  result: Record<string, unknown> | null;
  attempts?: number;
  recovered_at?: number | null;
  recovery_count?: number;
  last_transition?: string | null;
}

export interface TaskList { tasks: TaskItem[]; }

export interface TaskChild {
  name: string;
  status: string;
}

// --- Async (resumable) ingest job ---------------------------------------
export interface IngestJob {
  job_id: string;
  status: "queued" | "running" | "done" | "error" | "cancelled";
  ingest_path: string;
  cancel_requested: boolean;
  progress: {
    files_indexed: number;
    chunks_indexed: number;
    chunks_pruned: number;
    current_file: string;
    completed_files: number;
  };
  errors: { path: string; error: string }[];
  report: {
    files_indexed: number;
    chunks_indexed: number;
    chunks_pruned: number;
  } | null;
}

// --- Dynamic MCP endpoints ----------------------------------------------
export interface PublishDecision {
  id: string;
  collection: string;
  view: string;
  action: "publish" | "unpublish";
  status: "published" | "unpublished";
  readiness_status: ReadinessStatus;
  readiness_score: number;
  override_reason: string;
  endpoint_url: string;
  created_at: number;
  gates: {
    id: string;
    gate: string;
    status: ReadinessStatus;
    score: number;
    summary: string;
  }[];
}

export interface McpEndpoint {
  view: string;
  title: string;
  path: string;
  published: boolean;
  status: "published" | "unpublished";
  url: string;
  latest_decision: PublishDecision | null;
  history: PublishDecision[];
  validation: ValidationSummary;
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
    filters: {
      review_status?: string | null;
      knowledge_type?: string | null;
      trust?: string | null;
      evidence_status?: string | null;
      order?: string | null;
    } = {}
  ) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (kind) params.set("kind", kind);
    if (filters.review_status) params.set("review_status", filters.review_status);
    if (filters.knowledge_type) params.set("knowledge_type", filters.knowledge_type);
    if (filters.trust) params.set("trust", filters.trust);
    if (filters.evidence_status) params.set("evidence_status", filters.evidence_status);
    if (filters.order) params.set("order", filters.order);
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

  approveItem: (id: string, note = "") =>
    fetch(`/api/items/${id}/approve`, {
      method: "POST",
      headers: headers(note ? { "Content-Type": "application/json" } : {}),
      body: note ? JSON.stringify({ note }) : undefined,
    }).then(json<Item>),

  rejectItem: (id: string, note = "") =>
    fetch(`/api/items/${id}/reject`, {
      method: "POST",
      headers: headers(note ? { "Content-Type": "application/json" } : {}),
      body: note ? JSON.stringify({ note }) : undefined,
    }).then(json<Item>),

  reviewBatch: (ids: string[], action: "approve" | "reject", note = "") =>
    fetch("/api/items/review-batch", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ ids, action, note }),
    }).then(json<{ updated: string[]; missing: string[]; action: string }>),

  itemHistory: (id: string) =>
    fetch(`/api/items/${id}/history`, { headers: headers() }).then(json<AuditRow[]>),

  views: () => fetch("/api/views", { headers: headers() }).then(json<ViewsMap>),

  simulate: (view: string, query: string, top_k = 5) =>
    fetch("/api/simulate", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ view, query, top_k }),
    }).then(json<SimulateResult>),

  validationScenarios: (view?: string) => {
    const params = new URLSearchParams();
    if (view) params.set("view", view);
    const suffix = params.toString() ? `?${params}` : "";
    return fetch(withCollection(`/api/validation/scenarios${suffix}`), {
      headers: headers(),
    }).then(json<ValidationScenario[]>);
  },

  runValidationScenario: (id: string) =>
    fetch(withCollection(`/api/validation/scenarios/${encodeURIComponent(id)}/run`), {
      method: "POST",
      headers: headers(),
    }).then(json<ValidationRun>),

  runValidation: (view: string, query: string, name: string, top_k = 5) =>
    fetch(withCollection("/api/validation/run"), {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ view, query, name, top_k }),
    }).then(json<ValidationRunResponse>),

  validationSummary: (view?: string) => {
    const params = new URLSearchParams();
    if (view) params.set("view", view);
    const suffix = params.toString() ? `?${params}` : "";
    return fetch(withCollection(`/api/validation/summary${suffix}`), {
      headers: headers(),
    }).then(json<ValidationSummary>);
  },

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

  workspaceReadiness: () =>
    fetch(withCollection("/api/workspace/readiness"), { headers: headers() }).then(
      json<KnowledgeBaseReadiness>
    ),

  qualityEvidence: () =>
    fetch(withCollection("/api/quality/evidence"), { headers: headers() }).then(
      json<QualityEvidenceResponse>
    ),

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

  // -- async (resumable) ingest ------------------------------------------
  ingestAsync: (path: string, sync = false) =>
    fetch(
      withCollection(
        `/api/ingest/async?path=${encodeURIComponent(path)}&sync=${sync}`
      ),
      { method: "POST", headers: headers() }
    ).then(json<{ job_id: string; status: string }>),

  ingestJob: (jobId: string) =>
    fetch(withCollection(`/api/ingest/jobs/${encodeURIComponent(jobId)}`), {
      headers: headers(),
    }).then(json<IngestJob>),

  cancelIngestJob: (jobId: string) =>
    fetch(withCollection(`/api/ingest/jobs/${encodeURIComponent(jobId)}`), {
      method: "DELETE",
      headers: headers(),
    }).then(json<{ job_id: string; status: string }>),

  // -- articles -----------------------------------------------------------
  articles: () =>
    fetch("/api/articles", { headers: headers() }).then(json<Article[]>),

  // -- dynamic MCP endpoints ---------------------------------------------
  mcpEndpoints: () =>
    fetch("/api/mcp/endpoints", { headers: headers() }).then(json<McpEndpoint[]>),

  publishMcp: (view: string, override_reason = "") =>
    fetch("/api/mcp/endpoints", {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ view, override_reason }),
    }).then(json<McpEndpoint>),

  unpublishMcp: (view: string) =>
    fetch(`/api/mcp/endpoints/${encodeURIComponent(view)}`, {
      method: "DELETE",
      headers: headers(),
    }).then(json<McpEndpoint>),

  // -- task center --------------------------------------------------------
  createTask: (type: string, params: Record<string, unknown>) =>
    fetch(withCollection("/api/tasks"), {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ type, params }),
    }).then(json<TaskItem>),

  exportDownloadUrl: (id: string) =>
    withCollection(`/api/export/${encodeURIComponent(id)}/download`),

  listTasks: () =>
    fetch(withCollection("/api/tasks"), { headers: headers() })
      .then(json<Partial<TaskList>>)
      .then((body) => ({ tasks: Array.isArray(body.tasks) ? body.tasks : [] })),

  taskChildren: (id: string, offset = 0, limit = 100) =>
    fetch(
      withCollection(
        `/api/tasks/${encodeURIComponent(id)}/children?offset=${offset}&limit=${limit}`
      ),
      { headers: headers() }
    ).then(json<{ children: TaskChild[]; total: number }>),

  cancelTask: (id: string) =>
    fetch(withCollection(`/api/tasks/${encodeURIComponent(id)}`), {
      method: "DELETE",
      headers: headers(),
    }).then(json<{ cancelled: boolean }>),

  retryTask: (id: string) =>
    fetch(withCollection(`/api/tasks/${encodeURIComponent(id)}/retry`), {
      method: "POST",
      headers: headers(),
    }).then(json<TaskItem>),

  clearTasks: () =>
    fetch(withCollection("/api/tasks/clear"), {
      method: "POST",
      headers: headers(),
    }).then(json<{ cleared: number }>),
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

// Stream article-synthesis progress via Server-Sent Events. Per-topic events
// (topic/stored/rejected/...) drive a live log; a final "report" event carries
// the SynthesisReport counts. Returns an unsubscribe fn.
export function synthesizeStream(
  onEvent: (e: Record<string, unknown>) => void,
  onDone: () => void,
  opts: { limit?: number; dryRun?: boolean } = {}
): () => void {
  const params = new URLSearchParams();
  if (opts.limit != null) params.set("limit", String(opts.limit));
  if (opts.dryRun) params.set("dry_run", "true");
  const qs = params.toString();
  const source = new EventSource(
    withCollection(`/api/synthesize/stream${qs ? `?${qs}` : ""}`)
  );
  const handler = (ev: MessageEvent) => {
    try {
      onEvent(JSON.parse(ev.data));
    } catch {
      /* ignore keep-alive */
    }
  };
  ["start", "topic", "stored", "rejected", "removed", "topic_error", "error", "report"].forEach(
    (name) => source.addEventListener(name, handler as EventListener)
  );
  // Both a successful report and a fatal error end the run.
  ["report", "error"].forEach((name) =>
    source.addEventListener(name, () => {
      source.close();
      onDone();
    })
  );
  source.onerror = () => {
    source.close();
    onDone();
  };
  return () => source.close();
}
