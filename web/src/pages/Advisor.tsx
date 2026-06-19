import { useState } from "react";
import { AdviseResult, api, GraphWorkflow, SearchResult } from "../api";
import {
  Badge,
  Button,
  Card,
  Label,
  PageHeader,
  Skeleton,
  Textarea,
  useToast,
} from "../components/ui";
import { IconAdvisor } from "../components/icons";

// The five facets the advisor returns, rendered in this fixed order.
const FACETS: { key: keyof AdviseResult; label: string }[] = [
  { key: "workflow", label: "Workflow" },
  { key: "risks", label: "Risks" },
  { key: "permissions", label: "Permissions" },
  { key: "dependencies", label: "Dependencies" },
  { key: "constraints", label: "Constraints" },
];

export default function Advisor() {
  const [action, setAction] = useState("");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AdviseResult | null>(null);
  const toast = useToast();

  async function advise() {
    const trimmed = action.trim();
    if (!trimmed) return;
    setRunning(true);
    setResult(null);
    try {
      setResult(await api.advise(trimmed));
    } catch (e) {
      toast.show(String(e), "red");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Pre-Execution Advisor"
        subtitle="Before doing something, get the workflow, risks, permissions, dependencies, and constraints to know first."
        icon={<IconAdvisor />}
      />

      <Card className="space-y-3 p-5">
        <div>
          <Label>What are you about to do?</Label>
          <Textarea
            className="mt-1.5 h-20"
            placeholder="e.g. Roll out a new billing webhook to production"
            value={action}
            onChange={(e) => setAction(e.target.value)}
          />
        </div>
        <div className="flex justify-end">
          <Button onClick={advise} loading={running} disabled={!action.trim()}>
            Advise
          </Button>
        </div>
      </Card>

      {running && <AdvisorSkeleton />}

      {result && !running && (
        <>
          <SummaryStrip summary={result.summary} />

          {FACETS.map(({ key, label }) => (
            <FacetSection
              key={key}
              label={label}
              results={result[key] as SearchResult[]}
              graph={key === "workflow" ? result.graph_workflow : null}
            />
          ))}
        </>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */

function SummaryStrip({
  summary,
}: {
  summary: AdviseResult["summary"];
}) {
  return (
    <Card className="flex flex-wrap items-center gap-x-6 gap-y-3 p-4">
      {FACETS.map(({ key, label }) => (
        <Stat key={key} label={label} value={String(summary.counts[key] ?? 0)} />
      ))}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs uppercase tracking-wide text-slate-400">
          Types
        </span>
        {summary.knowledge_types.length ? (
          summary.knowledge_types.map((t) => (
            <Badge key={t} tone="brand">
              {t}
            </Badge>
          ))
        ) : (
          <Badge tone="amber">none</Badge>
        )}
      </div>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-slate-400">{label}</div>
      <div className="text-lg font-semibold text-slate-900 dark:text-white">
        {value}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */

function FacetSection({
  label,
  results,
  graph,
}: {
  label: string;
  results: SearchResult[];
  graph: GraphWorkflow | null;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-slate-700 dark:text-slate-200">
          {label}
        </h3>
        <span className="text-xs text-slate-400">
          {results.length} result{results.length === 1 ? "" : "s"}
        </span>
      </div>

      {graph && <WorkflowGraph graph={graph} />}

      {results.length > 0 ? (
        <Card className="divide-y divide-slate-100 dark:divide-slate-800">
          {results.map((r) => (
            <ResultRow key={r.id} result={r} />
          ))}
        </Card>
      ) : (
        !graph && (
          <p className="text-sm text-slate-400 dark:text-slate-500">none found</p>
        )
      )}
    </div>
  );
}

function ResultRow({ result }: { result: SearchResult }) {
  return (
    <div className="p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        {result.metadata.knowledge_type && (
          <Badge tone="brand">{result.metadata.knowledge_type}</Badge>
        )}
        <span className="truncate font-mono text-xs text-slate-500 dark:text-slate-400">
          {result.metadata.source}
          {result.metadata.symbol ? `::${result.metadata.symbol}` : ""}
        </span>
        <span className="ml-auto text-xs text-slate-400">
          {result.score.toFixed(3)}
        </span>
      </div>
      <div className="mt-1 text-sm text-slate-600 dark:text-slate-300">
        {result.text.slice(0, 200)}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */

function WorkflowGraph({ graph }: { graph: GraphWorkflow }) {
  return (
    <Card className="space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs uppercase tracking-wide text-slate-400">
          Graph workflow
        </span>
        <code className="rounded bg-slate-100 px-2 py-0.5 font-mono text-xs text-brand-700 dark:bg-slate-800 dark:text-brand-300">
          {graph.workflow_name}
        </code>
      </div>

      {graph.prerequisites.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-slate-400">
            Prerequisites
          </span>
          {graph.prerequisites.map((p) => (
            <Badge key={p} tone="amber">
              {p}
            </Badge>
          ))}
        </div>
      )}

      {graph.steps.length > 0 && (
        <ol className="space-y-2">
          {graph.steps.map((step) => (
            <li key={step.chunk_id} className="flex gap-3">
              <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-50 text-xs font-semibold text-brand-600 dark:bg-brand-500/15 dark:text-brand-300">
                {step.order}
              </span>
              <div className="min-w-0">
                <div className="text-sm text-slate-700 dark:text-slate-200">
                  {step.text}
                </div>
                {step.precondition && (
                  <div className="mt-0.5 text-xs text-slate-400 dark:text-slate-500">
                    Precondition: {step.precondition}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

/* -------------------------------------------------------------------------- */

function AdvisorSkeleton() {
  return (
    <div className="space-y-5">
      <Skeleton className="h-16 w-full" />
      {FACETS.map(({ key }) => (
        <div key={key} className="space-y-2">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-20 w-full" />
        </div>
      ))}
    </div>
  );
}
