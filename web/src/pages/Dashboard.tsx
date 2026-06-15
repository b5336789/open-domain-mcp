import { useEffect, useState } from "react";
import { api, Stats } from "../api";

const STAGES = ["load", "split", "extract", "embed", "store", "search"];

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.stats().then(setStats).catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold">Dashboard</h2>

      <section>
        <h3 className="text-sm font-medium text-slate-500 mb-2">Pipeline</h3>
        <div className="flex items-center gap-2 flex-wrap">
          {STAGES.map((s, i) => (
            <div key={s} className="flex items-center gap-2">
              <span className="rounded-full bg-slate-200 px-3 py-1 text-sm capitalize">
                {s}
              </span>
              {i < STAGES.length - 1 && <span className="text-slate-400">→</span>}
            </div>
          ))}
        </div>
      </section>

      {error && <p className="text-red-600">{error}</p>}

      {stats && (
        <section className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <Card label="Indexed chunks" value={stats.count} />
          <Card label="Collection" value={stats.collection} />
          <Card label="Embedder" value={stats.embedder} />
          <Card label="Vector dim" value={stats.dim} />
          <Card label="Extraction" value={stats.extract_knowledge ? "on" : "off"} />
          <Card label="Data dir" value={stats.data_dir} />
        </section>
      )}
    </div>
  );
}

function Card({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border bg-white p-4 shadow-sm">
      <div className="text-xs uppercase tracking-wide text-slate-400">{label}</div>
      <div className="mt-1 text-lg font-semibold break-words">{value}</div>
    </div>
  );
}
