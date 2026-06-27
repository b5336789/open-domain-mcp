import { useEffect, useRef, useState } from "react";
import { api, ingestStream, SourceInfo } from "../api";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  IconButton,
  Input,
  Label,
  Modal,
  PageHeader,
  Skeleton,
  useToast,
} from "../components/ui";
import { IconIngest, IconTrash, IconUpload } from "../components/icons";

interface Report {
  files_indexed: number;
  chunks_indexed: number;
  chunks_pruned: number;
  skipped: { path: string; reason: string }[];
  errors: { path: string; error: string }[];
}

interface LogLine {
  stage: string;
  text: string;
}

const STAGE_TONE: Record<string, string> = {
  load: "text-sky-300",
  split: "text-violet-300",
  extract: "text-amber-300",
  embed: "text-emerald-300",
  store: "text-brand-300",
  prune: "text-orange-300",
  skip: "text-slate-400",
  error: "text-red-400",
};

export default function SourceIntake() {
  const [path, setPath] = useState("");
  const [sync, setSync] = useState(false);
  const [log, setLog] = useState<LogLine[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [running, setRunning] = useState(false);
  const [queueing, setQueueing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [sources, setSources] = useState<SourceInfo[] | null>(null);
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<SourceInfo | null>(null);
  const [deleting, setDeleting] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const toast = useToast();

  async function loadSources() {
    setSourcesError(null);
    try {
      const data = await api.sources();
      setSources(data.sources);
    } catch (e) {
      setSources([]);
      setSourcesError(String(e));
    }
  }

  useEffect(() => {
    void loadSources();
  }, []);

  function run(rawTarget: string) {
    const target = rawTarget.trim();
    if (!target) return;
    setLog([]);
    setReport(null);
    setRunning(true);
    ingestStream(
      target,
      (e) => {
        const stage = String(e.stage ?? "");
        if (stage === "report") setReport(e as unknown as Report);
        else
          setLog((prev) => [
            ...prev,
            { stage, text: `${e.path ?? ""} ${e.detail ?? ""}`.trim() },
          ]);
      },
      () => {
        setRunning(false);
        void loadSources();
      },
      sync,
    );
  }

  async function runBackground(rawTarget: string) {
    const target = rawTarget.trim();
    if (!target) return;
    setQueueing(true);
    try {
      await api.createTask("ingest", { path: target, sync });
      toast.show(
        "Queued in Task Center (top-right). You can leave this page.",
        "green",
      );
    } catch (e) {
      toast.show(String(e), "red");
    } finally {
      setQueueing(false);
    }
  }

  async function onUpload() {
    const files = fileInput.current?.files;
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      const { path: staged } = await api.upload(files);
      run(staged);
    } catch (e) {
      toast.show(String(e), "red");
    } finally {
      setUploading(false);
    }
  }

  function pick(files: FileList | null) {
    setPicked(files ? Array.from(files).map((f) => f.name) : []);
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    const target = pendingDelete.source;
    setDeleting(true);
    try {
      const { deleted } = await api.deleteSource(target);
      setSources((prev) =>
        prev ? prev.filter((source) => source.source !== target) : prev,
      );
      toast.show(`Removed ${target} (${deleted.toLocaleString()} chunks)`, "green");
      setPendingDelete(null);
    } catch (e) {
      toast.show(
        e instanceof Error ? e.message : `Failed to remove ${target}`,
        "red",
      );
    } finally {
      setDeleting(false);
    }
  }

  const canRunPath = Boolean(path.trim()) && !running && !queueing;
  const hasPickedFiles = picked.length > 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Source Intake"
        subtitle="Add server paths, upload files, and manage indexed sources for the active knowledge base."
        icon={<IconIngest />}
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(20rem,0.8fr)]">
        <Card className="space-y-3 p-5">
          <Label>Server path</Label>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              className="min-w-0 flex-1 font-mono"
              placeholder="/path/to/code-or-docs"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && canRunPath) run(path);
              }}
            />
            <Button
              disabled={!canRunPath}
              loading={running}
              onClick={() => run(path)}
              className="px-6"
            >
              Ingest
            </Button>
            <Button
              variant="secondary"
              disabled={!canRunPath}
              loading={queueing}
              onClick={() => void runBackground(path)}
              title="Queue as a background task in the Task Center."
            >
              Run in background
            </Button>
          </div>
          <label className="flex w-fit cursor-pointer items-center gap-2 text-sm text-slate-600 dark:text-slate-400">
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500 dark:border-slate-600 dark:bg-slate-800"
              checked={sync}
              onChange={(e) => setSync(e.target.checked)}
            />
            Sync directory - prune chunks for deleted files
          </label>
        </Card>

        <Card className="space-y-3 p-5">
          <Label>Upload files</Label>
          <label
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              if (fileInput.current && e.dataTransfer.files.length) {
                fileInput.current.files = e.dataTransfer.files;
                pick(e.dataTransfer.files);
              }
            }}
            className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-7 text-center transition-colors ${
              dragging
                ? "border-brand-400 bg-brand-50 dark:bg-brand-500/10"
                : "border-slate-300 hover:border-slate-400 dark:border-slate-700 dark:hover:border-slate-600"
            }`}
          >
            <input
              ref={fileInput}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => pick(e.target.files)}
            />
            <IconUpload className="mb-2 h-6 w-6 text-slate-400" />
            <span className="text-sm text-slate-600 dark:text-slate-300">
              {hasPickedFiles
                ? `${picked.length} file(s) selected`
                : "Drop files here or click to browse"}
            </span>
            {hasPickedFiles && (
              <span className="mt-1 max-w-full truncate text-xs text-slate-400">
                {picked.join(", ")}
              </span>
            )}
          </label>
          <div className="flex justify-end">
            <Button
              variant="secondary"
              disabled={running || uploading || !hasPickedFiles}
              loading={uploading}
              onClick={() => void onUpload()}
            >
              <IconUpload className="h-4 w-4" />
              Upload &amp; ingest
            </Button>
          </div>
        </Card>
      </div>

      {(running || log.length > 0) && (
        <Card className="overflow-hidden p-0">
          <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2 dark:border-slate-800">
            <span className="text-xs font-medium uppercase tracking-wide text-slate-400">
              Live log
            </span>
            {running && (
              <span className="flex items-center gap-1.5 text-xs text-emerald-500">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-500" />
                running
              </span>
            )}
          </div>
          <div className="scroll-thin h-56 overflow-auto bg-slate-950 p-4 font-mono text-xs">
            {log.map((line, i) => (
              <div key={i} className="flex gap-2">
                <span
                  className={`shrink-0 ${STAGE_TONE[line.stage] ?? "text-slate-400"}`}
                >
                  [{line.stage}]
                </span>
                <span className="text-slate-300">{line.text}</span>
              </div>
            ))}
            {running && <div className="animate-pulse text-slate-500">...</div>}
          </div>
        </Card>
      )}

      {report && (
        <Card className="animate-fade-in-up space-y-4 p-5">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Metric label="Files" value={report.files_indexed} accent />
            <Metric label="Chunks" value={report.chunks_indexed} accent />
            <Metric label="Pruned" value={report.chunks_pruned} />
            <Metric
              label="Issues"
              value={report.skipped.length + report.errors.length}
            />
          </div>

          {report.skipped.length > 0 && (
            <details className="text-sm">
              <summary className="cursor-pointer font-medium text-amber-600 dark:text-amber-400">
                {report.skipped.length} skipped file(s)
              </summary>
              <ul className="mt-2 space-y-1 text-slate-500 dark:text-slate-400">
                {report.skipped.map((skipped, i) => (
                  <li key={i} className="font-mono text-xs">
                    {skipped.path} - {skipped.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {report.errors.length > 0 && (
            <details className="text-sm" open>
              <summary className="cursor-pointer font-medium text-red-600 dark:text-red-400">
                {report.errors.length} error(s)
              </summary>
              <ul className="mt-2 space-y-1 text-red-500/90">
                {report.errors.map((error, i) => (
                  <li key={i} className="font-mono text-xs">
                    {error.path} - {error.error}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {report.skipped.length === 0 && report.errors.length === 0 && (
            <Badge tone="green">Completed with no issues</Badge>
          )}
        </Card>
      )}

      <section aria-label="Source registry">
        <Card className="p-5">
          <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="flex items-center gap-2 text-sm font-medium text-slate-700 dark:text-slate-200">
                <IconIngest className="h-4 w-4 text-slate-400" />
                Source registry
                {sources && (
                  <span className="text-slate-400 dark:text-slate-500">
                    ({sources.length})
                  </span>
                )}
              </h3>
              <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                Indexed source paths and their review state.
              </p>
            </div>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void loadSources()}
              disabled={sources === null}
            >
              Refresh
            </Button>
          </div>

          {sourcesError && (
            <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300">
              {sourcesError}
            </div>
          )}

          {!sources && <SourceSkeleton />}

          {sources && sources.length === 0 && (
            <EmptyState
              icon={<IconIngest className="h-6 w-6" />}
              title="No sources indexed yet"
              hint="Run a server path ingest or upload files to populate this registry."
            />
          )}

          {sources && sources.length > 0 && (
            <div className="divide-y divide-slate-100 dark:divide-slate-800">
              {sources.map((source) => (
                <SourceRow
                  key={source.source}
                  source={source}
                  disabled={deleting}
                  onDelete={() => setPendingDelete(source)}
                />
              ))}
            </div>
          )}
        </Card>
      </section>

      {pendingDelete && (
        <Modal
          title="Delete source"
          onClose={() => !deleting && setPendingDelete(null)}
          footer={
            <>
              <Button
                variant="secondary"
                onClick={() => setPendingDelete(null)}
                disabled={deleting}
              >
                Cancel
              </Button>
              <Button
                variant="danger"
                onClick={() => void confirmDelete()}
                loading={deleting}
                disabled={deleting}
              >
                Delete
              </Button>
            </>
          }
        >
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Delete all {pendingDelete.chunks.toLocaleString()} chunks from{" "}
            <span className="break-all font-mono text-slate-900 dark:text-white">
              {pendingDelete.source}
            </span>
            ?
          </p>
        </Modal>
      )}
    </div>
  );
}

function SourceSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 3 }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-3 rounded-lg border border-slate-100 px-3 py-3 dark:border-slate-800"
        >
          <div className="min-w-0 flex-1">
            <Skeleton className="h-4 w-48 max-w-full" />
            <Skeleton className="mt-2 h-3 w-32 max-w-full" />
          </div>
          <Skeleton className="h-8 w-20" />
        </div>
      ))}
    </div>
  );
}

function SourceRow({
  source,
  disabled,
  onDelete,
}: {
  source: SourceInfo;
  disabled: boolean;
  onDelete: () => void;
}) {
  const reviewBadges: {
    key: keyof SourceInfo["review"];
    count: number;
    tone: "green" | "amber" | "red" | "neutral";
  }[] = [
    { key: "approved", count: source.review.approved, tone: "green" },
    { key: "pending", count: source.review.pending, tone: "amber" },
    { key: "rejected", count: source.review.rejected, tone: "red" },
    { key: "unset", count: source.review.unset, tone: "neutral" },
  ];

  return (
    <div className="flex min-w-0 items-center gap-3 py-3">
      <div className="min-w-0 flex-1">
        <div
          className="truncate font-mono text-sm font-medium text-slate-900 dark:text-slate-100"
          title={source.source}
        >
          {source.source}
        </div>
        <div className="mt-1.5 flex min-w-0 flex-wrap items-center gap-1.5">
          {source.kinds.map((kind) => (
            <Badge key={kind} tone="neutral" className="max-w-full truncate">
              {kind}
            </Badge>
          ))}
          {reviewBadges
            .filter((badge) => badge.count > 0)
            .map((badge) => (
              <Badge key={badge.key} tone={badge.tone}>
                {badge.key} {badge.count.toLocaleString()}
              </Badge>
            ))}
        </div>
      </div>
      <div className="hidden shrink-0 text-right text-sm tabular-nums text-slate-500 dark:text-slate-400 sm:block">
        {source.chunks.toLocaleString()}
        <span className="ml-1 text-xs text-slate-400 dark:text-slate-500">
          chunks
        </span>
      </div>
      <IconButton
        onClick={onDelete}
        disabled={disabled}
        aria-label={`Delete ${source.source}`}
        title={`Delete ${source.source}`}
        className="shrink-0 hover:text-red-600 dark:hover:text-red-400"
      >
        <IconTrash className="h-4 w-4" />
      </IconButton>
    </div>
  );
}

function Metric({
  label,
  value,
  accent = false,
}: {
  label: string;
  value: number;
  accent?: boolean;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 dark:border-slate-800 dark:bg-slate-800/50">
      <div className="text-xs uppercase tracking-wide text-slate-400">{label}</div>
      <div
        className={`mt-0.5 text-xl font-semibold ${
          accent
            ? "text-brand-600 dark:text-brand-400"
            : "text-slate-900 dark:text-white"
        }`}
      >
        {value.toLocaleString()}
      </div>
    </div>
  );
}
