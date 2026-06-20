import { ReactNode, useEffect, useState } from "react";
import { api, MetricsView } from "../api";
import {
  Button,
  Card,
  EmptyState,
  PageHeader,
  Skeleton,
  useToast,
} from "../components/ui";
import {
  IconDatabase,
  IconExplore,
  IconMetrics,
  IconSparkle,
} from "../components/icons";

function formatPercent(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

export default function Metrics() {
  const [metrics, setMetrics] = useState<MetricsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const toast = useToast();

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setMetrics(await api.metrics());
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

  const showSkeletons = loading && !metrics;

  return (
    <div className="space-y-8">
      <PageHeader
        title="Metrics"
        subtitle="What this knowledge base publishes and how well it grounds agents."
        icon={<IconMetrics />}
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

      <section className="space-y-3">
        <SectionHeader
          title="Product metrics"
          caption="Scale of the published knowledge base: how much is exposed to agents."
        />
        {showSkeletons ? (
          <SkeletonGrid count={3} />
        ) : (
          metrics && (
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
              <Stat
                icon={<IconExplore className="h-4 w-4" />}
                label="Published MCPs"
                value={metrics.product.published_mcps.toLocaleString()}
                accent
              />
              <Stat
                icon={<IconDatabase className="h-4 w-4" />}
                label="Knowledge objects"
                value={metrics.product.knowledge_objects.toLocaleString()}
              />
              <Stat
                icon={<IconSparkle className="h-4 w-4" />}
                label="Indexed sources"
                value={metrics.product.indexed_sources.toLocaleString()}
              />
            </div>
          )
        )}
      </section>

      <section className="space-y-3">
        <SectionHeader
          title="Agent metrics"
          caption="Retrieval quality observed across searches, asks and simulations."
        />
        {showSkeletons ? (
          <SkeletonGrid count={5} />
        ) : (
          metrics &&
          (metrics.agent.total_events === 0 ? (
            <EmptyState
              icon={<IconMetrics className="h-6 w-6" />}
              title="No agent activity yet"
              hint="These metrics populate after searches, asks and simulations are run against this knowledge base."
            />
          ) : (
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
              <Stat
                label="Total events"
                value={metrics.agent.total_events.toLocaleString()}
                accent
              />
              <Stat
                label="Grounding hit rate"
                value={formatPercent(metrics.agent.grounding_hit_rate)}
              />
              <Stat
                label="Avg hits"
                value={metrics.agent.avg_hits.toFixed(2)}
              />
              <Stat
                label="Avg score"
                value={metrics.agent.avg_score.toFixed(3)}
              />
              <Stat
                label="Retrieval precision"
                value={formatPercent(metrics.agent.retrieval_precision)}
              />
            </div>
          ))
        )}
      </section>
    </div>
  );
}

function SectionHeader({ title, caption }: { title: string; caption: string }) {
  return (
    <div>
      <h3 className="text-sm font-medium text-slate-700 dark:text-slate-200">
        {title}
      </h3>
      <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-500">
        {caption}
      </p>
    </div>
  );
}

function SkeletonGrid({ count }: { count: number }) {
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
      {Array.from({ length: count }).map((_, i) => (
        <Card key={i} className="p-4">
          <Skeleton className="h-3 w-20" />
          <Skeleton className="mt-3 h-5 w-28" />
        </Card>
      ))}
    </div>
  );
}

function Stat({
  icon,
  label,
  value,
  accent = false,
}: {
  icon?: ReactNode;
  label: string;
  value: ReactNode;
  accent?: boolean;
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
        }`}
      >
        {value}
      </div>
    </Card>
  );
}
