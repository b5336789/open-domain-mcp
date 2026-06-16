import { useState } from "react";
import { api, SearchResult } from "../api";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  PageHeader,
  Select,
  Skeleton,
  useToast,
} from "../components/ui";
import { IconExplore } from "../components/icons";

export default function Explore() {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [language, setLanguage] = useState("");
  const [source, setSource] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searched, setSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const toast = useToast();

  async function run() {
    if (!query.trim()) return;
    setLoading(true);
    try {
      const res = await api.search(query, 8, {
        kind: kind || null,
        language: language || null,
        source_contains: source || null,
      });
      setResults(res);
      setSearched(true);
    } catch (e) {
      toast.show(String(e), "red");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Explore"
        subtitle="Hybrid semantic + keyword search over the indexed knowledge."
        icon={<IconExplore />}
      />

      <Card className="space-y-3 p-4">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            className="flex-1"
            placeholder="Search the indexed knowledge…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
          />
          <div className="flex gap-2">
            <Select
              className="w-28"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              <option value="">all</option>
              <option value="code">code</option>
              <option value="text">text</option>
            </Select>
            <Button onClick={run} loading={loading} className="px-6">
              Search
            </Button>
          </div>
        </div>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <Input
            className="h-9 sm:w-48"
            placeholder="language (e.g. python)"
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
          />
          <Input
            className="h-9 flex-1"
            placeholder="source contains…"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
          />
          <Badge tone="brand" className="self-start sm:self-center">
            dense + BM25 · RRF
          </Badge>
        </div>
      </Card>

      {loading && (
        <div className="space-y-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i} className="space-y-3 p-4">
              <Skeleton className="h-3 w-1/3" />
              <Skeleton className="h-3 w-2/3" />
              <Skeleton className="h-20 w-full" />
            </Card>
          ))}
        </div>
      )}

      {!loading && searched && results.length === 0 && (
        <EmptyState
          icon={<IconExplore className="h-6 w-6" />}
          title="No matches"
          hint="Try broader wording or clearing the language / source filters."
        />
      )}

      {!loading && (
        <div className="space-y-4">
          {results.map((r, i) => (
            <Card
              key={r.id}
              interactive
              className="animate-fade-in-up p-4"
            >
              <header className="flex items-center justify-between gap-3 text-sm">
                <span className="min-w-0 truncate font-mono text-slate-600 dark:text-slate-300">
                  {r.metadata.source}
                  {r.metadata.symbol ? `::${r.metadata.symbol}` : ""}
                </span>
                <Badge tone={i === 0 ? "green" : "neutral"}>
                  {r.score.toFixed(3)}
                </Badge>
              </header>
              {r.metadata.summary && (
                <p className="mt-2 text-sm text-slate-700 dark:text-slate-300">
                  {r.metadata.summary}
                </p>
              )}
              {r.metadata.concepts && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {r.metadata.concepts.split(",").map((c) => (
                    <Badge key={c} tone="neutral">
                      {c.trim()}
                    </Badge>
                  ))}
                </div>
              )}
              <pre className="scroll-thin mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 font-mono text-xs text-slate-700 dark:bg-slate-950/60 dark:text-slate-300">
                {r.text}
              </pre>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
