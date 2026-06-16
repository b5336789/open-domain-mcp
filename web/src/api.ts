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

  items: (limit: number, offset: number, kind: string | null) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (kind) params.set("kind", kind);
    return fetch(`/api/items?${params}`, { headers: headers() }).then(json<Item[]>);
  },

  updateItem: (id: string, metadata: Record<string, string>) =>
    fetch(`/api/items/${id}`, {
      method: "PATCH",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ metadata }),
    }).then(json<Item>),

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
