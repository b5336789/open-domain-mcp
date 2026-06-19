import { ReactNode, useEffect, useState } from "react";
import { api, SourceInfo, Stats } from "../api";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  IconButton,
  Modal,
  PageHeader,
  Skeleton,
  useToast,
} from "../components/ui";
import {
  IconDashboard,
  IconDatabase,
  IconExplore,
  IconIngest,
  IconSparkle,
  IconTrash,
} from "../components/icons";

const STAGES = [
  { name: "load", hint: "files" },
  { name: "split", hint: "AST / text" },
  { name: "extract", hint: "Claude" },
  { name: "embed", hint: "vectors" },
  { name: "store", hint: "Chroma" },
  { name: "search", hint: "hybrid" },
];

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sources, setSources] = useState<SourceInfo[] | null>(null);
  const [pending, setPending] = useState<SourceInfo | null>(null);
  const [deleting, setDeleting] = useState(false);
  const toast = useToast();

  useEffect(() => {
    api
      .stats()
      .then(setStats)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    api
      .sources()
      .then((r) => setSources(r.sources))
      .catch(() => setSources([]));
  }, []);

  async function confirmDelete() {
    if (!pending) return;
    const target = pending.source;
    setDeleting(true);
    try {
      const { deleted } = await api.deleteSource(target);
      setSources((prev) =>
        prev ? prev.filter((s) => s.source !== target) : prev,
      );
      toast.show(`Removed ${target} (${deleted} chunks)`, "green");
      setPending(null);
    } catch (e) {
      toast.show(
        e instanceof Error ? e.message : `Failed to remove ${target}`,
        "red",
      );
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="space-y-8">
      <PageHeader
        title="Dashboard"
        subtitle="Status of the active knowledge base and the indexing pipeline."
        icon={<IconDashboard />}
      />

      <Card className="p-5">
        <h3 className="mb-4 flex items-center gap-2 text-sm font-medium text-slate-500 dark:text-slate-400">
          <IconSparkle className="h-4 w-4" /> Pipeline
        </h3>
        <div className="flex flex-wrap items-stretch gap-2">
          {STAGES.map((s, i) => (
            <div key={s.name} className="flex items-stretch gap-2">
              <div className="flex flex-col items-center justify-center rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-center dark:border-slate-700 dark:bg-slate-800/60">
                <span className="text-sm font-medium capitalize text-slate-700 dark:text-slate-200">
                  {s.name}
                </span>
                <span className="text-[11px] text-slate-400 dark:text-slate-500">
                  {s.hint}
                </span>
              </div>
              {i < STAGES.length - 1 && (
                <span className="self-center text-slate-300 dark:text-slate-600">
                  →
                </span>
              )}
            </div>
          ))}
        </div>
      </Card>

      {error && (
        <Card className="border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300">
          {error}
        </Card>
      )}

      {!stats && !error && (
        <section className="grid grid-cols-2 gap-4 md:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Card key={i} className="p-4">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="mt-3 h-5 w-28" />
            </Card>
          ))}
        </section>
      )}

      {stats && (
        <section className="grid grid-cols-2 gap-4 md:grid-cols-3">
          <Stat
            icon={<IconDatabase className="h-4 w-4" />}
            label="Indexed chunks"
            value={stats.count.toLocaleString()}
            accent
          />
          <Stat
            icon={<IconExplore className="h-4 w-4" />}
            label="Collection"
            value={stats.collection}
          />
          <Stat
            icon={<IconSparkle className="h-4 w-4" />}
            label="Embedder"
            value={stats.embedder}
          />
          <Stat label="Vector dim" value={stats.dim} />
          <Stat
            label="Extraction"
            value={
              <Badge tone={stats.extract_knowledge ? "green" : "neutral"}>
                {stats.extract_knowledge ? "on" : "off"}
              </Badge>
            }
          />
          <Stat
            icon={<IconIngest className="h-4 w-4" />}
            label="Data dir"
            value={stats.data_dir}
            mono
          />
        </section>
      )}

      <Card className="p-5">
        <h3 className="mb-4 flex items-center gap-2 text-sm font-medium text-slate-500 dark:text-slate-400">
          <IconDatabase className="h-4 w-4" /> Sources
          {sources && (
            <span className="text-slate-400 dark:text-slate-500">
              ({sources.length})
            </span>
          )}
        </h3>

        {!sources && (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <div
                key={i}
                className="flex items-center gap-3 rounded-lg border border-slate-100 px-3 py-2.5 dark:border-slate-800"
              >
                <Skeleton className="h-4 w-48" />
                <Skeleton className="ml-auto h-4 w-16" />
              </div>
            ))}
          </div>
        )}

        {sources && sources.length === 0 && (
          <EmptyState
            icon={<IconIngest className="h-6 w-6" />}
            title="No sources ingested yet"
            hint="Use the Ingest page to add files to this knowledge base."
          />
        )}

        {sources && sources.length > 0 && (
          <div className="divide-y divide-slate-100 dark:divide-slate-800">
            {sources.map((s) => (
              <SourceRow
                key={s.source}
                source={s}
                onDelete={() => setPending(s)}
                disabled={deleting}
              />
            ))}
          </div>
        )}
      </Card>

      {pending && (
        <Modal
          title="Delete source"
          onClose={() => !deleting && setPending(null)}
          footer={
            <>
              <Button
                variant="secondary"
                onClick={() => setPending(null)}
                disabled={deleting}
              >
                Cancel
              </Button>
              <Button
                variant="danger"
                onClick={confirmDelete}
                loading={deleting}
                disabled={deleting}
              >
                Delete
              </Button>
            </>
          }
        >
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Delete all {pending.chunks.toLocaleString()} chunks from{" "}
            <span className="break-all font-mono text-slate-900 dark:text-white">
              {pending.source}
            </span>
            ?
          </p>
        </Modal>
      )}
    </div>
  );
}

interface ReviewBucket {
  key: "approved" | "pending" | "rejected";
  count: number;
  tone: "green" | "amber" | "red";
}

function SourceRow({
  source,
  onDelete,
  disabled,
}: {
  source: SourceInfo;
  onDelete: () => void;
  disabled: boolean;
}) {
  const buckets: ReviewBucket[] = [
    { key: "approved", count: source.review.approved, tone: "green" },
    { key: "pending", count: source.review.pending, tone: "amber" },
    { key: "rejected", count: source.review.rejected, tone: "red" },
  ];
  const visibleBuckets = buckets.filter((b) => b.count > 0);

  return (
    <div className="flex items-center gap-3 py-2.5">
      <div className="min-w-0 flex-1">
        <div
          className="truncate font-mono text-sm text-slate-900 dark:text-slate-100"
          title={source.source}
        >
          {source.source}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          {source.kinds.map((kind) => (
            <Badge key={kind} tone="neutral">
              {kind}
            </Badge>
          ))}
          {visibleBuckets.map((b) => (
            <Badge key={b.key} tone={b.tone}>
              {b.key} {b.count}
            </Badge>
          ))}
        </div>
      </div>
      <div className="shrink-0 text-right text-sm tabular-nums text-slate-500 dark:text-slate-400">
        {source.chunks.toLocaleString()}
        <span className="ml-1 text-xs text-slate-400 dark:text-slate-500">
          chunks
        </span>
      </div>
      <IconButton
        onClick={onDelete}
        disabled={disabled}
        aria-label={`Delete ${source.source}`}
        className="hover:text-red-600 dark:hover:text-red-400"
      >
        <IconTrash className="h-4 w-4" />
      </IconButton>
    </div>
  );
}

function Stat({
  icon,
  label,
  value,
  accent = false,
  mono = false,
}: {
  icon?: ReactNode;
  label: string;
  value: ReactNode;
  accent?: boolean;
  mono?: boolean;
}) {
  return (
    <Card interactive className="p-4">
      <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-slate-400 dark:text-slate-500">
        {icon}
        {label}
      </div>
      <div
        className={`mt-1.5 break-words text-lg font-semibold ${
          accent
            ? "text-brand-600 dark:text-brand-400"
            : "text-slate-900 dark:text-white"
        } ${mono ? "font-mono text-sm" : ""}`}
      >
        {value}
      </div>
    </Card>
  );
}
