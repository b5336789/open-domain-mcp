import { useCallback, useEffect, useMemo, useState } from "react";
import { api, Article, synthesizeStream } from "../api";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  PageHeader,
  Skeleton,
  useToast,
} from "../components/ui";
import { IconArticles } from "../components/icons";

interface SynthLine {
  stage: string;
  text: string;
}

interface SynthReport {
  topics_gated: number;
  articles_written: number;
  stored: number;
  removed: number;
  rejected: unknown[];
  errors: unknown[];
}

const SYNTH_TONE: Record<string, string> = {
  start: "text-slate-400",
  topic: "text-sky-300",
  stored: "text-emerald-300",
  rejected: "text-amber-300",
  removed: "text-orange-300",
  topic_error: "text-red-400",
  error: "text-red-400",
};

export default function Articles() {
  const [articles, setArticles] = useState<Article[] | null>(null);
  const [selected, setSelected] = useState<Article | null>(null);
  const [q, setQ] = useState("");
  const [running, setRunning] = useState(false);
  const [log, setLog] = useState<SynthLine[]>([]);
  const [report, setReport] = useState<SynthReport | null>(null);
  const toast = useToast();

  const loadArticles = useCallback(() => {
    return api
      .articles()
      .then((rows) => {
        const sorted = [...rows].sort(
          (a, b) => b.business_relevance - a.business_relevance,
        );
        setArticles(sorted);
        setSelected((prev) =>
          prev && sorted.some((a) => a.id === prev.id) ? prev : sorted[0] ?? null,
        );
      })
      .catch((e) => {
        toast.show(String(e), "red");
        setArticles([]);
      });
  }, [toast]);

  useEffect(() => {
    loadArticles();
  }, [loadArticles]);

  function runSynthesize() {
    setLog([]);
    setReport(null);
    setRunning(true);
    synthesizeStream(
      (e) => {
        const stage = String(e.stage ?? "");
        if (stage === "report") {
          setReport(e as unknown as SynthReport);
        } else {
          const label =
            (e.topic as string) ??
            (e.detail as string) ??
            (stage === "start" ? `${e.total ?? 0} topics` : "");
          setLog((prev) => [...prev, { stage, text: String(label) }]);
        }
      },
      () => {
        setRunning(false);
        loadArticles().then(() =>
          toast.show("Synthesis finished — articles refreshed", "green"),
        );
      },
    );
  }

  const filtered = useMemo(() => {
    if (!articles) return [];
    const needle = q.trim().toLowerCase();
    if (!needle) return articles;
    return articles.filter((a) =>
      `${a.title} ${a.topic} ${a.body}`.toLowerCase().includes(needle),
    );
  }, [articles, q]);

  const active =
    selected && filtered.some((a) => a.id === selected.id)
      ? selected
      : filtered[0] ?? null;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Articles"
        subtitle="Synthesized business-meaning articles from your knowledge base."
        icon={<IconArticles />}
        actions={
          <div className="flex items-center gap-2">
            <Input
              className="w-56"
              placeholder="Filter articles…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <Button onClick={runSynthesize} loading={running}>
              Synthesize now
            </Button>
          </div>
        }
      />

      {(running || log.length > 0) && (
        <Card className="overflow-hidden p-0">
          <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2 dark:border-slate-800">
            <span className="text-xs font-medium uppercase tracking-wide text-slate-400">
              Synthesis log
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
                  className={`shrink-0 ${SYNTH_TONE[line.stage] ?? "text-slate-400"}`}
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
        <Card className="animate-fade-in-up p-5">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Metric label="Topics" value={report.topics_gated} />
            <Metric label="Stored" value={report.stored} accent />
            <Metric label="Removed" value={report.removed} />
            <Metric
              label="Rejected"
              value={report.rejected.length + report.errors.length}
            />
          </div>
        </Card>
      )}

      {!articles && (
        <Card className="space-y-2 p-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-4 w-2/3" />
          ))}
        </Card>
      )}

      {articles && articles.length === 0 && (
        <EmptyState
          icon={<IconArticles className="h-6 w-6" />}
          title="No articles yet"
          hint="Run `synthesize` to generate business-meaning articles."
        />
      )}

      {articles && articles.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-[20rem_1fr]">
          <Card className="divide-y divide-slate-100 dark:divide-slate-800">
            {filtered.map((a) => (
              <button
                key={a.id}
                onClick={() => setSelected(a)}
                aria-current={active?.id === a.id ? "true" : undefined}
                className={
                  "block w-full px-3.5 py-3 text-left transition hover:bg-slate-50 dark:hover:bg-slate-800/50" +
                  (active?.id === a.id
                    ? " bg-slate-50 dark:bg-slate-800/50"
                    : "")
                }
              >
                <div className="font-medium text-slate-800 dark:text-slate-100">
                  {a.title}
                </div>
                <div className="mt-0.5 text-xs text-slate-500">{a.topic}</div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  <Badge tone="brand">
                    relevance {a.business_relevance.toFixed(2)}
                  </Badge>
                  {a.cross_validated && <Badge tone="green">cross-validated</Badge>}
                </div>
              </button>
            ))}
            {filtered.length === 0 && (
              <div className="px-3.5 py-6 text-center text-sm text-slate-500">
                No matches.
              </div>
            )}
          </Card>

          {active && (
            <Card className="space-y-4 p-5">
              <div>
                <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-50">
                  {active.title}
                </h2>
                <div className="mt-1 text-sm text-slate-500">{active.topic}</div>
              </div>
              <div className="whitespace-pre-wrap leading-relaxed text-slate-800 dark:text-slate-200">
                {active.body}
              </div>
              {active.sources.length > 0 && (
                <div className="border-t border-slate-100 pt-3 dark:border-slate-800">
                  <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                    Sources
                  </div>
                  <ul className="space-y-1 font-mono text-xs text-slate-600 dark:text-slate-400">
                    {active.sources.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ul>
                </div>
              )}
            </Card>
          )}
        </div>
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
