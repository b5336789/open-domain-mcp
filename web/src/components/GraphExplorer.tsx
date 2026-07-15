import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { api, EvidenceEntry, GraphEntity, GraphNeighbors } from "../api";
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

type Selection =
  | { kind: "node"; node: GNode }
  | { kind: "link"; link: GLink }
  | null;

/** Merge one /api/graph/entity response into the accumulated node/link maps.
 *  Returns the number of neighbors dropped by the MAX_NEIGHBORS cap. */
function mergeDetail(
  nodes: Map<string, GNode>,
  links: Map<string, GLink>,
  detail: GraphNeighbors,
  markExpanded: string | null,
): number {
  const root = detail.entity;
  if (!root) return 0;
  const existing = nodes.get(root.normalized_name);
  nodes.set(root.normalized_name, {
    id: root.normalized_name,
    name: root.name,
    type: root.type,
    expanded: existing?.expanded || markExpanded === root.normalized_name,
    entity: root,
  });
  const shown = detail.neighbors.slice(0, MAX_NEIGHBORS);
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
      n.direction === "out" ? [root.normalized_name, id] : [id, root.normalized_name];
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
  return detail.neighbors.length - shown.length;
}

export default function GraphExplorer({ rootName }: { rootName: string }) {
  const [nodes, setNodes] = useState<Map<string, GNode>>(new Map());
  const [links, setLinks] = useState<Map<string, GLink>>(new Map());
  const [selection, setSelection] = useState<Selection>(null);
  const [truncated, setTruncated] = useState<{ node: string; hidden: number } | null>(null);
  const [loading, setLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const toast = useToast();

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

  const expand = useCallback(
    async (name: string, isRoot: boolean) => {
      setLoading(true);
      try {
        const detail = await api.graphEntity(name);
        if (!detail.entity) {
          if (isRoot) setNotFound(true);
          return;
        }
        setNodes((prev) => {
          const next = new Map(prev);
          setLinks((prevLinks) => {
            const nextLinks = new Map(prevLinks);
            const hidden = mergeDetail(next, nextLinks, detail, detail.entity!.normalized_name);
            setTruncated(hidden > 0 ? { node: detail.entity!.name, hidden } : null);
            return nextLinks;
          });
          return next;
        });
        if (isRoot) {
          setSelection({
            kind: "node",
            node: {
              id: detail.entity.normalized_name,
              name: detail.entity.name,
              type: detail.entity.type,
              expanded: true,
              entity: detail.entity,
            },
          });
        }
      } catch (e) {
        if (isRoot && String(e).startsWith("404")) setNotFound(true);
        else toast.show(String(e), "red");
      } finally {
        setLoading(false);
      }
    },
    [toast],
  );

  // A new root resets the canvas and loads its ego network.
  useEffect(() => {
    setNodes(new Map());
    setLinks(new Map());
    setSelection(null);
    setTruncated(null);
    setNotFound(false);
    void expand(rootName, true);
  }, [rootName, expand]);

  // react-force-graph mutates node objects (positions); memo on map identity.
  const graphData = useMemo(
    () => ({ nodes: [...nodes.values()], links: [...links.values()] }),
    [nodes, links],
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
            nodeVal={(n) => ((n as GNode).id === rootName ? 3 : 1)}
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
        {truncated && (
          <div
            className="absolute bottom-3 left-3 rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-700 dark:bg-amber-500/15 dark:text-amber-300"
            data-testid="graph-truncation-note"
          >
            Showing {MAX_NEIGHBORS} of {MAX_NEIGHBORS + truncated.hidden} neighbors for{" "}
            {truncated.node}
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
