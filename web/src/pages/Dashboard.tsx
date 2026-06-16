import { ReactNode, useEffect, useState } from "react";
import { api, Stats } from "../api";
import { Badge, Card, PageHeader, Skeleton } from "../components/ui";
import {
  IconDashboard,
  IconDatabase,
  IconExplore,
  IconIngest,
  IconSparkle,
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

  useEffect(() => {
    api
      .stats()
      .then(setStats)
      .catch((e) => setError(String(e)));
  }, []);

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
