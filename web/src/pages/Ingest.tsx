import { useRef, useState } from "react";
import { api, ingestStream } from "../api";
import {
  Badge,
  Button,
  Card,
  Input,
  Label,
  PageHeader,
  useToast,
} from "../components/ui";
import { IconIngest, IconUpload } from "../components/icons";

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

export default function Ingest() {
  const [path, setPath] = useState("");
  const [sync, setSync] = useState(false);
  const [log, setLog] = useState<LogLine[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [running, setRunning] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const toast = useToast();

  function run(target: string) {
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
      () => setRunning(false),
      sync,
    );
  }

  async function runBackground(target: string) {
    try {
      await api.createTask("ingest", { path: target, sync });
      toast.show("Queued in Task Center (top-right) — you can leave this page", "green");
    } catch (e) {
      toast.show(String(e), "red");
    }
  }

  async function onUpload() {
    const files = fileInput.current?.files;
    if (!files || files.length === 0) return;
    const { path: staged } = await api.upload(files);
    run(staged);
  }

  function pick(files: FileList | null) {
    setPicked(files ? Array.from(files).map((f) => f.name) : []);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Ingest"
        subtitle="Index a server path or upload files. Progress streams live below."
        icon={<IconIngest />}
      />

      <Card className="space-y-3 p-5">
        <Label>Server path (file or directory)</Label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            className="flex-1 font-mono"
            placeholder="/path/to/code-or-docs"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && path && !running && run(path)}
          />
          <Button
            disabled={!path || running}
            loading={running}
            onClick={() => run(path)}
            className="px-6"
          >
            Ingest
          </Button>
          <Button
            variant="secondary"
            disabled={!path || running}
            onClick={() => runBackground(path)}
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
          Sync directory — prune chunks for deleted files
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
          className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors ${
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
            {picked.length > 0
              ? `${picked.length} file(s) selected`
              : "Drop files here or click to browse"}
          </span>
          {picked.length > 0 && (
            <span className="mt-1 max-w-full truncate text-xs text-slate-400">
              {picked.join(", ")}
            </span>
          )}
        </label>
        <div className="flex justify-end">
          <Button
            variant="secondary"
            disabled={running || picked.length === 0}
            onClick={onUpload}
          >
            <IconUpload className="h-4 w-4" />
            Upload &amp; ingest
          </Button>
        </div>
      </Card>

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
            {running && <div className="animate-pulse text-slate-500">…</div>}
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
                {report.skipped.map((s, i) => (
                  <li key={i} className="font-mono text-xs">
                    {s.path} — {s.reason}
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
                {report.errors.map((s, i) => (
                  <li key={i} className="font-mono text-xs">
                    {s.path} — {s.error}
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
