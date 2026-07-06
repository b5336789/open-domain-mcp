import { useEffect, useState } from "react";
import {
  api,
  EvidenceEntry,
  EntityRef,
  GraphNeighbor,
  GraphNeighbors,
  GraphWorkflow,
  WorkflowRef,
} from "../api";
import {
  Badge,
  Card,
  EmptyState,
  Input,
  Label,
  PageHeader,
  Skeleton,
  Spinner,
  useToast,
} from "../components/ui";
import { IconGraph } from "../components/icons";

type Mode = "entities" | "workflows";

const SEARCH_DEBOUNCE_MS = 250;

export default function Graph() {
  const [mode, setMode] = useState<Mode>("entities");

  return (
    <div className="space-y-5">
      <PageHeader
        title="Knowledge Graph"
        subtitle="Browse extracted entities, their relationships, and workflow structures."
        icon={<IconGraph />}
        actions={<ModeTabs mode={mode} onChange={setMode} />}
      />

      {mode === "entities" ? <EntitiesMode /> : <WorkflowsMode />}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Mode switcher                                                              */
/* -------------------------------------------------------------------------- */

function ModeTabs({ mode, onChange }: { mode: Mode; onChange: (m: Mode) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-1 dark:border-slate-700 dark:bg-slate-800/70">
      <TabButton active={mode === "entities"} onClick={() => onChange("entities")}>
        Entities
      </TabButton>
      <TabButton active={mode === "workflows"} onClick={() => onChange("workflows")}>
        Workflows
      </TabButton>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "rounded-md px-3 py-1.5 text-sm font-medium transition-colors " +
        (active
          ? "bg-white text-brand-700 shadow-sm dark:bg-slate-900 dark:text-brand-300"
          : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200")
      }
    >
      {children}
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/* Shared helpers                                                             */
/* -------------------------------------------------------------------------- */

// The fetch helper throws "404: ..." on a missing entity/workflow. Treat that
// as a friendly "not found" rather than an error toast.
function isNotFound(e: unknown): boolean {
  return e instanceof Error && e.message.startsWith("404");
}

function useDebounced<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState<T>(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(handle);
  }, [value, delay]);
  return debounced;
}

/* -------------------------------------------------------------------------- */
/* Entities mode                                                              */
/* -------------------------------------------------------------------------- */

function EntitiesMode() {
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebounced(query, SEARCH_DEBOUNCE_MS);
  const [entities, setEntities] = useState<EntityRef[] | null>(null);
  const [listLoading, setListLoading] = useState(false);

  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<GraphNeighbors | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const toast = useToast();

  useEffect(() => {
    let active = true;
    setListLoading(true);
    api
      .graphEntities(debouncedQuery.trim() || undefined)
      .then((res) => {
        if (active) setEntities(res.items);
      })
      .catch((e) => {
        if (active) toast.show(String(e), "red");
      })
      .finally(() => {
        if (active) setListLoading(false);
      });
    return () => {
      active = false;
    };
  }, [debouncedQuery]);

  async function selectEntity(name: string) {
    setSelected(name);
    setDetail(null);
    setNotFound(false);
    setDetailLoading(true);
    try {
      setDetail(await api.graphEntity(name));
    } catch (e) {
      if (isNotFound(e)) setNotFound(true);
      else toast.show(String(e), "red");
    } finally {
      setDetailLoading(false);
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[20rem_1fr]">
      <Card className="space-y-3 p-4">
        <div>
          <Label>Search entities</Label>
          <Input
            className="mt-1.5"
            placeholder="e.g. deployment, RBAC, billing"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        {listLoading && !entities ? (
          <div className="space-y-2">
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : entities && entities.length > 0 ? (
          <div className="max-h-[60vh] space-y-1 overflow-y-auto pr-1">
            {entities.map((entity) => (
              <button
                key={entity.normalized_name}
                type="button"
                onClick={() => selectEntity(entity.name)}
                className={
                  "flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors " +
                  (selected === entity.name
                    ? "bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300"
                    : "text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800")
                }
              >
                <span className="truncate">{entity.name}</span>
                <Badge tone="neutral">{entity.type}</Badge>
              </button>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<IconGraph className="h-6 w-6" />}
            title="No entities found"
            hint={
              query.trim()
                ? "Try a different search term."
                : "This knowledge base has no extracted entities yet."
            }
          />
        )}
      </Card>

      <EntityDetail
        selected={selected}
        detail={detail}
        loading={detailLoading}
        notFound={notFound}
      />
    </div>
  );
}

function EntityDetail({
  selected,
  detail,
  loading,
  notFound,
}: {
  selected: string | null;
  detail: GraphNeighbors | null;
  loading: boolean;
  notFound: boolean;
}) {
  if (!selected) {
    return (
      <EmptyState
        icon={<IconGraph className="h-6 w-6" />}
        title="Select an entity"
        hint="Pick an entity on the left to see its neighbors and relationships."
      />
    );
  }

  if (loading) {
    return (
      <Card className="flex items-center gap-3 p-6 text-sm text-slate-500 dark:text-slate-400">
        <Spinner className="h-5 w-5" />
        Loading graph neighbors…
      </Card>
    );
  }

  if (notFound || !detail || !detail.entity) {
    return (
      <EmptyState
        icon={<IconGraph className="h-6 w-6" />}
        title="Entity not found"
        hint={`No graph record exists for "${selected}".`}
      />
    );
  }

  const incoming = detail.neighbors.filter((n) => n.direction === "in");
  const outgoing = detail.neighbors.filter((n) => n.direction === "out");

  return (
    <div className="space-y-5">
      <Card className="space-y-2 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-lg font-semibold text-slate-900 dark:text-white">
            {detail.entity.name}
          </h3>
          <Badge tone="brand">{detail.entity.type}</Badge>
          {typeof detail.entity.confidence === "number" && (
            <Badge tone="neutral">conf {detail.entity.confidence.toFixed(2)}</Badge>
          )}
        </div>
        <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-400">
          <span>
            normalized: <span className="font-mono">{detail.entity.normalized_name}</span>
          </span>
          {detail.entity.chunk_ids && (
            <span>
              {detail.entity.chunk_ids.length} chunk
              {detail.entity.chunk_ids.length === 1 ? "" : "s"}
            </span>
          )}
        </div>
        {detail.entity.evidence && detail.entity.evidence.length > 0 && (
          <EntityEvidenceList entries={detail.entity.evidence} />
        )}
      </Card>

      {detail.neighbors.length === 0 ? (
        <EmptyState
          icon={<IconGraph className="h-6 w-6" />}
          title="No relationships"
          hint="This entity has no recorded neighbors in the graph."
        />
      ) : (
        <div className="grid gap-5 md:grid-cols-2">
          <NeighborColumn
            title="Outgoing"
            arrow="→"
            neighbors={outgoing}
          />
          <NeighborColumn title="Incoming" arrow="←" neighbors={incoming} />
        </div>
      )}
    </div>
  );
}

function EntityEvidenceList({ entries }: { entries: EvidenceEntry[] }) {
  return (
    <div className="space-y-2 pt-1">
      <div className="text-xs uppercase tracking-wide text-slate-400">
        Evidence ({entries.length})
      </div>
      {entries.map((entry, i) => (
        <div
          key={i}
          className="rounded-md border border-slate-100 p-2 dark:border-slate-800"
        >
          {!entry.verified && (
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
              : "unverified"}
          </div>
        </div>
      ))}
    </div>
  );
}

function NeighborColumn({
  title,
  arrow,
  neighbors,
}: {
  title: string;
  arrow: string;
  neighbors: GraphNeighbor[];
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-slate-400">
        <span>{title}</span>
        <span className="text-slate-300 dark:text-slate-600">({neighbors.length})</span>
      </div>
      {neighbors.length === 0 ? (
        <Card className="p-3.5 text-sm text-slate-400 dark:text-slate-500">
          No {title.toLowerCase()} relationships.
        </Card>
      ) : (
        <Card className="divide-y divide-slate-100 dark:divide-slate-800">
          {neighbors.map((n, i) => (
            <div
              key={`${n.entity.normalized_name}-${n.relation_type}-${i}`}
              className="flex flex-wrap items-center gap-2 p-3.5"
            >
              <span className="text-slate-300 dark:text-slate-600">{arrow}</span>
              <Badge tone="amber">{n.relation_type}</Badge>
              <span className="truncate text-sm text-slate-700 dark:text-slate-200">
                {n.entity.name}
              </span>
              <Badge tone="neutral" className="ml-auto">
                {n.entity.type}
              </Badge>
            </div>
          ))}
        </Card>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Workflows mode                                                             */
/* -------------------------------------------------------------------------- */

function WorkflowsMode() {
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebounced(query, SEARCH_DEBOUNCE_MS);
  const [workflows, setWorkflows] = useState<WorkflowRef[] | null>(null);
  const [listLoading, setListLoading] = useState(false);

  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<GraphWorkflow | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const toast = useToast();

  useEffect(() => {
    let active = true;
    setListLoading(true);
    api
      .graphWorkflows(debouncedQuery.trim() || undefined)
      .then((res) => {
        if (active) setWorkflows(res.items);
      })
      .catch((e) => {
        if (active) toast.show(String(e), "red");
      })
      .finally(() => {
        if (active) setListLoading(false);
      });
    return () => {
      active = false;
    };
  }, [debouncedQuery]);

  async function selectWorkflow(name: string) {
    setSelected(name);
    setDetail(null);
    setNotFound(false);
    setDetailLoading(true);
    try {
      setDetail(await api.graphWorkflow(name));
    } catch (e) {
      if (isNotFound(e)) setNotFound(true);
      else toast.show(String(e), "red");
    } finally {
      setDetailLoading(false);
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[20rem_1fr]">
      <Card className="space-y-3 p-4">
        <div>
          <Label>Search workflows</Label>
          <Input
            className="mt-1.5"
            placeholder="e.g. onboarding, rollback"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        {listLoading && !workflows ? (
          <div className="space-y-2">
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : workflows && workflows.length > 0 ? (
          <div className="max-h-[60vh] space-y-1 overflow-y-auto pr-1">
            {workflows.map((workflow) => (
              <button
                key={workflow.name}
                type="button"
                onClick={() => selectWorkflow(workflow.name)}
                className={
                  "flex w-full items-center rounded-lg px-3 py-2 text-left text-sm transition-colors " +
                  (selected === workflow.name
                    ? "bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300"
                    : "text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800")
                }
              >
                <span className="truncate">{workflow.name}</span>
              </button>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<IconGraph className="h-6 w-6" />}
            title="No workflows found"
            hint={
              query.trim()
                ? "Try a different search term."
                : "This knowledge base has no extracted workflows yet."
            }
          />
        )}
      </Card>

      <WorkflowDetail
        selected={selected}
        detail={detail}
        loading={detailLoading}
        notFound={notFound}
      />
    </div>
  );
}

function WorkflowDetail({
  selected,
  detail,
  loading,
  notFound,
}: {
  selected: string | null;
  detail: GraphWorkflow | null;
  loading: boolean;
  notFound: boolean;
}) {
  if (!selected) {
    return (
      <EmptyState
        icon={<IconGraph className="h-6 w-6" />}
        title="Select a workflow"
        hint="Pick a workflow on the left to see its prerequisites and steps."
      />
    );
  }

  if (loading) {
    return (
      <Card className="flex items-center gap-3 p-6 text-sm text-slate-500 dark:text-slate-400">
        <Spinner className="h-5 w-5" />
        Loading workflow…
      </Card>
    );
  }

  if (notFound || !detail) {
    return (
      <EmptyState
        icon={<IconGraph className="h-6 w-6" />}
        title="Workflow not found"
        hint={`No workflow record exists for "${selected}".`}
      />
    );
  }

  return (
    <div className="space-y-5">
      <Card className="space-y-3 p-5">
        <h3 className="text-lg font-semibold text-slate-900 dark:text-white">
          {detail.workflow_name}
        </h3>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-slate-400">
            Prerequisites
          </span>
          {detail.prerequisites.length > 0 ? (
            detail.prerequisites.map((p) => (
              <Badge key={p} tone="brand">
                {p}
              </Badge>
            ))
          ) : (
            <Badge tone="neutral">none</Badge>
          )}
        </div>
      </Card>

      {detail.steps.length === 0 ? (
        <EmptyState
          icon={<IconGraph className="h-6 w-6" />}
          title="No steps"
          hint="This workflow has no recorded steps."
        />
      ) : (
        <Card className="divide-y divide-slate-100 dark:divide-slate-800">
          {detail.steps.map((step) => (
            <div key={`${step.order}-${step.chunk_id}`} className="flex gap-3 p-4">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-50 text-xs font-semibold text-brand-700 dark:bg-brand-500/15 dark:text-brand-300">
                {step.order}
              </span>
              <div className="min-w-0 space-y-1">
                <div className="text-sm text-slate-700 dark:text-slate-200">
                  {step.text}
                </div>
                {step.precondition && (
                  <div className="flex items-center gap-1.5 text-xs text-slate-400">
                    <Badge tone="amber">precondition</Badge>
                    <span>{step.precondition}</span>
                  </div>
                )}
              </div>
            </div>
          ))}
        </Card>
      )}
    </div>
  );
}
