import { useRef, useState } from "react";
import { api, ingestStream } from "../api";

interface Report {
  files_indexed: number;
  chunks_indexed: number;
  chunks_pruned: number;
  skipped: { path: string; reason: string }[];
  errors: { path: string; error: string }[];
}

export default function Ingest() {
  const [path, setPath] = useState("");
  const [sync, setSync] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [running, setRunning] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  function run(target: string) {
    setLog([]);
    setReport(null);
    setRunning(true);
    ingestStream(
      target,
      (e) => {
        if (e.stage === "report") setReport(e as unknown as Report);
        else
          setLog((prev) => [
            ...prev,
            `[${e.stage}] ${e.path ?? ""} ${e.detail ?? ""}`.trim(),
          ]);
      },
      () => setRunning(false),
      sync
    );
  }

  async function onUpload() {
    const files = fileInput.current?.files;
    if (!files || files.length === 0) return;
    const { path: staged } = await api.upload(files);
    run(staged);
  }

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold">Ingest</h2>

      <div className="rounded-lg border bg-white p-4 space-y-3">
        <label className="block text-sm font-medium">Server path (file or directory)</label>
        <div className="flex gap-2">
          <input
            className="flex-1 rounded border px-3 py-2"
            placeholder="/path/to/code-or-docs"
            value={path}
            onChange={(e) => setPath(e.target.value)}
          />
          <button
            disabled={!path || running}
            onClick={() => run(path)}
            className="rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-40"
          >
            Ingest
          </button>
        </div>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input type="checkbox" checked={sync} onChange={(e) => setSync(e.target.checked)} />
          Sync directory (prune chunks for deleted files)
        </label>
      </div>

      <div className="rounded-lg border bg-white p-4 space-y-3">
        <label className="block text-sm font-medium">Upload files</label>
        <div className="flex gap-2">
          <input ref={fileInput} type="file" multiple className="flex-1" />
          <button
            disabled={running}
            onClick={onUpload}
            className="rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-40"
          >
            Upload &amp; ingest
          </button>
        </div>
      </div>

      {(running || log.length > 0) && (
        <div className="rounded-lg border bg-slate-900 p-4 text-xs text-green-300 font-mono h-56 overflow-auto">
          {log.map((line, i) => (
            <div key={i}>{line}</div>
          ))}
          {running && <div className="animate-pulse">…</div>}
        </div>
      )}

      {report && (
        <div className="rounded-lg border bg-white p-4">
          <p className="font-medium">
            Indexed {report.files_indexed} files / {report.chunks_indexed} chunks
          </p>
          {report.chunks_pruned > 0 && (
            <p className="text-slate-600 text-sm mt-1">
              Pruned {report.chunks_pruned} stale chunk(s)
            </p>
          )}
          {report.skipped.length > 0 && (
            <p className="text-amber-600 text-sm mt-1">
              Skipped {report.skipped.length} file(s)
            </p>
          )}
          {report.errors.length > 0 && (
            <p className="text-red-600 text-sm mt-1">
              {report.errors.length} error(s)
            </p>
          )}
        </div>
      )}
    </div>
  );
}
