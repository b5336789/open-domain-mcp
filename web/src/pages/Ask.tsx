import { useState } from "react";
import { askStream, Citation } from "../api";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  PageHeader,
  Spinner,
  useToast,
} from "../components/ui";
import { IconAsk, IconCheck, IconCopy } from "../components/icons";

export default function Ask() {
  const [query, setQuery] = useState("");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const toast = useToast();

  function run() {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    setAnswer("");
    setCitations([]);
    askStream(
      query,
      6,
      (text) => setAnswer((prev) => prev + text),
      (cites) => setCitations(cites),
      (detail) => setError(detail),
      () => setLoading(false)
    );
  }

  async function copy() {
    if (!answer) return;
    try {
      await navigator.clipboard.writeText(answer);
      setCopied(true);
      toast.show("Answer copied", "green");
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.show("Could not copy", "red");
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Ask"
        subtitle="Retrieves the most relevant chunks and has Claude compose a cited answer."
        icon={<IconAsk />}
      />

      <Card className="p-4">
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            className="flex-1"
            placeholder="Ask a question about the indexed knowledge…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && run()}
          />
          <Button onClick={run} loading={loading} className="px-6">
            Ask
          </Button>
        </div>
      </Card>

      {loading && !answer && (
        <Card className="flex items-center gap-3 p-4 text-sm text-slate-500 dark:text-slate-400">
          <Spinner className="h-4 w-4 text-brand-500" />
          <span>Retrieving context and composing an answer…</span>
        </Card>
      )}

      {error && (
        <Card className="border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300">
          {error}
        </Card>
      )}

      {!loading && !error && !answer && (
        <EmptyState
          icon={<IconAsk className="h-6 w-6" />}
          title="Ask anything"
          hint="Answers are grounded in your indexed knowledge and include sources."
        />
      )}

      {answer && (
        <div className="space-y-4">
          <Card className="p-5">
            <div className="mb-3 flex items-center justify-between">
              <Badge tone="brand">Answer</Badge>
              <Button variant="ghost" size="sm" onClick={copy}>
                {copied ? (
                  <IconCheck className="h-4 w-4 text-emerald-500" />
                ) : (
                  <IconCopy className="h-4 w-4" />
                )}
                {copied ? "Copied" : "Copy"}
              </Button>
            </div>
            <div className="whitespace-pre-wrap leading-relaxed text-slate-800 dark:text-slate-200">
              {answer}
              {loading && <span className="animate-pulse">▍</span>}
            </div>
          </Card>

          {citations.length > 0 && (
            <Card className="p-5">
              <h3 className="mb-3 text-sm font-medium text-slate-500 dark:text-slate-400">
                Sources
              </h3>
              <ol className="space-y-2">
                {citations.map((c) => (
                  <li key={c.n} className="flex items-start gap-3 text-sm">
                    <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-brand-50 text-xs font-semibold text-brand-700 dark:bg-brand-500/15 dark:text-brand-300">
                      {c.n}
                    </span>
                    <span className="min-w-0 flex-1 break-words font-mono text-slate-700 dark:text-slate-300">
                      {c.source}
                      {c.symbol ? `::${c.symbol}` : ""}
                    </span>
                    <Badge tone="neutral">{c.score.toFixed(3)}</Badge>
                  </li>
                ))}
              </ol>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
