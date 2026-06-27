import { ReactNode, useEffect, useState } from "react";
import { api, QualityEvidence, QualityEvidenceResponse, ReadinessStatus } from "../api";
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
  IconAdvisor,
  IconArticles,
  IconGraph,
  IconIngest,
  IconMetrics,
  IconReview,
  IconSimulator,
} from "../components/icons";

const STATUS_LABELS: Record<ReadinessStatus, string> = {
  blocked: "blocked",
  needs_review: "needs review",
  validating: "validating",
  ready: "ready",
  published: "published",
};

const STATUS_TONES: Record<
  ReadinessStatus,
  "red" | "amber" | "brand" | "green"
> = {
  blocked: "red",
  needs_review: "amber",
  validating: "brand",
  ready: "green",
  published: "green",
};

export default function QualityLab() {
  const [data, setData] = useState<QualityEvidenceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const toast = useToast();

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(await api.qualityEvidence());
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
        title="Quality Lab"
        subtitle="Evidence, gates, and validation signals for the active knowledge base."
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
                  Evidence score
                </div>
                <div className="text-4xl font-semibold text-brand-600 dark:text-brand-400">
                  {data.score}
                </div>
              </div>
            </div>
          </Card>

          {data.evidence.length === 0 ? (
            <EmptyState
              icon={<IconMetrics className="h-6 w-6" />}
              title="No quality evidence"
              hint="Run validation workflows to generate evidence."
            />
          ) : (
            <section className="grid gap-4 md:grid-cols-2">
              {data.evidence.map((card) => (
                <EvidenceCard key={card.id} evidence={card} />
              ))}
            </section>
          )}

          <section className="flex flex-wrap gap-2">
            <ButtonLink href="#/intake" icon={<IconIngest className="h-4 w-4" />}>
              Source Intake
            </ButtonLink>
            <ButtonLink href="#/review" icon={<IconReview className="h-4 w-4" />}>
              Review Knowledge
            </ButtonLink>
            <ButtonLink href="#/articles" icon={<IconArticles className="h-4 w-4" />}>
              Curate Articles
            </ButtonLink>
            <ButtonLink href="#/graph" icon={<IconGraph className="h-4 w-4" />}>
              Inspect Graph
            </ButtonLink>
            <ButtonLink href="#/advisor" icon={<IconAdvisor className="h-4 w-4" />}>
              Run Advisor
            </ButtonLink>
            <ButtonLink href="#/simulator" icon={<IconSimulator className="h-4 w-4" />}>
              Run Simulator
            </ButtonLink>
            <ButtonLink href="#/metrics" icon={<IconMetrics className="h-4 w-4" />}>
              Detailed Metrics
            </ButtonLink>
          </section>
        </>
      )}
    </div>
  );
}

function EvidenceCard({ evidence }: { evidence: QualityEvidence }) {
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-slate-900 dark:text-white">
            {evidence.gate}
          </h3>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            {evidence.summary}
          </p>
        </div>
        <div className="text-right">
          <Badge tone={STATUS_TONES[evidence.status]}>
            {STATUS_LABELS[evidence.status]}
          </Badge>
          <div className="mt-2 text-xl font-semibold text-slate-900 dark:text-white">
            {evidence.score}
          </div>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-1.5">
        {evidence.details.map((detail) => (
          <Badge key={detail} tone="neutral">
            {detail}
          </Badge>
        ))}
      </div>
      <div className="mt-4 text-sm font-medium text-slate-700 dark:text-slate-200">
        {evidence.action}
      </div>
    </Card>
  );
}

function ButtonLink({
  href,
  icon,
  children,
}: {
  href: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <a
      href={href}
      className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:border-slate-300 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200 dark:hover:bg-slate-700/70"
    >
      {icon}
      {children}
    </a>
  );
}

function LoadingGrid() {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <Card key={i} className="p-4">
          <Skeleton className="h-4 w-24" />
          <Skeleton className="mt-3 h-4 w-2/3" />
          <Skeleton className="mt-4 h-8 w-full" />
        </Card>
      ))}
    </div>
  );
}
