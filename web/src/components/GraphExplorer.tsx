import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { api, EvidenceEntry, GraphEntity, GraphNeighbor } from "../api";
import { Badge, Card, EmptyState, Spinner, useToast } from "./ui";
import { IconGraph } from "./icons";

// Per-expansion neighbor cap. Truncation is always surfaced with a count
// (Fail Loud) — never silently dropped.
const MAX_NEIGHBORS = 50;

interface GNode {
  id: string; // normalized_name
  name: string;
  type: string;
  expanded: boolean;
  entity?: GraphEntity;
}

interface GLink {
  id: string; // `${src}|${dst}|${relation_type}`
  source: string;
  target: string;
  relation_type: string;
  edge_evidence?: EvidenceEntry[];
}

// react-force-graph mutates link objects after the simulation starts, so
// `link.source`/`link.target` become node object references instead of the
// original id strings. Display code must defend against both shapes.
function linkEndpointId(endpoint: string | GNode): string {
  return typeof endpoint === "object" ? endpoint.id : endpoint;
}

// The fetch helper throws "404: ..." on a missing entity. Treat that as a
// friendly "not found" rather than an error toast (same pattern as Graph.tsx).
function isNotFound(e: unknown): boolean {
  return e instanceof Error && e.message.startsWith("404");
}

type Selection =
  | { kind: "node"; node: GNode }
  | { kind: "link"; link: GLink }
  | null;

/** Accumulated canvas state. Nodes, links, and truncation notices move
 *  together so each expansion is a single pure state update. */
interface GraphState {
  nodes: Map<string, GNode>;
  links: Map<string, GLink>;
  // Per-node truncation record, keyed by normalized_name. Entries accumulate
  // and are never cleared by later expansions (Fail Loud): expanded nodes
  // don't re-fetch, so their hidden neighbors stay hidden and every notice
  // remains relevant until the root resets the canvas.
  truncated: Map<string, { name: string; hidden: number }>;
}

function emptyGraph(): GraphState {
  return { nodes: new Map(), links: new Map(), truncated: new Map() };
}

/** Pure merge of one /api/graph/entity response (for the just-expanded
 *  `entity`) into the accumulated graph state. */
function mergeDetail(
  prev: GraphState,
  entity: GraphEntity,
  neighbors: GraphNeighbor[],
): GraphState {
  const nodes = new Map(prev.nodes);
  const links = new Map(prev.links);
  const truncated = new Map(prev.truncated);
  // The merged center is by definition the node just expanded.
  nodes.set(entity.normalized_name, {
    id: entity.normalized_name,
    name: entity.name,
    type: entity.type,
    expanded: true,
    entity,
  });
  const shown = neighbors.slice(0, MAX_NEIGHBORS);
  for (const n of shown) {
    const id = n.entity.normalized_name;
    if (!nodes.has(id)) {
      nodes.set(id, {
        id,
        name: n.entity.name,
        type: n.entity.type,
        expanded: false,
        entity: n.entity,
      });
    }
    const [src, dst] =
      n.direction === "out" ? [entity.normalized_name, id] : [id, entity.normalized_name];
    const key = `${src}|${dst}|${n.relation_type}`;
    if (!links.has(key)) {
      links.set(key, {
        id: key,
        source: src,
        target: dst,
        relation_type: n.relation_type,
        edge_evidence: n.edge_evidence,
      });
    }
  }
  const hidden = neighbors.length - shown.length;
  if (hidden > 0) {
    truncated.set(entity.normalized_name, { name: entity.name, hidden });
  }
  return { nodes, links, truncated };
}

export default function GraphExplorer({ rootName }: { rootName: string }) {
  const [graph, setGraph] = useState<GraphState>(emptyGraph);
  const [selection, setSelection] = useState<Selection>(null);
  const [loading, setLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);

  // useToast() returns a fresh object identity on provider re-render; hold it
  // in a ref so `expand` (and the root-reset effect keyed on it) stay stable.
  const toast = useToast();
  const toastRef = useRef(toast);
  toastRef.current = toast;

  // Current root, readable from in-flight expansions so responses that arrive
  // after the root changed are dropped instead of merging into the new canvas.
  const rootRef = useRef(rootName);

  // The root's normalized_name, captured once its expansion resolves.
  // `rootName` (the prop) is a display name — comparing it against
  // `GNode.id` (normalized_name) in nodeVal would silently never match for
  // names that differ in case/formatting.
  const rootIdRef = useRef<string | null>(null);

  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(600);
  useEffect(() => {
    if (!wrapRef.current) return;
    const el = wrapRef.current;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  const expand = useCallback(async (name: string, isRoot: boolean) => {
    const rootAtCall = rootRef.current;
    setLoading(true);
    try {
      const detail = await api.graphEntity(name);
      if (rootRef.current !== rootAtCall) return; // stale: root changed mid-flight
      // Unknown entities 404 (handled in the catch below); a 200 response
      // always carries the entity. A contract violation here throws into the
      // same catch and surfaces as a toast (Fail Loud).
      const entity = detail.entity!;
      setGraph((prev) => mergeDetail(prev, entity, detail.neighbors));
      if (isRoot) {
        rootIdRef.current = entity.normalized_name;
        setSelection({
          kind: "node",
          node: {
            id: entity.normalized_name,
            name: entity.name,
            type: entity.type,
            expanded: true,
            entity,
          },
        });
      }
    } catch (e) {
      if (rootRef.current !== rootAtCall) return; // stale: outcome no longer relevant
      if (isRoot && isNotFound(e)) setNotFound(true);
      else toastRef.current.show(String(e), "red");
    } finally {
      setLoading(false);
    }
  }, []);

  // A new root resets the canvas and loads its ego network.
  useEffect(() => {
    rootRef.current = rootName;
    rootIdRef.current = null;
    setGraph(emptyGraph());
    setSelection(null);
    setNotFound(false);
    void expand(rootName, true);
  }, [rootName, expand]);

  // react-force-graph mutates node objects (positions); memo on state identity.
  const graphData = useMemo(
    () => ({ nodes: [...graph.nodes.values()], links: [...graph.links.values()] }),
    [graph],
  );

  if (notFound) {
    return (
      <EmptyState
        icon={<IconGraph className="h-6 w-6" />}
        title="Entity not found"
        hint={`No graph record exists for "${rootName}".`}
      />
    );
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[1fr_20rem]">
      <Card className="relative overflow-hidden p-0" data-testid="graph-canvas-wrap">
        <div ref={wrapRef}>
          <ForceGraph2D
            width={width}
            height={480}
            graphData={graphData}
            nodeId="id"
            nodeLabel="name"
            nodeAutoColorBy="type"
            nodeVal={(n) => ((n as GNode).id === rootIdRef.current ? 3 : 1)}
            linkLabel="relation_type"
            linkDirectionalArrowLength={4}
            linkDirectionalArrowRelPos={1}
            onNodeClick={(n) => {
              const node = n as unknown as GNode;
              setSelection({ kind: "node", node });
              if (!node.expanded) void expand(node.name, false);
            }}
            onLinkClick={(l) => setSelection({ kind: "link", link: l as unknown as GLink })}
          />
        </div>
        {loading && (
          <div className="absolute right-3 top-3">
            <Spinner className="h-4 w-4" />
          </div>
        )}
        {graph.truncated.size > 0 && (
          <div
            className="absolute bottom-3 left-3 space-y-0.5 rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-700 dark:bg-amber-500/15 dark:text-amber-300"
            data-testid="graph-truncation-note"
          >
            {[...graph.truncated.entries()].map(([id, t]) => (
              <div key={id}>
                Showing {MAX_NEIGHBORS} of {MAX_NEIGHBORS + t.hidden} neighbors for {t.name}
              </div>
            ))}
          </div>
        )}
      </Card>
      <SelectionPanel selection={selection} />
    </div>
  );
}

function SelectionPanel({ selection }: { selection: Selection }) {
  if (!selection) {
    return (
      <Card className="p-5 text-sm text-slate-400 dark:text-slate-500">
        Click a node to expand it, or an edge to inspect its evidence.
      </Card>
    );
  }
  if (selection.kind === "node") {
    const { node } = selection;
    return (
      <Card className="space-y-2 p-5" data-testid="graph-selection-panel">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-base font-semibold text-slate-900 dark:text-white">
            {node.name}
          </h3>
          <Badge tone="brand">{node.type}</Badge>
        </div>
        {node.entity?.chunk_ids && (
          <div className="text-xs text-slate-400">
            {node.entity.chunk_ids.length} source chunk
            {node.entity.chunk_ids.length === 1 ? "" : "s"}
          </div>
        )}
        <EvidenceList entries={node.entity?.evidence ?? []} />
      </Card>
    );
  }
  const { link } = selection;
  return (
    <Card className="space-y-2 p-5" data-testid="graph-selection-panel">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="amber">{link.relation_type}</Badge>
        <span className="font-mono text-xs text-slate-500">
          {linkEndpointId(link.source)} → {linkEndpointId(link.target)}
        </span>
      </div>
      <EvidenceList entries={link.edge_evidence ?? []} />
    </Card>
  );
}

function EvidenceList({ entries }: { entries: EvidenceEntry[] }) {
  if (entries.length === 0) {
    return <div className="text-xs text-slate-400">No evidence recorded.</div>;
  }
  return (
    <div className="space-y-2 pt-1">
      <div className="text-xs uppercase tracking-wide text-slate-400">
        Evidence ({entries.length})
      </div>
      {entries.map((entry, i) => (
        <div key={i} className="rounded-md border border-slate-100 p-2 dark:border-slate-800">
          {entry.verified === false && (
            <div className="mb-1">
              <Badge tone="red">unverified</Badge>
            </div>
          )}
          <code className="block whitespace-pre-wrap break-all font-mono text-xs text-slate-700 dark:text-slate-300">
            {entry.quote}
          </code>
          <div className="mt-0.5 font-mono text-xs text-slate-400">
            {entry.start_line != null && entry.end_line != null
              ? `${entry.source ?? ""}:${entry.start_line}-${entry.end_line}`
              : (entry.source ?? "")}
          </div>
        </div>
      ))}
    </div>
  );
}
