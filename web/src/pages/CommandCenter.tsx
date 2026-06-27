import { ReactNode, useEffect, useState } from "react";
import { api, KnowledgeBaseReadiness } from "../api";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  PageHeader,
  Skeleton,
  useToast,
} from "../components/ui";
import {
  IconDashboard,
  IconDatabase,
  IconIngest,
  IconMetrics,
  IconReview,
  IconSparkle,
} from "../components/icons";

const STATUS_LABELS: Record<KnowledgeBaseReadiness["status"], string> = {
  blocked: "blocked",
  needs_review: "needs review",
  validating: "validating",
  ready: "ready",
  published: "published",
};

const STATUS_TONES: Record<
  KnowledgeBaseReadiness["status"],
  "red" | "amber" | "brand" | "green"
> = {
  blocked: "red",
  needs_review: "amber",
  validating: "brand",
  ready: "green",
  published: "green",
};

export default function CommandCenter() {
  const [data, setData] = useState<KnowledgeBaseReadiness | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const toast = useToast();

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(await api.workspaceReadiness());
    } catch (e) {
      const message = String(e);
      setError(message);
      toast.show(message, "red");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Command Center"
        subtitle="Lifecycle status, blockers, and next action for the active knowledge base."
        icon={<IconDashboard />}
        actions={
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void load()}
            loading={loading}
          >
            Refresh
          </Button>
        }
      />

      {error && (
        <Card className="border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300">
          {error}
        </Card>
      )}

      {!data && !error && <LoadingGrid />}

      {data && (
        <>
          <Card className="p-5">
            <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-lg font-semibold text-slate-900 dark:text-white">
                    {data.collection}
                  </h3>
                  <Badge tone={STATUS_TONES[data.status]}>
                    {STATUS_LABELS[data.status]}
                  </Badge>
                </div>
                <p className="mt-2 max-w-2xl text-sm text-slate-500 dark:text-slate-400">
                  {data.next_action}
                </p>
              </div>
              <div className="text-left md:text-right">
                <div className="text-xs font-medium uppercase tracking-wide text-slate-400">
                  Readiness
                </div>
                <div className="text-4xl font-semibold text-brand-600 dark:text-brand-400">
                  {data.score}
                </div>
              </div>
            </div>
          </Card>

          {(data.blockers.length > 0 || data.warnings.length > 0) && (
            <div className="grid gap-4 md:grid-cols-2">
              <IssueList title="Blockers" tone="red" items={data.blockers} />
              <IssueList title="Warnings" tone="amber" items={data.warnings} />
            </div>
          )}

          <section className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <Stat
              icon={<IconDatabase className="h-4 w-4" />}
              label="Sources"
              value={data.source_health.sources.toLocaleString()}
              detail={`${data.source_health.chunks.toLocaleString()} chunks`}
            />
            <Stat
              icon={<IconReview className="h-4 w-4" />}
              label="Approved"
              value={`${Math.round(data.review_health.approved_ratio * 100)}%`}
              detail={`${data.review_health.pending.toLocaleString()} pending`}
            />
            <Stat
              icon={<IconSparkle className="h-4 w-4" />}
              label="Jobs"
              value={`${data.job_health.running + data.job_health.queued} active`}
              detail={`${data.job_health.error} failed`}
            />
            <Stat
              icon={<IconMetrics className="h-4 w-4" />}
              label="Graph"
              value={data.graph_health.available ? "available" : "offline"}
              detail={`${data.graph_health.entities} entities, ${data.graph_health.workflows} workflows`}
            />
          </section>

          <Card className="p-5">
            <h3 className="text-sm font-medium text-slate-700 dark:text-slate-200">
              Next workflow step
            </h3>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                variant={data.status === "blocked" ? "primary" : "secondary"}
                onClick={() => {
                  window.location.hash = "#/intake";
                }}
              >
                <IconIngest className="h-4 w-4" />
                Source Intake
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  window.location.hash = "#/review";
                }}
              >
                <IconReview className="h-4 w-4" />
                Review Knowledge
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  window.location.hash = "#/metrics";
                }}
              >
                <IconMetrics className="h-4 w-4" />
                Quality Signals
              </Button>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function LoadingGrid() {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {Array.from({ length: 4 }).map((_, i) => (
        <Card key={i} className="p-4">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="mt-3 h-8 w-16" />
        </Card>
      ))}
    </div>
  );
}

function IssueList({
  title,
  tone,
  items,
}: {
  title: string;
  tone: "red" | "amber";
  items: string[];
}) {
  if (items.length === 0) {
    return (
      <EmptyState
        title={`No ${title.toLowerCase()}`}
        hint="No action required for this category."
      />
    );
  }
  return (
    <Card className="p-4">
      <div className="mb-2 flex items-center gap-2">
        <Badge tone={tone}>{title}</Badge>
      </div>
      <ul className="space-y-1 text-sm text-slate-600 dark:text-slate-300">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </Card>
  );
}

function Stat({
  icon,
  label,
  value,
  detail,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  detail: string;
}) {
  return (
    <Card interactive className="p-4">
      <div className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-slate-400 dark:text-slate-500">
        {icon}
        {label}
      </div>
      <div className="mt-1.5 break-words text-lg font-semibold text-slate-900 dark:text-white">
        {value}
      </div>
      <div className="mt-1 text-xs text-slate-400 dark:text-slate-500">
        {detail}
      </div>
    </Card>
  );
}
