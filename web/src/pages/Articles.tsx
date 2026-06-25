import { useCallback, useEffect, useMemo, useState } from "react";
import { api, Article } from "../api";
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


export default function Articles() {
  const [articles, setArticles] = useState<Article[] | null>(null);
  const [selected, setSelected] = useState<Article | null>(null);
  const [q, setQ] = useState("");
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
    api
      .createTask("synthesize", {})
      .then(() =>
        toast.show("Synthesis queued in Task Center (top-right)", "green"),
      )
      .catch((e) => toast.show(String(e), "red"));
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
            <Button onClick={runSynthesize}>
              Synthesize now
            </Button>
          </div>
        }
      />

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

